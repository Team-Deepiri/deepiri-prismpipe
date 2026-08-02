"""Integration-style tests for durable document processing and messaging."""

from __future__ import annotations

import asyncio
import sqlite3
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
    StaleDocumentOperationClaimError,
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



@pytest.mark.asyncio
async def test_expired_lease_reclaim_fences_stale_worker_updates(tmp_path):
    release = threading.Event()
    started = threading.Event()

    class FixedVectorizer(CountingVectorizer):
        def __init__(self, vector, **kwargs):
            super().__init__(**kwargs)
            self.vector = vector

        def vectorize(self, request):
            with self.lock:
                self.calls += 1
                self.requests.append(request)
                if self.started is not None:
                    self.started.set()
            if self.release is not None:
                self.release.wait()
            return VectorizeBackendResult(
                chunks=[
                    VectorizedChunk(
                        chunk_id=request.chunks[0].chunk_id,
                        text=request.chunks[0].text or "",
                        vector=list(self.vector),
                    )
                ],
                dimensions=len(self.vector),
            )

    store = SQLiteDocumentOperationStore(
        tmp_path / "operations.sqlite3",
        claim_lease_seconds=0.0,
    )
    payload = route_payload("fenced")
    first_processor = DocumentVectorizeProcessor(
        FixedVectorizer([1.0, 1.0], release=release, started=started),
        operation_store=store,
    )
    second_processor = DocumentVectorizeProcessor(
        FixedVectorizer([9.0, 9.0]),
        operation_store=store,
    )
    first_task = asyncio.create_task(
        first_processor.process(payload, message_id="lease-worker-a")
    )
    assert await asyncio.to_thread(started.wait, 1.0)

    key = build_document_idempotency_key(
        payload,
        message_id="lease-worker-a",
        headers={"messageId": "lease-worker-a"},
    )
    first_claim = await store.get(key)
    assert first_claim is not None
    assert first_claim.claim_token is not None

    second_result = await second_processor.process(
        payload,
        message_id="lease-worker-b",
    )
    assert second_result.success is True
    assert second_result.record.attempt_count == 2
    assert second_result.record.result["chunks"][0]["vector"] == [9.0, 9.0]

    with pytest.raises(StaleDocumentOperationClaimError, match="no longer active"):
        await store.record_retryable_failure(
            key,
            first_claim.claim_token,
            {"code": "STALE_FAILURE", "retryable": True},
        )
    with pytest.raises(StaleDocumentOperationClaimError, match="no longer active"):
        await store.record_retryable_failure(
            key,
            first_claim.claim_token,
            {"code": "CANCELLED", "retryable": True},
        )
    with pytest.raises(StaleDocumentOperationClaimError, match="no longer active"):
        await store.finish(
            key,
            claim_token=first_claim.claim_token,
            status=DocumentOperationStatus.TERMINAL_FAILURE,
            result=None,
            error={"code": "STALE_TERMINAL_FAILURE"},
            outbound_topic=DeepiriStreamTopics.PIPELINE_DEAD_LETTER.value,
            outbound_message_id="stale-publication",
            outbound_payload={"success": False},
        )

    release.set()
    with pytest.raises(StaleDocumentOperationClaimError, match="no longer active"):
        await first_task

    final = await store.get(key)
    assert final is not None
    assert final.status is DocumentOperationStatus.SUCCEEDED
    assert final.attempt_count == 2
    assert final.claim_token is None
    assert final.error is None
    assert final.result["chunks"][0]["vector"] == [9.0, 9.0]
    assert final.publication_state is PublicationState.PENDING
    assert final.publication_attempts == 0
    assert final.outbound_message_id == second_result.record.outbound_message_id
    assert final.outbound_payload["result"]["chunks"][0]["vector"] == [9.0, 9.0]


@pytest.mark.asyncio
async def test_invalid_backend_numbers_are_persisted_only_as_structured_failure(tmp_path):
    class InvalidBackendVectorizer:
        provider = "invalid-provider"
        model = "invalid-model"

        def vectorize(self, request):
            return VectorizeBackendResult(
                chunks=[
                    VectorizedChunk(
                        chunk_id=request.chunks[0].chunk_id,
                        text=request.chunks[0].text or "",
                        vector=[float("nan"), 2.0],
                    )
                ],
                dimensions=2,
            )

    processor = DocumentVectorizeProcessor(
        InvalidBackendVectorizer(),
        operation_store=operation_store(tmp_path),
    )

    result = await processor.process(route_payload("nan"), message_id="nan-message")

    assert result.record.status is DocumentOperationStatus.TERMINAL_FAILURE
    assert result.record.result is None
    assert result.record.error["code"] == "VECTORIZER_RESULT_INVALID"
    assert result.record.error["retryable"] is False
    assert "chunks[0].vector[0]" in result.record.error["details"]["error"]
    assert result.record.publication_state is PublicationState.PENDING
    assert result.record.outbound_payload["success"] is False
    assert result.record.outbound_payload["result"] is None


