"""Integration-style tests for durable document processing and messaging."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from prismpipe.deepiri_bus import (
    DeepiriMessage,
    DeepiriStreamTopics,
    InMemoryDeepiriTransport,
)
from prismpipe.document import (
    DocumentOperationStatus,
    DocumentVectorizeConsumer,
    DocumentVectorizeInput,
    DocumentVectorizeProcessor,
    PublicationState,
    SQLiteDocumentOperationStore,
    VectorizeBackendResult,
    VectorizedChunk,
    build_document_idempotency_key,
)
from prismpipe.document.vectorize import DocumentVectorizeValidationError
from prismpipe.exceptions import StorageError
from prismpipe.storage import MemoryStorage, StorageBackend


def route_payload(identifier: str = "001") -> dict[str, Any]:
    return {
        "routeId": f"route-{identifier}",
        "documentId": f"doc-{identifier}",
        "manifestVersion": "1",
        "documentType": "text",
        "schemaId": "document.route.v1",
        "schemaVersion": "1",
        "provenance": {"producer": "test"},
        "artifactRequests": [],
        "destination": "vectorize",
        "qualityScore": 0.9,
        "correlationId": f"corr-{identifier}",
        "document": {
            "documentId": f"doc-{identifier}",
            "mimeType": "text/plain",
        },
        "chunks": [
            {
                "chunkId": f"chunk-{identifier}",
                "documentId": f"doc-{identifier}",
                "index": 0,
                "text": f"content-{identifier}",
            }
        ],
        "storageReferences": [],
        "options": {"normalize": False, "metadata": {}},
    }


class CountingVectorizer:
    provider = "test-provider"
    model = "test-model"

    def __init__(
        self,
        *,
        release: threading.Event | None = None,
        started: threading.Event | None = None,
        fail: bool = False,
    ) -> None:
        self.calls = 0
        self.requests: list[DocumentVectorizeInput] = []
        self.release = release
        self.started = started
        self.fail = fail
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def vectorize(self, request: DocumentVectorizeInput) -> VectorizeBackendResult:
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.requests.append(request)
            if self.started is not None:
                self.started.set()
        try:
            if self.release is not None:
                self.release.wait()
            if self.fail:
                raise RuntimeError("temporary backend failure")
            return VectorizeBackendResult(
                chunks=[
                    VectorizedChunk(
                        chunk_id=chunk.chunk_id,
                        text=chunk.text or "",
                        vector=[float(chunk.index + 1), float(len(chunk.text or ""))],
                    )
                    for chunk in request.chunks
                ],
                dimensions=2,
            )
        finally:
            with self.lock:
                self.active -= 1


class AsyncVectorizer:
    provider = "async-provider"
    model = "async-model"

    def __init__(self) -> None:
        self.calls = 0

    async def vectorize(
        self,
        request: DocumentVectorizeInput,
    ) -> VectorizeBackendResult:
        self.calls += 1
        await asyncio.sleep(0)
        return VectorizeBackendResult(
            chunks=[
                VectorizedChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text or "",
                    vector=[1.0, 2.0],
                )
                for chunk in request.chunks
            ],
            dimensions=2,
        )


class FailingStorage(StorageBackend[Any]):
    async def save(self, key: str, value: Any) -> None:
        return None

    async def load(self, key: str) -> Any:
        raise StorageError("load", "temporary outage")

    async def delete(self, key: str) -> None:
        return None

    async def exists(self, key: str) -> bool:
        return False

    async def list_keys(self, prefix: str = "") -> list[str]:
        return []


class AckTrackingTransport(InMemoryDeepiriTransport):
    def __init__(self, expected_acks: int) -> None:
        super().__init__()
        self.expected_acks = expected_acks
        self.all_acknowledged = asyncio.Event()

    async def acknowledge(self, message: DeepiriMessage) -> None:
        await super().acknowledge(message)
        if len(self.acknowledged) >= self.expected_acks:
            self.all_acknowledged.set()


def operation_store(tmp_path) -> SQLiteDocumentOperationStore:
    return SQLiteDocumentOperationStore(tmp_path / "document-operations.sqlite3")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda payload: payload["document"].update({"documentId": "other"}),
            "document.documentId must match documentId",
        ),
        (
            lambda payload: payload["chunks"][0].update({"documentId": "other"}),
            r"chunks\[0\].documentId must match documentId",
        ),
        (
            lambda payload: payload["chunks"].append(
                {
                    "chunkId": payload["chunks"][0]["chunkId"],
                    "index": 1,
                    "text": "duplicate",
                }
            ),
            r"chunks\[1\].chunkId must be unique",
        ),
        (
            lambda payload: payload.update({"qualityScore": 1.1}),
            "qualityScore must be between 0 and 1 inclusive",
        ),
        (
            lambda payload: payload["chunks"][0].update(
                {"text": None, "storage": {"provider": "file"}}
            ),
            r"chunks\[0\].storage must include uri or key",
        ),
        (
            lambda payload: payload["chunks"].append(
                {
                    "chunkId": "different-chunk",
                    "index": 0,
                    "text": "duplicate index",
                }
            ),
            r"chunks\[1\].index must be unique",
        ),
        (
            lambda payload: payload["chunks"][0].update({"index": 1}),
            r"chunks\[0\].index must equal its zero-based position",
        ),
        (
            lambda payload: payload.update(
                {
                    "artifactRequests": [
                        {"artifactType": "summary", "required": True}
                    ]
                }
            ),
            "is not supported when required",
        ),
        (
            lambda payload: payload.update(
                {
                    "artifactRequests": [
                        {"artifactType": "embedding", "required": "yes"}
                    ]
                }
            ),
            r"artifactRequests\[0\].required must be a boolean",
        ),
        (
            lambda payload: payload.update(
                {
                    "artifactRequests": [
                        {
                            "artifactType": "embedding",
                            "parameters": {"dimensions": 3},
                        }
                    ],
                    "options": {"dimensions": 2},
                }
            ),
            r"artifactRequests\[0\].parameters.dimensions must match options.dimensions",
        ),
    ],
)
def test_cross_field_validation_is_deterministic(mutation, message):
    payload = route_payload()
    mutation(payload)

    with pytest.raises(DocumentVectorizeValidationError, match=message):
        DocumentVectorizeInput.from_payload(payload)


@pytest.mark.asyncio
async def test_storage_backed_chunk_and_artifact_status_are_persisted(tmp_path):
    payload = route_payload()
    payload["chunks"][0].pop("text")
    payload["chunks"][0]["storage"] = {"uri": "chunk://001"}
    payload["artifactRequests"] = [
        {"artifactType": "embedding", "required": True},
        {"artifactType": "summary", "required": False},
    ]
    storage = MemoryStorage()
    await storage.save("chunk://001", {"text": "loaded content"})
    vectorizer = CountingVectorizer()
    database = tmp_path / "document-operations.sqlite3"
    processor = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=SQLiteDocumentOperationStore(database),
        chunk_storage=storage,
    )

    result = await processor.process(payload, message_id="message-1")

    assert result.success is True
    assert vectorizer.requests[0].chunks[0].chunk_id == "chunk-001"
    assert vectorizer.requests[0].chunks[0].document_id == "doc-001"
    assert vectorizer.requests[0].chunks[0].text == "loaded content"
    assert result.record.result is not None
    assert result.record.result["artifacts"] == [
        {
            "artifactType": "embedding",
            "status": "fulfilled",
            "required": True,
            "details": {
                "dimensions": 2,
                "provider": "test-provider",
                "model": "test-model",
            },
        },
        {
            "artifactType": "summary",
            "status": "unsupported",
            "required": False,
            "details": {"reason": "No artifact producer is configured"},
        },
    ]

    reconstructed = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=SQLiteDocumentOperationStore(database),
        chunk_storage=storage,
    )
    duplicate = await reconstructed.process(payload, message_id="message-2")
    assert duplicate.duplicate is True
    assert duplicate.record.result["artifacts"] == result.record.result["artifacts"]
    assert vectorizer.calls == 1


@pytest.mark.asyncio
async def test_missing_and_temporary_storage_failures_are_classified(tmp_path):
    payload = route_payload()
    payload["chunks"][0].pop("text")
    payload["chunks"][0]["storage"] = {"uri": "chunk://missing"}

    missing_database = tmp_path / "missing" / "document-operations.sqlite3"
    missing_vectorizer = CountingVectorizer()
    missing = DocumentVectorizeProcessor(
        missing_vectorizer,
        operation_store=SQLiteDocumentOperationStore(missing_database),
        chunk_storage=MemoryStorage(),
    )
    missing_result = await missing.process(payload, message_id="missing")
    recovered_missing = await DocumentVectorizeProcessor(
        missing_vectorizer,
        operation_store=SQLiteDocumentOperationStore(missing_database),
        chunk_storage=MemoryStorage(),
    ).process(payload, message_id="missing-redelivery")
    assert missing_result.record.status is DocumentOperationStatus.TERMINAL_FAILURE
    assert recovered_missing.duplicate is True
    assert recovered_missing.record.error == missing_result.record.error
    assert missing_result.record.error["code"] == "STORAGE_CONTENT_NOT_FOUND"
    assert missing_vectorizer.calls == 0

    temporary = DocumentVectorizeProcessor(
        CountingVectorizer(),
        operation_store=operation_store(tmp_path / "temporary"),
        chunk_storage=FailingStorage(),
    )
    temporary_result = await temporary.process(payload, message_id="temporary")
    assert temporary_result.record.status is DocumentOperationStatus.RETRYABLE_FAILURE
    assert temporary_result.record.error["code"] == "STORAGE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_duplicate_conflict_and_reconstruction_are_durable(tmp_path):
    database = tmp_path / "operations.sqlite3"
    vectorizer = CountingVectorizer()
    first_processor = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=SQLiteDocumentOperationStore(database),
    )
    payload = route_payload()

    first = await first_processor.process(payload, message_id="delivery-1")
    duplicate = await first_processor.process(payload, message_id="delivery-2")
    reconstructed = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=SQLiteDocumentOperationStore(database),
    )
    recovered = await reconstructed.process(payload, message_id="delivery-3")

    conflict_payload = route_payload()
    conflict_payload["chunks"][0]["text"] = "different content"
    conflict = await reconstructed.process(
        conflict_payload,
        message_id="delivery-4",
    )

    assert first.success is True
    assert duplicate.duplicate is True
    assert recovered.duplicate is True
    assert vectorizer.calls == 1
    assert conflict.record.status is DocumentOperationStatus.TERMINAL_FAILURE
    assert conflict.record.error["code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict.record.publication_state is PublicationState.PENDING
    original_after_conflict = await reconstructed.operation_store.get(
        first.record.idempotency_key
    )
    assert original_after_conflict.status is DocumentOperationStatus.SUCCEEDED
    assert original_after_conflict.result == first.record.result
    assert first.record.result == recovered.record.result


@pytest.mark.asyncio
async def test_concurrent_identical_delivery_vectorizes_once(tmp_path):
    release = threading.Event()
    started = threading.Event()
    vectorizer = CountingVectorizer(release=release, started=started)
    processor = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=operation_store(tmp_path),
        claim_wait_seconds=2.0,
    )
    payload = route_payload()

    first_task = asyncio.create_task(
        processor.process(payload, message_id="concurrent-1")
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    second_task = asyncio.create_task(
        processor.process(payload, message_id="concurrent-2")
    )
    release.set()
    first, second = await asyncio.gather(first_task, second_task)

    assert first.success is True
    assert second.success is True
    assert second.duplicate is True
    assert vectorizer.calls == 1


@pytest.mark.asyncio
async def test_publication_failure_recovers_without_revectorizing(tmp_path):
    database = tmp_path / "operations.sqlite3"
    vectorizer = CountingVectorizer()
    first_transport = InMemoryDeepiriTransport()
    await first_transport.start()
    first_transport.fail_publications = 1
    first_processor = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=SQLiteDocumentOperationStore(database),
    )
    first_consumer = DocumentVectorizeConsumer(
        first_transport,
        first_processor,
        max_attempts=3,
    )
    message = DeepiriMessage(
        "publication-message",
        route_payload(),
        {"correlationId": "corr-001", "requestId": "request-001"},
        topic=DeepiriStreamTopics.DOCUMENT_VECTORIZE.value,
    )

    first_result = await first_consumer.process_message(message)
    assert first_result.record.publication_state is PublicationState.PENDING
    assert first_transport.acknowledged == []
    assert first_transport.retried == ["publication-message"]

    second_transport = InMemoryDeepiriTransport()
    second_processor = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=SQLiteDocumentOperationStore(database),
    )
    second_consumer = DocumentVectorizeConsumer(second_transport, second_processor)
    await second_consumer.start()
    recovered = await second_consumer.process_message(
        DeepiriMessage(
            "publication-message",
            route_payload(),
            message.headers,
            delivery_attempt=2,
            topic=DeepiriStreamTopics.DOCUMENT_VECTORIZE.value,
        )
    )
    await second_consumer.stop()

    assert recovered.duplicate is True
    assert recovered.record.publication_state is PublicationState.PUBLISHED
    assert vectorizer.calls == 1
    assert second_transport.acknowledged == ["publication-message"]
    assert second_transport.published[0][0] == DeepiriStreamTopics.DOCUMENT_ARTIFACTS.value
    assert second_transport.published[0][1].headers["requestId"] == "request-001"


@pytest.mark.asyncio
async def test_retry_exhaustion_dead_letters_and_terminal_validation_acks(tmp_path):
    transport = InMemoryDeepiriTransport()
    await transport.start()
    failing = CountingVectorizer(fail=True)
    consumer = DocumentVectorizeConsumer(
        transport,
        DocumentVectorizeProcessor(
            failing,
            operation_store=operation_store(tmp_path / "retry"),
        ),
        max_attempts=3,
    )
    payload = route_payload("retry")

    for attempt in range(1, 4):
        result = await consumer.process_message(
            DeepiriMessage(
                "retry-message",
                payload,
                delivery_attempt=attempt,
                topic=DeepiriStreamTopics.DOCUMENT_VECTORIZE.value,
            )
        )

    assert result.record.status is DocumentOperationStatus.DEAD_LETTERED
    assert result.record.attempt_count == 3
    assert transport.retried == ["retry-message", "retry-message"]
    assert transport.acknowledged == ["retry-message"]
    assert transport.published[-1][0] == DeepiriStreamTopics.PIPELINE_DEAD_LETTER.value

    invalid_vectorizer = CountingVectorizer()
    invalid_consumer = DocumentVectorizeConsumer(
        transport,
        DocumentVectorizeProcessor(
            invalid_vectorizer,
            operation_store=operation_store(tmp_path / "invalid"),
        ),
    )
    invalid_payload = route_payload("invalid")
    invalid_payload["chunks"] = []
    invalid = await invalid_consumer.process_message(
        DeepiriMessage("invalid-message", invalid_payload)
    )

    assert invalid.record.status is DocumentOperationStatus.TERMINAL_FAILURE
    assert invalid_vectorizer.calls == 0
    assert "invalid-message" in transport.acknowledged
    assert "invalid-message" not in transport.retried


@pytest.mark.asyncio
async def test_sync_vectorizer_is_non_blocking_and_timeout_cannot_commit_late(tmp_path):
    release = threading.Event()
    started = threading.Event()
    vectorizer = CountingVectorizer(release=release, started=started)
    processor = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=operation_store(tmp_path),
        vectorizer_timeout_seconds=0.02,
    )
    task = asyncio.create_task(
        processor.process(route_payload(), message_id="timeout-message")
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    loop_progressed = False

    async def mark_progress() -> None:
        nonlocal loop_progressed
        loop_progressed = True

    await asyncio.create_task(mark_progress())
    assert loop_progressed is True
    result = await task
    release.set()
    stored = await processor.operation_store.get(result.record.idempotency_key)

    assert result.record.status is DocumentOperationStatus.RETRYABLE_FAILURE
    assert result.record.error["code"] == "VECTORIZER_TIMEOUT"
    assert stored.status is DocumentOperationStatus.RETRYABLE_FAILURE
    assert stored.result is None


@pytest.mark.asyncio
async def test_async_vectorizer_and_caller_cancellation_are_supported(tmp_path):
    async_vectorizer = AsyncVectorizer()
    async_processor = DocumentVectorizeProcessor(
        async_vectorizer,
        operation_store=operation_store(tmp_path / "async"),
    )
    async_result = await async_processor.process(
        route_payload("async"),
        message_id="async-message",
    )
    assert async_result.success is True
    assert async_vectorizer.calls == 1

    release = threading.Event()
    started = threading.Event()
    blocking = CountingVectorizer(release=release, started=started)
    cancel_processor = DocumentVectorizeProcessor(
        blocking,
        operation_store=operation_store(tmp_path / "cancel"),
    )
    payload = route_payload("cancel")
    task = asyncio.create_task(
        cancel_processor.process(payload, message_id="cancel-message")
    )
    assert await asyncio.to_thread(started.wait, 1.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()

    key = build_document_idempotency_key(
        payload,
        message_id="cancel-message",
        headers={"messageId": "cancel-message"},
    )
    stored = await cancel_processor.operation_store.get(key)
    assert stored.status is DocumentOperationStatus.RETRYABLE_FAILURE
    assert stored.error["code"] == "CANCELLED"


@pytest.mark.asyncio
async def test_consumer_concurrency_is_bounded_and_shutdown_does_not_ack_unfinished(tmp_path):
    release = threading.Event()
    two_started = threading.Event()

    class BoundedVectorizer(CountingVectorizer):
        def vectorize(self, request):
            with self.lock:
                self.calls += 1
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                self.requests.append(request)
                if self.active == 2:
                    two_started.set()
            try:
                release.wait()
                return VectorizeBackendResult(
                    chunks=[
                        VectorizedChunk(
                            chunk_id=request.chunks[0].chunk_id,
                            text=request.chunks[0].text or "",
                            vector=[1.0, 2.0],
                        )
                    ],
                    dimensions=2,
                )
            finally:
                with self.lock:
                    self.active -= 1

    transport = AckTrackingTransport(expected_acks=4)
    vectorizer = BoundedVectorizer()
    consumer = DocumentVectorizeConsumer(
        transport,
        DocumentVectorizeProcessor(
            vectorizer,
            operation_store=operation_store(tmp_path / "bounded"),
        ),
        max_concurrency=2,
    )
    await consumer.start()
    for index in range(4):
        await transport.send(
            DeepiriStreamTopics.DOCUMENT_VECTORIZE.value,
            DeepiriMessage(f"bounded-{index}", route_payload(str(index))),
        )

    assert await asyncio.to_thread(two_started.wait, 1.0)
    assert vectorizer.max_active == 2
    release.set()
    await asyncio.wait_for(transport.all_acknowledged.wait(), timeout=2.0)
    await consumer.stop()
    assert vectorizer.max_active == 2
    assert consumer._dispatcher is None
    assert consumer._active == set()

    cancel_release = threading.Event()
    cancel_started = threading.Event()
    cancel_transport = InMemoryDeepiriTransport()
    cancel_processor = DocumentVectorizeProcessor(
        CountingVectorizer(release=cancel_release, started=cancel_started),
        operation_store=operation_store(tmp_path / "shutdown"),
    )
    cancel_consumer = DocumentVectorizeConsumer(
        cancel_transport,
        cancel_processor,
        max_concurrency=1,
    )
    cancel_payload = route_payload("shutdown")
    await cancel_consumer.start()
    await cancel_transport.send(
        DeepiriStreamTopics.DOCUMENT_VECTORIZE.value,
        DeepiriMessage("shutdown-message", cancel_payload),
    )
    assert await asyncio.to_thread(cancel_started.wait, 1.0)
    await cancel_consumer.stop(drain=False)
    cancel_release.set()

    assert cancel_transport.acknowledged == []
    key = build_document_idempotency_key(
        cancel_payload,
        message_id="shutdown-message",
        headers={"messageId": "shutdown-message"},
    )
    stored = await cancel_processor.operation_store.get(key)
    assert stored.status is DocumentOperationStatus.RETRYABLE_FAILURE
