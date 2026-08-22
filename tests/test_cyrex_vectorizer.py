"""Tests for CyrexVectorizer -- the document.vectorize backend that calls
Cyrex's POST /api/v1/documents/index/chunks and persists into Milvus."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from prismpipe.document import CyrexVectorizer, CyrexVectorizerError
from prismpipe.document.vectorize import ChunkReference, DocumentVectorizeInput


def make_request(chunk_texts: list[str]) -> DocumentVectorizeInput:
    payload = {
        "routeId": "route-001",
        "documentId": "doc-001",
        "manifestVersion": "1.0",
        "documentType": "lease",
        "destination": "vectorize",
        "qualityScore": 0.95,
        "document": {
            "documentId": "doc-001",
            "title": "Test Document",
            "mimeType": "text/plain",
        },
        "chunks": [
            {"chunkId": f"chunk-{i:03d}", "index": i, "text": text}
            for i, text in enumerate(chunk_texts)
        ],
        "storageReferences": [],
        "options": {"dimensions": None, "normalize": False, "metadata": {}},
    }
    return DocumentVectorizeInput.from_payload(payload)


def cyrex_success_response(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    chunks = [
        {"chunk_id": c["chunk_id"], "vector": [0.1, 0.2, 0.3], "dimensions": 3}
        for c in body["chunks"]
    ]
    return httpx.Response(
        200,
        json={
            "success": True,
            "document_id": body["document_id"],
            "provider": "cyrex",
            "model": "sentence-transformers/all-MiniLM-L6-v2",
            "dimensions": 3,
            "chunks": chunks,
        },
    )


def make_vectorizer(handler) -> CyrexVectorizer:
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://cyrex-test")
    return CyrexVectorizer(base_url="http://cyrex-test", client=client)


@pytest.mark.asyncio
async def test_vectorize_calls_index_chunks_and_maps_result():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return cyrex_success_response(request)

    vectorizer = make_vectorizer(handler)
    request = make_request(["Tenant shall pay rent.", "Lease term is 5 years."])

    result = await vectorizer.vectorize(request)

    assert captured["url"].endswith("/api/v1/documents/index/chunks")
    assert captured["body"]["document_id"] == "doc-001"
    assert [c["chunk_id"] for c in captured["body"]["chunks"]] == [
        "chunk-000",
        "chunk-001",
    ]

    assert result.dimensions == 3
    assert len(result.chunks) == 2
    assert result.chunks[0].chunk_id == "chunk-000"
    assert result.chunks[0].vector == [0.1, 0.2, 0.3]
    assert result.chunks[1].chunk_id == "chunk-001"
    assert result.metadata["backend"] == "cyrex-milvus"

    await vectorizer.aclose()


@pytest.mark.asyncio
async def test_vectorize_preserves_chunk_order_when_cyrex_reorders():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        # Cyrex returns chunks in reverse order; the vectorizer must still
        # map results back onto request.chunks by chunk_id, not by position.
        reversed_chunks = list(reversed(body["chunks"]))
        return httpx.Response(
            200,
            json={
                "success": True,
                "document_id": body["document_id"],
                "dimensions": 3,
                "chunks": [
                    {"chunk_id": c["chunk_id"], "vector": [1.0, 2.0, 3.0]}
                    for c in reversed_chunks
                ],
            },
        )

    vectorizer = make_vectorizer(handler)
    request = make_request(["first", "second", "third"])

    result = await vectorizer.vectorize(request)

    assert [c.chunk_id for c in result.chunks] == [
        "chunk-000",
        "chunk-001",
        "chunk-002",
    ]
    await vectorizer.aclose()


@pytest.mark.asyncio
async def test_vectorize_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "Milvus unavailable"})

    vectorizer = make_vectorizer(handler)
    request = make_request(["some text"])

    with pytest.raises(CyrexVectorizerError, match="Cyrex indexing request failed"):
        await vectorizer.vectorize(request)
    await vectorizer.aclose()


@pytest.mark.asyncio
async def test_vectorize_raises_when_cyrex_reports_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "detail": "bad input"})

    vectorizer = make_vectorizer(handler)
    request = make_request(["some text"])

    with pytest.raises(CyrexVectorizerError, match="reported failure"):
        await vectorizer.vectorize(request)
    await vectorizer.aclose()


@pytest.mark.asyncio
async def test_vectorize_raises_when_chunk_missing_from_response():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"success": True, "document_id": "doc-001", "dimensions": 3, "chunks": []},
        )

    vectorizer = make_vectorizer(handler)
    request = make_request(["some text"])

    with pytest.raises(CyrexVectorizerError, match="missing vector"):
        await vectorizer.vectorize(request)
    await vectorizer.aclose()


@pytest.mark.asyncio
async def test_vectorize_rejects_empty_chunk_text():
    vectorizer = make_vectorizer(lambda request: cyrex_success_response(request))
    request = make_request(["ok"])
    request.chunks[0] = ChunkReference(
        chunk_id="chunk-000", index=0, text=None, document_id="doc-001"
    )

    with pytest.raises(CyrexVectorizerError, match="has no text"):
        await vectorizer.vectorize(request)
    await vectorizer.aclose()


def test_default_base_url_from_env(monkeypatch):
    monkeypatch.setenv("CYREX_BASE_URL", "http://cyrex.internal:9000/")
    vectorizer = CyrexVectorizer()
    assert vectorizer._base_url == "http://cyrex.internal:9000"


def test_default_provider_and_model():
    vectorizer = CyrexVectorizer(base_url="http://x")
    assert vectorizer.provider == "cyrex"
    assert vectorizer.model == "sentence-transformers/all-MiniLM-L6-v2"