class PublicationTrackingTransport(InMemoryDeepiriTransport):
    def __init__(self) -> None:
        super().__init__()
        self.publication_ids: list[str] = []
        self.publication_times: list[float] = []
        self.publication_succeeded = asyncio.Event()
        self.message_acknowledged = asyncio.Event()
        self.third_publication_attempt = asyncio.Event()

    async def publish(self, topic, payload, *, message_id, headers=None):
        self.publication_ids.append(message_id)
        self.publication_times.append(asyncio.get_running_loop().time())
        if len(self.publication_ids) >= 3:
            self.third_publication_attempt.set()
        await super().publish(
            topic,
            payload,
            message_id=message_id,
            headers=headers,
        )
        self.publication_succeeded.set()

    async def acknowledge(self, message):
        await super().acknowledge(message)
        self.message_acknowledged.set()


@pytest.mark.asyncio
async def test_background_recovery_publishes_later_without_revectorizing(tmp_path):
    transport = PublicationTrackingTransport()
    transport.fail_publications = 3
    vectorizer = CountingVectorizer()
    processor = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=operation_store(tmp_path),
    )
    consumer = DocumentVectorizeConsumer(
        transport,
        processor,
        max_attempts=3,
        pending_recovery_interval_seconds=0.03,
    )
    payload = route_payload("recover")
    await consumer.start()
    await transport.send(
        DeepiriStreamTopics.DOCUMENT_VECTORIZE.value,
        DeepiriMessage("recover-message", payload),
    )

    await asyncio.wait_for(transport.message_acknowledged.wait(), timeout=1.0)
    key = build_document_idempotency_key(
        payload,
        message_id="recover-message",
        headers={"messageId": "recover-message"},
    )
    pending = await processor.operation_store.get(key)
    assert pending is not None
    assert pending.status is DocumentOperationStatus.SUCCEEDED
    assert pending.publication_state is PublicationState.PENDING
    assert pending.publication_attempts == 3

    await asyncio.wait_for(transport.publication_succeeded.wait(), timeout=1.0)
    async with asyncio.timeout(1.0):
        while True:
            recovered = await processor.operation_store.get(key)
            if recovered is not None and recovered.publication_state is PublicationState.PUBLISHED:
                break
            await asyncio.sleep(0.005)
    await consumer.stop()

    assert recovered is not None
    assert recovered.status is DocumentOperationStatus.SUCCEEDED
    assert recovered.publication_state is PublicationState.PUBLISHED
    assert vectorizer.calls == 1
    assert len(transport.publication_ids) == 4
    assert len(set(transport.publication_ids)) == 1
    assert transport.publication_ids[0] == pending.outbound_message_id
    assert consumer._recovery_task is None


@pytest.mark.asyncio
async def test_permanent_publication_failure_is_rate_limited_and_remains_durable(tmp_path):
    transport = PublicationTrackingTransport()
    transport.fail_publications = 1000
    vectorizer = CountingVectorizer()
    processor = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=operation_store(tmp_path),
    )
    consumer = DocumentVectorizeConsumer(
        transport,
        processor,
        max_attempts=1,
        pending_recovery_interval_seconds=0.03,
    )
    payload = route_payload("permanent")
    await consumer.start()
    await transport.send(
        DeepiriStreamTopics.DOCUMENT_VECTORIZE.value,
        DeepiriMessage("permanent-message", payload),
    )

    await asyncio.wait_for(transport.third_publication_attempt.wait(), timeout=1.0)
    key = build_document_idempotency_key(
        payload,
        message_id="permanent-message",
        headers={"messageId": "permanent-message"},
    )
    async with asyncio.timeout(1.0):
        while True:
            stored = await processor.operation_store.get(key)
            if stored is not None and stored.publication_attempts >= 3:
                break
            await asyncio.sleep(0.005)
    await asyncio.wait_for(consumer.stop(), timeout=0.5)
    assert stored is not None
    assert stored.status is DocumentOperationStatus.SUCCEEDED
    assert stored.publication_state is PublicationState.PENDING
    assert stored.publication_attempts >= 3
    assert vectorizer.calls == 1
    assert len(set(transport.publication_ids)) == 1
    assert transport.publication_times[1] - transport.publication_times[0] >= 0.02
    assert transport.publication_times[2] - transport.publication_times[1] >= 0.02
    assert len(transport.publication_ids) <= 4
    assert consumer._recovery_task is None
    assert consumer._active == set()


