"""Vectorizer backend that calls Cyrex's document indexing API.

Per the platform data-pipeline audit (deepiri-platform#317), Milvus -- owned
by Cyrex -- is the canonical vector store; nothing else in the architecture
persists embeddings locally. This is the concrete Vectorizer implementation
that closes the loop for document.vectorize: it calls Cyrex's
POST /api/v1/documents/index/chunks (which preserves the caller's chunk_id
instead of re-splitting, and returns the raw vector for each chunk so the
DocumentVectorizeNode contract -- which requires real, non-empty vectors --
is satisfied) and persists the chunks into Milvus in the same call.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable
from typing import Any

import httpx

from prismpipe.document.vectorize import (
    DocumentVectorizeInput,
    VectorizeBackendResult,
    VectorizedChunk,
)


class CyrexVectorizerError(RuntimeError):
    """Raised when the Cyrex indexing call fails or returns an invalid result."""


class CyrexVectorizer:
    """Vectorizer protocol implementation backed by Cyrex + Milvus."""

    provider = "cyrex"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_seconds: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = (
            base_url or os.environ.get("CYREX_BASE_URL", "http://cyrex:8000")
        ).rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client = client
        self._owns_client = client is None
        self.model = "sentence-transformers/all-MiniLM-L6-v2"

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def vectorize(
        self, request: DocumentVectorizeInput
    ) -> Awaitable[VectorizeBackendResult]:
        return self._vectorize(request)

    async def _vectorize(
        self, request: DocumentVectorizeInput
    ) -> VectorizeBackendResult:
        chunks_payload = []
        for chunk in request.chunks:
            if not chunk.text:
                raise CyrexVectorizerError(
                    f"chunk {chunk.chunk_id!r} has no text; cannot vectorize"
                )
            chunks_payload.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "text": chunk.text,
                    "metadata": dict(chunk.metadata),
                }
            )

        body: dict[str, Any] = {
            "document_id": request.document_id,
            "chunks": chunks_payload,
            "doc_type": request.document_type or "legal_document",
            "industry": request.metadata.get("industry", "generic"),
            "metadata": dict(request.metadata),
        }

        client = await self._get_client()
        try:
            response = await client.post(
                f"{self._base_url}/api/v1/documents/index/chunks", json=body
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise CyrexVectorizerError(f"Cyrex indexing request failed: {exc}") from exc

        data = response.json()
        if not data.get("success"):
            raise CyrexVectorizerError(f"Cyrex indexing reported failure: {data}")

        by_chunk_id = {c["chunk_id"]: c for c in data.get("chunks", [])}
        vectorized_chunks: list[VectorizedChunk] = []
        for chunk in request.chunks:
            result = by_chunk_id.get(chunk.chunk_id)
            if result is None:
                raise CyrexVectorizerError(
                    f"Cyrex response missing vector for chunk {chunk.chunk_id!r}"
                )
            vectorized_chunks.append(
                VectorizedChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    vector=list(result["vector"]),
                    metadata={"milvusDocumentId": request.document_id},
                )
            )

        return VectorizeBackendResult(
            chunks=vectorized_chunks,
            dimensions=data.get("dimensions"),
            metadata={"backend": "cyrex-milvus", "cyrexDocumentId": data.get("document_id")},
        )
