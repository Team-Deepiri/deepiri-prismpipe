"""Durable document.vectorize processing and managed Deepiri consumption."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from prismpipe.core import Intent, create_envelope
from prismpipe.deepiri_bus import (
    DeepiriMessage,
    DeepiriStreamTopics,
    DeepiriTransport,
    DeepiriTransportError,
)
from prismpipe.document.operations import (
    DocumentOperationRecord,
    DocumentOperationStatus,
    DocumentProcessingError,
    IdempotencyConflictError,
    PublicationState,
    SQLiteDocumentOperationStore,
    canonical_json,
)
from prismpipe.document.vectorize import (
    DOCUMENT_VECTORIZE_CAPABILITY,
    DOCUMENT_VECTORIZE_INPUT_KEY,
    DocumentVectorizeError,
    DocumentVectorizeInput,
    DocumentVectorizeNode,
    DocumentVectorizeValidationError,
    Vectorizer,
)
from prismpipe.exceptions import StorageError
from prismpipe.storage import FileStorage, StorageBackend


@dataclass
class DocumentProcessingResult:
    record: DocumentOperationRecord
    duplicate: bool = False

    @property
    def success(self) -> bool:
        return self.record.status is DocumentOperationStatus.SUCCEEDED

    @property
    def retryable(self) -> bool:
        return self.record.status is DocumentOperationStatus.RETRYABLE_FAILURE


class DocumentVectorizeProcessor:
    """Durable, idempotent document.vectorize processing boundary."""

    def __init__(
        self,
        vectorizer: Vectorizer,
        *,
        operation_store: SQLiteDocumentOperationStore | None = None,
        chunk_storage: StorageBackend[Any] | None = None,
        vectorizer_timeout_seconds: float = 30.0,
        claim_wait_seconds: float = 30.0,
    ) -> None:
        self.vectorizer = vectorizer
        self.operation_store = operation_store or SQLiteDocumentOperationStore()
        self.chunk_storage = chunk_storage or FileStorage("./data/document_chunks")
        self.vectorizer_timeout_seconds = vectorizer_timeout_seconds
        self.claim_wait_seconds = claim_wait_seconds

    async def process(
        self,
        payload: Mapping[str, Any],
        *,
        message_id: str,
        headers: Mapping[str, Any] | None = None,
    ) -> DocumentProcessingResult:
        request_metadata = _preserved_metadata(headers, message_id)
        try:
            request = DocumentVectorizeInput.from_payload(payload)
            normalized_payload = request.to_payload()
        except DocumentVectorizeValidationError as error:
            return await self._record_validation_failure(
                payload,
                message_id=message_id,
                headers=request_metadata,
                error=error,
            )

        idempotency_key = build_document_idempotency_key(
            normalized_payload,
            message_id=message_id,
            headers=request_metadata,
        )
        fingerprint = build_document_request_fingerprint(normalized_payload)
        operation_id = build_document_operation_id(idempotency_key)
        try:
            claim = await self.operation_store.claim(
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                document_id=request.document_id,
                manifest_version=str(request.manifest_version),
                capability=DOCUMENT_VECTORIZE_CAPABILITY,
                normalized_payload=normalized_payload,
                request_metadata=request_metadata,
            )
        except IdempotencyConflictError as error:
            existing = await self.operation_store.get(idempotency_key)
            if existing is None:
                raise
            return await self._record_conflict(
                existing,
                normalized_payload=normalized_payload,
                request_fingerprint=fingerprint,
                request_metadata=request_metadata,
                error=error,
            )

        if not claim.owner:
            settled = await self._settled_duplicate(claim.record)
            return DocumentProcessingResult(settled, duplicate=True)

        try:
            loaded_request = await self._resolve_chunk_content(request)
            output = await self._execute_vectorizer(loaded_request)
            outbound = _result_message(
                claim.record,
                success=True,
                result=output,
                error=None,
            )
            record = await self.operation_store.finish(
                idempotency_key,
                status=DocumentOperationStatus.SUCCEEDED,
                result=output,
                error=None,
                outbound_topic=DeepiriStreamTopics.DOCUMENT_ARTIFACTS.value,
                outbound_message_id=f"document-result-{operation_id}",
                outbound_payload=outbound,
            )
            return DocumentProcessingResult(record)
        except asyncio.CancelledError:
            cancellation = DocumentProcessingError(
                "CANCELLED",
                "Document vectorization was cancelled",
                retryable=True,
            )
            await self.operation_store.record_retryable_failure(
                idempotency_key,
                cancellation.to_payload(),
            )
            raise
        except DocumentProcessingError as error:
            if error.retryable:
                record = await self.operation_store.record_retryable_failure(
                    idempotency_key,
                    error.to_payload(),
                )
                return DocumentProcessingResult(record)
            return DocumentProcessingResult(
                await self._finish_failure(claim.record, error.to_payload())
            )

    async def _record_validation_failure(
        self,
        payload: Mapping[str, Any],
        *,
        message_id: str,
        headers: Mapping[str, Any],
        error: DocumentVectorizeValidationError,
    ) -> DocumentProcessingResult:
        normalized = _normalize_untrusted_payload(payload)
        idempotency_key = build_document_idempotency_key(
            normalized,
            message_id=message_id,
            headers=headers,
        )
        fingerprint = build_document_request_fingerprint(normalized)
        operation_id = build_document_operation_id(idempotency_key)
        document_id = _safe_identity_value(normalized.get("documentId"), "unknown")
        manifest_version = _safe_identity_value(
            normalized.get("manifestVersion"),
            "unknown",
        )
        try:
            claim = await self.operation_store.claim(
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                document_id=document_id,
                manifest_version=manifest_version,
                capability=DOCUMENT_VECTORIZE_CAPABILITY,
                normalized_payload=normalized,
                request_metadata=headers,
            )
        except IdempotencyConflictError as conflict:
            existing = await self.operation_store.get(idempotency_key)
            if existing is None:
                raise
            return await self._record_conflict(
                existing,
                normalized_payload=normalized,
                request_fingerprint=fingerprint,
                request_metadata=headers,
                error=conflict,
            )

        if not claim.owner:
            settled = await self._settled_duplicate(claim.record)
            return DocumentProcessingResult(settled, duplicate=True)

        structured = DocumentVectorizeError(
            code="VALIDATION_ERROR",
            message=str(error),
            document_id=None if document_id == "unknown" else document_id,
            retryable=False,
        ).to_payload()
        return DocumentProcessingResult(
            await self._finish_failure(claim.record, structured)
        )

    async def _record_conflict(
        self,
        existing: DocumentOperationRecord,
        *,
        normalized_payload: Mapping[str, Any],
        request_fingerprint: str,
        request_metadata: Mapping[str, Any],
        error: IdempotencyConflictError,
    ) -> DocumentProcessingResult:
        conflict_key = (
            f"{existing.idempotency_key}:conflict:{request_fingerprint[:24]}"
        )
        conflict_operation_id = build_document_operation_id(conflict_key)
        claim = await self.operation_store.claim(
            operation_id=conflict_operation_id,
            idempotency_key=conflict_key,
            request_fingerprint=request_fingerprint,
            document_id=_safe_identity_value(
                normalized_payload.get("documentId"),
                existing.document_id,
            ),
            manifest_version=_safe_identity_value(
                normalized_payload.get("manifestVersion"),
                existing.manifest_version,
            ),
            capability=DOCUMENT_VECTORIZE_CAPABILITY,
            normalized_payload=normalized_payload,
            request_metadata=request_metadata,
        )
        if not claim.owner:
            settled = await self._settled_duplicate(claim.record)
            return DocumentProcessingResult(settled, duplicate=True)

        structured = error.to_payload()
        structured["details"] = {
            "conflictingOperationId": existing.operation_id,
            "conflictingIdempotencyKey": existing.idempotency_key,
        }
        record = await self._finish_failure(claim.record, structured)
        return DocumentProcessingResult(record, duplicate=True)

    async def _finish_failure(
        self,
        record: DocumentOperationRecord,
        error: Mapping[str, Any],
        *,
        dead_lettered: bool = False,
    ) -> DocumentOperationRecord:
        outbound = _result_message(
            record,
            success=False,
            result=None,
            error=error,
        )
        return await self.operation_store.finish(
            record.idempotency_key,
            status=(
                DocumentOperationStatus.DEAD_LETTERED
                if dead_lettered
                else DocumentOperationStatus.TERMINAL_FAILURE
            ),
            result=None,
            error=error,
            outbound_topic=DeepiriStreamTopics.PIPELINE_DEAD_LETTER.value,
            outbound_message_id=f"document-failure-{record.operation_id}",
            outbound_payload=outbound,
        )

    async def _settled_duplicate(
        self,
        record: DocumentOperationRecord,
    ) -> DocumentOperationRecord:
        if record.status is not DocumentOperationStatus.PROCESSING:
            return record
        deadline = asyncio.get_running_loop().time() + self.claim_wait_seconds
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
            current = await self.operation_store.get(record.idempotency_key)
            if current is None:
                break
            if current.status is not DocumentOperationStatus.PROCESSING:
                return current
        return record

    async def _resolve_chunk_content(
        self,
        request: DocumentVectorizeInput,
    ) -> DocumentVectorizeInput:
        loaded = copy.deepcopy(request)
        for position, chunk in enumerate(loaded.chunks):
            # Inline text is authoritative; storage is the fallback for storage-only chunks.
            if chunk.text is not None:
                continue
            if chunk.storage is None:
                raise DocumentProcessingError(
                    "INVALID_STORAGE_REFERENCE",
                    f"chunks[{position}] must include text or storage",
                    retryable=False,
                )
            try:
                content = await self.chunk_storage.load(chunk.storage.storage_key())
            except asyncio.CancelledError:
                raise
            except StorageError as error:
                raise DocumentProcessingError(
                    "STORAGE_UNAVAILABLE",
                    f"Unable to load chunks[{position}] content",
                    retryable=True,
                    details={"operation": error.operation},
                ) from error
            except Exception as error:
                raise DocumentProcessingError(
                    "STORAGE_UNAVAILABLE",
                    f"Unable to load chunks[{position}] content",
                    retryable=True,
                    details={"errorType": type(error).__name__},
                ) from error
            if content is None:
                raise DocumentProcessingError(
                    "STORAGE_CONTENT_NOT_FOUND",
                    f"Storage content for chunks[{position}] was not found",
                    retryable=False,
                )
            chunk.text = _decode_chunk_content(content, position)
        return loaded

    async def _execute_vectorizer(
        self,
        request: DocumentVectorizeInput,
    ) -> dict[str, Any]:
        envelope = create_envelope(
            intent=Intent.CUSTOM,
            input_data={DOCUMENT_VECTORIZE_INPUT_KEY: request.to_payload()},
            next_capability=DOCUMENT_VECTORIZE_CAPABILITY,
        )
        node = DocumentVectorizeNode(
            self.vectorizer,
            timeout_seconds=self.vectorizer_timeout_seconds,
        )
        result = await node.execute_async(
            envelope,
            timeout_seconds=self.vectorizer_timeout_seconds,
        )
        if result.success and not result.envelope.terminated:
            output = result.envelope.state.get("document_vectorize")
            if isinstance(output, dict):
                return copy.deepcopy(output)
        structured = result.envelope.state.get("document_vectorize_error")
        if isinstance(structured, dict):
            raise DocumentProcessingError(
                str(structured.get("code", "VECTORIZER_ERROR")),
                str(structured.get("message", "Vectorizer backend failed")),
                retryable=bool(structured.get("retryable", True)),
                details=structured.get("details") or {},
            )
        message = result.error or result.envelope.error or "Vectorizer backend failed"
        code = "VECTORIZER_TIMEOUT" if "timed out" in message else "VECTORIZER_ERROR"
        raise DocumentProcessingError(code, message, retryable=True)


class DocumentVectorizeConsumer:
    """Bounded managed consumer for the Deepiri document.vectorize stream."""

    def __init__(
        self,
        transport: DeepiriTransport,
        processor: DocumentVectorizeProcessor,
        *,
        max_concurrency: int = 4,
        max_attempts: int = 3,
        pending_drain_limit: int = 100,
        retry_delay_seconds: float = 0.0,
    ) -> None:
        if max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self.transport = transport
        self.processor = processor
        self.max_concurrency = max_concurrency
        self.max_attempts = max_attempts
        self.pending_drain_limit = pending_drain_limit
        self.retry_delay_seconds = max(retry_delay_seconds, 0.0)
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._dispatcher: asyncio.Task[None] | None = None
        self._active: set[asyncio.Task[None]] = set()
        self._stopping = False

    async def start(self) -> None:
        if self._dispatcher is not None and not self._dispatcher.done():
            return
        self._stopping = False
        await self.transport.start()
        await self.drain_pending_publications(self.pending_drain_limit)
        self._dispatcher = asyncio.create_task(
            self._consume(),
            name="prismpipe-document-vectorize-consumer",
        )

    async def stop(self, *, drain: bool = True) -> None:
        self._stopping = True
        dispatcher = self._dispatcher
        self._dispatcher = None
        if dispatcher is not None:
            dispatcher.cancel()
            await asyncio.gather(dispatcher, return_exceptions=True)
        tasks = list(self._active)
        if tasks:
            if drain:
                await asyncio.gather(*tasks, return_exceptions=True)
            else:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        await self.transport.stop()

    async def _consume(self) -> None:
        async for message in self.transport.consume(
            DeepiriStreamTopics.DOCUMENT_VECTORIZE.value
        ):
            if self._stopping:
                break
            await self._semaphore.acquire()
            if self._stopping:
                self._semaphore.release()
                break
            task = asyncio.create_task(
                self._handle_and_release(message),
                name=f"prismpipe-document-{message.message_id}",
            )
            self._active.add(task)
            task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task[None]) -> None:
        self._active.discard(task)
        if not task.cancelled():
            task.exception()

    async def _handle_and_release(self, message: DeepiriMessage) -> None:
        try:
            await self.process_message(message)
        finally:
            self._semaphore.release()

    async def process_message(self, message: DeepiriMessage) -> DocumentProcessingResult:
        result = await self.processor.process(
            message.payload,
            message_id=message.message_id,
            headers=message.headers,
        )
        if result.record.status is DocumentOperationStatus.PROCESSING:
            if message.delivery_attempt < self.max_attempts:
                await self._retry(message)
            # Never acknowledge an unfinished operation. A broker may redeliver it
            # after the durable claim lease expires.
            return result

        if result.retryable:
            if max(result.record.attempt_count, message.delivery_attempt) < self.max_attempts:
                await self._retry(message)
                return result
            exhausted = dict(result.record.error or {})
            exhausted["retryable"] = False
            exhausted["exhausted"] = True
            record = await self.processor._finish_failure(
                result.record,
                exhausted,
                dead_lettered=True,
            )
            result = DocumentProcessingResult(record, duplicate=result.duplicate)

        if result.record.publication_state is PublicationState.PENDING:
            try:
                result.record = await self._publish_record(result.record)
            except DeepiriTransportError:
                result.record = await self.processor.operation_store.increment_publication_attempt(
                    result.record.idempotency_key
                )
                if result.record.publication_attempts < self.max_attempts:
                    await self._retry(message)
                    return result
                await self.transport.acknowledge(message)
                return result

        await self.transport.acknowledge(message)
        return result

    async def _retry(self, message: DeepiriMessage) -> None:
        if self.retry_delay_seconds > 0:
            await asyncio.sleep(self.retry_delay_seconds)
        await self.transport.retry(message)

    async def _publish_record(
        self,
        record: DocumentOperationRecord,
    ) -> DocumentOperationRecord:
        if (
            record.outbound_topic is None
            or record.outbound_message_id is None
            or record.outbound_payload is None
        ):
            raise RuntimeError("Pending publication record is incomplete")
        await self.transport.publish(
            record.outbound_topic,
            record.outbound_payload,
            message_id=record.outbound_message_id,
            headers=record.request_metadata,
        )
        return await self.processor.operation_store.mark_published(
            record.idempotency_key
        )

    async def drain_pending_publications(self, limit: int | None = None) -> int:
        records = await self.processor.operation_store.pending_publications(
            self.pending_drain_limit if limit is None else limit
        )
        published = 0
        for record in records:
            try:
                await self._publish_record(record)
            except DeepiriTransportError:
                await self.processor.operation_store.increment_publication_attempt(
                    record.idempotency_key
                )
                continue
            published += 1
        return published


def build_document_idempotency_key(
    payload: Mapping[str, Any],
    *,
    message_id: str,
    headers: Mapping[str, Any] | None = None,
) -> str:
    headers = headers or {}
    identity = {
        "capability": DOCUMENT_VECTORIZE_CAPABILITY,
        "documentId": payload.get("documentId"),
        "manifestVersion": payload.get("manifestVersion"),
        "routeId": payload.get("routeId"),
        "correlationId": payload.get("correlationId")
        or headers.get("correlationId")
        or headers.get("correlation_id"),
        "operationId": payload.get("operationId")
        or headers.get("operationId")
        or headers.get("operation_id"),
    }
    if not any(identity[key] for key in ("routeId", "correlationId", "operationId")):
        identity["messageId"] = message_id
    digest = hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()
    return f"document-vectorize:{digest}"


def build_document_request_fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_document_operation_id(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"docop_{digest[:24]}"


def _result_message(
    record: DocumentOperationRecord,
    *,
    success: bool,
    result: Mapping[str, Any] | None,
    error: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "success": success,
        "operationId": record.operation_id,
        "idempotencyKey": record.idempotency_key,
        "documentId": record.document_id,
        "manifestVersion": record.manifest_version,
        "capability": record.capability,
        "result": dict(result) if result is not None else None,
        "error": dict(error) if error is not None else None,
        "metadata": dict(record.request_metadata),
    }


def _decode_chunk_content(content: Any, position: int) -> str:
    if isinstance(content, str) and content:
        return content
    if isinstance(content, bytes):
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DocumentProcessingError(
                "INVALID_STORAGE_CONTENT",
                f"Storage content for chunks[{position}] is not UTF-8",
                retryable=False,
            ) from error
        if decoded:
            return decoded
    if isinstance(content, Mapping):
        value = content.get("text", content.get("content"))
        if isinstance(value, str) and value:
            return value
    raise DocumentProcessingError(
        "INVALID_STORAGE_CONTENT",
        f"Storage content for chunks[{position}] must contain non-empty text",
        retryable=False,
    )


def _preserved_metadata(
    headers: Mapping[str, Any] | None,
    message_id: str,
) -> dict[str, Any]:
    source = dict(headers or {})
    allowed = {
        "correlationId",
        "correlation_id",
        "requestId",
        "request_id",
        "traceId",
        "trace_id",
        "causationId",
        "causation_id",
        "operationId",
        "operation_id",
    }
    preserved = {key: value for key, value in source.items() if key in allowed}
    preserved["messageId"] = message_id
    return preserved


def _normalize_untrusted_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(canonical_json(payload))
    except (TypeError, ValueError) as error:
        raise DocumentProcessingError(
            "INVALID_PAYLOAD",
            "Document payload cannot be canonically serialized",
            retryable=False,
        ) from error


def _safe_identity_value(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return fallback
