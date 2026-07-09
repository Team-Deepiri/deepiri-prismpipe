"""Tests for document.vectorize runtime integration."""

from __future__ import annotations

from typing import Any

import pytest

from prismpipe.document import (
    DOCUMENT_VECTORIZE_CAPABILITY,
    DOCUMENT_VECTORIZE_INPUT_KEY,
    DocumentVectorizeInput,
    VectorizeBackendResult,
    VectorizedChunk,
    execute_document_vectorize,
)
from prismpipe.document.vectorize import DocumentVectorizeValidationError
from prismpipe.engine import PrismEngine


class DeterministicVectorizer:
    provider = "test-provider"
    model = "deterministic-v1"

    def __init__(self) -> None:
        self.calls: list[DocumentVectorizeInput] = []

    def vectorize(self, request: DocumentVectorizeInput) -> VectorizeBackendResult:
        self.calls.append(request)
        chunks = []
        for index, chunk in enumerate(request.chunks):
            text = chunk.text
            assert text is not None
            chunks.append(
                VectorizedChunk(
                    chunk_id=chunk.chunk_id,
                    text=text,
                    vector=[float(index + 1), float(len(text))],
                    metadata={"sourceChunk": chunk.metadata},
                )
            )
        return VectorizeBackendResult(
            chunks=chunks,
            dimensions=2,
            metadata={"backend": "deterministic"},
        )


class FailingVectorizer:
    provider = "broken-provider"
    model = "broken-model"

    def __init__(self) -> None:
        self.calls = 0

    def vectorize(self, request: DocumentVectorizeInput) -> VectorizeBackendResult:
        self.calls += 1
        raise RuntimeError("embedding backend unavailable")


class InvalidDimensionsVectorizer:
    provider = "invalid-dimensions-provider"
    model = "invalid-dimensions-model"

    def __init__(self, dimensions: Any) -> None:
        self.dimensions = dimensions

    def vectorize(self, request: DocumentVectorizeInput) -> VectorizeBackendResult:
        return VectorizeBackendResult(
            chunks=[
                VectorizedChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text or "",
                    vector=[float(index + 1), float(len(chunk.text or ""))],
                )
                for index, chunk in enumerate(request.chunks)
            ],
            dimensions=self.dimensions,
        )


class SwappedChunkVectorizer:
    provider = "swapped-chunk-provider"
    model = "swapped-chunk-model"

    def vectorize(self, request: DocumentVectorizeInput) -> VectorizeBackendResult:
        return VectorizeBackendResult(
            chunks=[
                VectorizedChunk(
                    chunk_id=request.chunks[1].chunk_id,
                    text=request.chunks[0].text,
                    vector=[1.0, 10.0],
                ),
                VectorizedChunk(
                    chunk_id=request.chunks[0].chunk_id,
                    text=request.chunks[1].text,
                    vector=[2.0, 9.0],
                ),
            ],
            dimensions=2,
        )


class TrackingPrismEngine(PrismEngine):
    def __init__(self) -> None:
        super().__init__()
        self.child_calls: list[dict[str, Any]] = []

    async def execute_child_organism(self, parent, capability, **kwargs):
        self.child_calls.append({"parent": parent, "capability": capability, "kwargs": kwargs})
        return await super().execute_child_organism(parent, capability, **kwargs)


def canonical_payload() -> dict[str, Any]:
    return {
        "routeId": "route-001",
        "documentId": "doc-001",
        "manifestVersion": "1.0",
        "destination": "vectorize",
        "qualityScore": 0.95,
        "correlationId": "corr-001",
        "embeddingModel": "text-embedding-3-small",
        "document": {
            "documentId": "doc-001",
            "title": "Test Document",
            "mimeType": "text/plain",
        },
        "chunks": [
            {
                "chunkId": "chunk-001",
                "index": 0,
                "text": "The mitochondria is the powerhouse of the cell.",
            },
        ],
        "storageReferences": [],
        "options": {"dimensions": None, "normalize": False, "metadata": {}},
    }


@pytest.mark.asyncio
async def test_valid_payload_routes_through_child_document_vectorize_capability():
    engine = TrackingPrismEngine()
    vectorizer = DeterministicVectorizer()

    result = await execute_document_vectorize(
        engine,
        canonical_payload(),
        vectorizer,
        parent_state={"api_request_state": "received"},
        parent_metadata={
            "source": "api",
            "trace_id": "trace_123",
            "tags": {"route": "documents"},
        },
        parent_input={"path": "/documents/vectorize"},
    )

    assert result.success is True
    assert len(engine.child_calls) == 1
    assert engine.child_calls[0]["capability"] == DOCUMENT_VECTORIZE_CAPABILITY
    assert vectorizer.calls[0].route_id == "route-001"
    assert vectorizer.calls[0].document_id == "doc-001"
    assert vectorizer.calls[0].document.document_id == "doc-001"

    assert result.child.envelope.parent_id == result.parent.id
    assert result.child.state["api_request_state"] == "received"
    assert result.child.metadata.trace_id == "trace_123"
    assert result.child.metadata.tags == {"route": "documents"}
    result.child.metadata.tags["route"] = "mutated"
    assert result.parent.metadata.tags == {"route": "documents"}
    assert [entry.capability for entry in result.child.history] == [DOCUMENT_VECTORIZE_CAPABILITY]

    output = result.output
    assert output is not None
    assert output["documentId"] == "doc-001"
    assert output["provider"] == "test-provider"
    assert output["model"] == "deterministic-v1"
    assert output["dimensions"] == 2
    assert output["metadata"]["routeId"] == "route-001"
    assert output["metadata"]["manifestVersion"] == "1.0"
    assert output["metadata"]["correlationId"] == "corr-001"
    assert output["metadata"]["embeddingModel"] == "text-embedding-3-small"
    assert output["metadata"]["backend"] == {"backend": "deterministic"}
    assert output["chunks"] == [
        {
            "chunkId": "chunk-001",
            "text": "The mitochondria is the powerhouse of the cell.",
            "vector": [1.0, 47.0],
            "metadata": {"sourceChunk": {}},
        },
    ]