@pytest.mark.asyncio
async def test_startup_recovery_batch_is_bounded(tmp_path):
    vectorizer = CountingVectorizer()
    processor = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=operation_store(tmp_path),
    )
    for index in range(3):
        result = await processor.process(
            route_payload(f"batch-{index}"),
            message_id=f"batch-{index}",
        )
        assert result.record.publication_state is PublicationState.PENDING

    transport = PublicationTrackingTransport()
    transport.fail_publications = 100
    consumer = DocumentVectorizeConsumer(
        transport,
        processor,
        pending_drain_limit=2,
        pending_recovery_interval_seconds=10.0,
    )

    await consumer.start()
    assert len(transport.publication_ids) == 2
    assert len(await processor.operation_store.pending_publications(limit=10)) == 3
    await consumer.stop()


@pytest.mark.asyncio
async def test_shutdown_cancels_blocked_publication_without_losing_outbox(tmp_path):
    class BlockingPublicationTransport(InMemoryDeepiriTransport):
        def __init__(self):
            super().__init__()
            self.publication_started = asyncio.Event()
            self.never_release = asyncio.Event()

        async def publish(self, topic, payload, *, message_id, headers=None):
            self.publication_started.set()
            await self.never_release.wait()

    transport = BlockingPublicationTransport()
    vectorizer = CountingVectorizer()
    processor = DocumentVectorizeProcessor(
        vectorizer,
        operation_store=operation_store(tmp_path),
    )
    consumer = DocumentVectorizeConsumer(
        transport,
        processor,
        max_attempts=1,
        pending_recovery_interval_seconds=0.03,
        publication_timeout_seconds=0.02,
    )
    payload = route_payload("blocked-publication")
    await consumer.start()
    await transport.send(
        DeepiriStreamTopics.DOCUMENT_VECTORIZE.value,
        DeepiriMessage("blocked-publication-message", payload),
    )
    await asyncio.wait_for(transport.publication_started.wait(), timeout=1.0)

    await asyncio.wait_for(consumer.stop(), timeout=0.5)

    key = build_document_idempotency_key(
        payload,
        message_id="blocked-publication-message",
        headers={"messageId": "blocked-publication-message"},
    )
    stored = await processor.operation_store.get(key)
    assert stored is not None
    assert stored.status is DocumentOperationStatus.SUCCEEDED
    assert stored.publication_state is PublicationState.PENDING
    assert vectorizer.calls == 1
    assert transport.acknowledged == ["blocked-publication-message"]
    assert stored.publication_attempts == 1
    assert consumer._dispatcher is None
    assert consumer._recovery_task is None
    assert consumer._active == set()