@pytest.mark.asyncio
async def test_parent_input_cannot_override_document_vectorize_payload():
    engine = TrackingPrismEngine()
    vectorizer = DeterministicVectorizer()
    payload = canonical_payload()

    result = await execute_document_vectorize(
        engine,
        payload,
        vectorizer,
        parent_input={
            "path": "/documents/vectorize",
            DOCUMENT_VECTORIZE_INPUT_KEY: {"documentId": "doc_shadow", "chunks": []},
            "event": "other.event",
        },
    )

    assert result.success is True
    assert vectorizer.calls[0].document_id == "doc-001"
    assert result.parent.input["path"] == "/documents/vectorize"
    assert result.parent.input["event"] == DOCUMENT_VECTORIZE_CAPABILITY
    assert result.parent.input[DOCUMENT_VECTORIZE_INPUT_KEY] == payload


@pytest.mark.asyncio
async def test_invalid_payload_fails_cleanly_without_backend_call():
    engine = TrackingPrismEngine()
    vectorizer = DeterministicVectorizer()
    payload = canonical_payload()
    payload["chunks"] = []

    result = await execute_document_vectorize(
        engine,
        payload,
        vectorizer,
        parent_state={"document_vectorize": {"documentId": "stale_doc"}},
    )

    assert len(engine.child_calls) == 1
    assert vectorizer.calls == []
    assert result.success is False
    assert result.child.terminated is True
    assert result.output is None
    assert "document_vectorize" not in result.child.state

    error = result.error
    assert error is not None
    assert error["code"] == "VALIDATION_ERROR"
    assert error["message"] == "chunks must be a non-empty list"
    assert error["provider"] == "test-provider"
    assert error["model"] == "deterministic-v1"


def test_missing_chunk_text_raises_document_vectorize_validation_error():
    payload = canonical_payload()
    payload["chunks"][0].pop("text")

    with pytest.raises(DocumentVectorizeValidationError, match=r"chunks\[0\]\.text"):
        DocumentVectorizeInput.from_payload(payload)


@pytest.mark.asyncio
async def test_bool_dimensions_are_rejected_without_backend_call():
    engine = TrackingPrismEngine()
    vectorizer = DeterministicVectorizer()
    payload = canonical_payload()
    payload["options"]["dimensions"] = True

    result = await execute_document_vectorize(engine, payload, vectorizer)

    assert vectorizer.calls == []
    assert result.success is False
    assert result.error is not None
    assert result.error["code"] == "VALIDATION_ERROR"
    assert result.error["message"] == "options.dimensions must be a positive integer"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend_dimensions", [True, 2.0])
async def test_invalid_backend_dimensions_are_rejected(backend_dimensions):
    engine = PrismEngine()
    vectorizer = InvalidDimensionsVectorizer(backend_dimensions)
    payload = canonical_payload()
    payload["options"].pop("dimensions")

    result = await execute_document_vectorize(engine, payload, vectorizer)

    assert result.success is False
    assert result.output is None
    assert result.error is not None
    assert result.error["code"] == "VECTORIZER_ERROR"
    assert result.error["details"]["error"] == "Vectorizer returned invalid vector dimensions"


@pytest.mark.asyncio
async def test_swapped_backend_chunk_ids_are_rejected():
    engine = PrismEngine()
    vectorizer = SwappedChunkVectorizer()
    payload = canonical_payload()
    payload["chunks"].append(
        {
            "chunkId": "chunk-002",
            "index": 1,
            "text": "Second chunk text.",
        }
    )

    result = await execute_document_vectorize(engine, payload, vectorizer)

    assert result.success is False
    assert result.output is None
    assert result.error is not None
    assert result.error["code"] == "VECTORIZER_ERROR"
    assert result.error["details"]["error"] == "Vectorizer returned mismatched chunk id for chunks[0]"


@pytest.mark.asyncio
async def test_backend_failure_terminates_child_and_records_context():
    engine = TrackingPrismEngine()
    vectorizer = FailingVectorizer()

    result = await execute_document_vectorize(engine, canonical_payload(), vectorizer)

    assert vectorizer.calls == 1
    assert result.success is False
    assert result.child.terminated is True
    assert result.output is None
    assert "Vectorizer backend failed" in result.child.state["_error"]

    error = result.error
    assert error is not None
    assert error["code"] == "VECTORIZER_ERROR"
    assert error["message"] == "Vectorizer backend failed"
    assert error["documentId"] == "doc-001"
    assert error["provider"] == "broken-provider"
    assert error["model"] == "broken-model"
    assert error["details"] == {
        "error": "embedding backend unavailable",
        "errorType": "RuntimeError",
    }


@pytest.mark.asyncio
async def test_output_shape_includes_required_vectorization_fields():
    engine = PrismEngine()
    vectorizer = DeterministicVectorizer()

    result = await execute_document_vectorize(engine, canonical_payload(), vectorizer)

    output = result.output
    assert output is not None
    assert set(output) == {
        "documentId",
        "chunks",
        "dimensions",
        "provider",
        "model",
        "metadata",
    }
    assert set(output["chunks"][0]) == {"chunkId", "text", "vector", "metadata"}
    assert isinstance(output["chunks"][0]["vector"], list)
    assert output["metadata"]["correlationId"] == "corr-001"
    assert output["metadata"]["manifestVersion"] == "1.0"
    assert output["dimensions"] == len(output["chunks"][0]["vector"])