@pytest.mark.asyncio
async def test_legacy_operation_database_migrates_claim_token_idempotently(tmp_path):
    database = tmp_path / "legacy-operations.sqlite3"
    normalized_payload = {"documentId": "legacy-doc"}
    request_metadata = {"messageId": "legacy-message"}
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE document_operations (
                operation_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_fingerprint TEXT NOT NULL,
                document_id TEXT NOT NULL,
                manifest_version TEXT NOT NULL,
                capability TEXT NOT NULL,
                normalized_payload TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                result_json TEXT,
                error_json TEXT,
                metadata_json TEXT NOT NULL,
                publication_state TEXT NOT NULL,
                publication_attempts INTEGER NOT NULL,
                outbound_topic TEXT,
                outbound_message_id TEXT,
                outbound_payload_json TEXT,
                lease_until TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX document_operations_pending_idx
            ON document_operations(publication_state, updated_at)
            """
        )
        connection.execute(
            """
            INSERT INTO document_operations (
                operation_id, idempotency_key, request_fingerprint,
                document_id, manifest_version, capability,
                normalized_payload, status, attempt_count,
                created_at, updated_at, result_json, error_json,
                metadata_json, publication_state, publication_attempts,
                outbound_topic, outbound_message_id,
                outbound_payload_json, lease_until
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 0, NULL, NULL, NULL, NULL)
            """,
            (
                "legacy-operation",
                "legacy-key",
                "legacy-fingerprint",
                "legacy-doc",
                "1",
                "document.vectorize",
                '{"documentId":"legacy-doc"}',
                DocumentOperationStatus.RETRYABLE_FAILURE.value,
                1,
                "2026-07-01T00:00:00+00:00",
                "2026-07-01T00:00:01+00:00",
                '{"code":"TEMPORARY_FAILURE"}',
                '{"messageId":"legacy-message"}',
                PublicationState.NONE.value,
            ),
        )
        original_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(document_operations)"
            ).fetchall()
        }
    assert "claim_token" not in original_columns

    store = SQLiteDocumentOperationStore(database)
    with sqlite3.connect(database) as connection:
        migrated_columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(document_operations)"
            ).fetchall()
        ]
        indexes = {
            row[1]
            for row in connection.execute(
                "PRAGMA index_list(document_operations)"
            ).fetchall()
        }
    assert migrated_columns.count("claim_token") == 1
    assert "document_operations_pending_idx" in indexes

    existing = await store.get("legacy-key")
    assert existing is not None
    assert existing.operation_id == "legacy-operation"
    assert existing.normalized_payload == normalized_payload
    assert existing.request_metadata == request_metadata
    assert existing.status is DocumentOperationStatus.RETRYABLE_FAILURE
    assert existing.claim_token is None

    claim = await store.claim(
        operation_id="legacy-operation",
        idempotency_key="legacy-key",
        request_fingerprint="legacy-fingerprint",
        document_id="legacy-doc",
        manifest_version="1",
        capability="document.vectorize",
        normalized_payload=normalized_payload,
        request_metadata=request_metadata,
    )
    assert claim.owner is True
    assert claim.record.attempt_count == 2
    assert claim.record.claim_token is not None

    completed = await store.finish(
        "legacy-key",
        claim_token=claim.record.claim_token,
        status=DocumentOperationStatus.SUCCEEDED,
        result={"documentId": "legacy-doc", "dimensions": 2},
        error=None,
        outbound_topic=DeepiriStreamTopics.DOCUMENT_ARTIFACTS.value,
        outbound_message_id="legacy-result",
        outbound_payload={"success": True, "documentId": "legacy-doc"},
    )
    assert completed.status is DocumentOperationStatus.SUCCEEDED
    assert completed.claim_token is None
    assert completed.publication_state is PublicationState.PENDING

    reopened = SQLiteDocumentOperationStore(database)
    reopened_record = await reopened.get("legacy-key")
    assert reopened_record is not None
    assert reopened_record.status is DocumentOperationStatus.SUCCEEDED
    assert reopened_record.result == {"documentId": "legacy-doc", "dimensions": 2}
    assert reopened_record.outbound_message_id == "legacy-result"
    with sqlite3.connect(database) as connection:
        reopened_columns = [
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(document_operations)"
            ).fetchall()
        ]
    assert reopened_columns.count("claim_token") == 1


@pytest.mark.asyncio
async def test_repeated_and_concurrent_start_create_one_recovery_task(tmp_path):
    transport = InMemoryDeepiriTransport()
    consumer = DocumentVectorizeConsumer(
        transport,
        DocumentVectorizeProcessor(
            CountingVectorizer(),
            operation_store=operation_store(tmp_path),
        ),
        pending_recovery_interval_seconds=10.0,
    )

    await asyncio.gather(consumer.start(), consumer.start())
    dispatcher = consumer._dispatcher
    recovery_task = consumer._recovery_task
    assert dispatcher is not None
    assert recovery_task is not None
    assert not dispatcher.done()
    assert not recovery_task.done()

    await consumer.start()
    assert consumer._dispatcher is dispatcher
    assert consumer._recovery_task is recovery_task

    dispatcher.cancel()
    await asyncio.gather(dispatcher, return_exceptions=True)
    await consumer.start()
    assert recovery_task.done()
    assert consumer._dispatcher is not dispatcher
    assert consumer._recovery_task is not recovery_task

    await consumer.stop()
    assert consumer._dispatcher is None
    assert consumer._recovery_task is None
