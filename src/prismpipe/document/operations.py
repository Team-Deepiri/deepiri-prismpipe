"""SQLite-backed durable state for document.vectorize operations."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class DocumentOperationStatus(str, Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"
    DEAD_LETTERED = "dead_lettered"


class PublicationState(str, Enum):
    NONE = "none"
    PENDING = "pending"
    PUBLISHED = "published"


class DocumentProcessingError(RuntimeError):
    """Classified document processing failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "details": dict(self.details),
        }


class IdempotencyConflictError(DocumentProcessingError):
    def __init__(self) -> None:
        super().__init__(
            "IDEMPOTENCY_CONFLICT",
            "The idempotency key is already associated with a different request",
            retryable=False,
        )


@dataclass
class DocumentOperationRecord:
    operation_id: str
    idempotency_key: str
    request_fingerprint: str
    document_id: str
    manifest_version: str
    capability: str
    normalized_payload: dict[str, Any]
    status: DocumentOperationStatus
    attempt_count: int
    created_at: str
    updated_at: str
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    request_metadata: dict[str, Any]
    publication_state: PublicationState
    publication_attempts: int
    outbound_topic: str | None
    outbound_message_id: str | None
    outbound_payload: dict[str, Any] | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DocumentOperationRecord":
        return cls(
            operation_id=row["operation_id"],
            idempotency_key=row["idempotency_key"],
            request_fingerprint=row["request_fingerprint"],
            document_id=row["document_id"],
            manifest_version=row["manifest_version"],
            capability=row["capability"],
            normalized_payload=json.loads(row["normalized_payload"]),
            status=DocumentOperationStatus(row["status"]),
            attempt_count=row["attempt_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            result=_load_optional_json(row["result_json"]),
            error=_load_optional_json(row["error_json"]),
            request_metadata=json.loads(row["metadata_json"]),
            publication_state=PublicationState(row["publication_state"]),
            publication_attempts=row["publication_attempts"],
            outbound_topic=row["outbound_topic"],
            outbound_message_id=row["outbound_message_id"],
            outbound_payload=_load_optional_json(row["outbound_payload_json"]),
        )


@dataclass
class DocumentOperationClaim:
    record: DocumentOperationRecord
    owner: bool


class SQLiteDocumentOperationStore:
    """Transactional claims and a document-specific publication outbox."""

    def __init__(
        self,
        path: str | Path = "./data/document_operations.sqlite3",
        *,
        claim_lease_seconds: float = 60.0,
    ) -> None:
        self.path = Path(path)
        self.claim_lease_seconds = claim_lease_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_sync()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_sync(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS document_operations (
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
                CREATE INDEX IF NOT EXISTS document_operations_pending_idx
                ON document_operations(publication_state, updated_at)
                """
            )

    async def claim(
        self,
        *,
        operation_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        document_id: str,
        manifest_version: str,
        capability: str,
        normalized_payload: Mapping[str, Any],
        request_metadata: Mapping[str, Any],
    ) -> DocumentOperationClaim:
        return await asyncio.to_thread(
            self._claim_sync,
            operation_id,
            idempotency_key,
            request_fingerprint,
            document_id,
            manifest_version,
            capability,
            dict(normalized_payload),
            dict(request_metadata),
        )

    def _claim_sync(
        self,
        operation_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        document_id: str,
        manifest_version: str,
        capability: str,
        normalized_payload: dict[str, Any],
        request_metadata: dict[str, Any],
    ) -> DocumentOperationClaim:
        now = _utc_now()
        lease_until = _utc_after(self.claim_lease_seconds)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM document_operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
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
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL, NULL, ?, ?, 0, NULL, NULL, NULL, ?)
                    """,
                    (
                        operation_id,
                        idempotency_key,
                        request_fingerprint,
                        document_id,
                        manifest_version,
                        capability,
                        canonical_json(normalized_payload),
                        DocumentOperationStatus.PROCESSING.value,
                        now,
                        now,
                        canonical_json(request_metadata),
                        PublicationState.NONE.value,
                        lease_until,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM document_operations WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                connection.commit()
                return DocumentOperationClaim(DocumentOperationRecord.from_row(row), True)

            record = DocumentOperationRecord.from_row(row)
            if record.request_fingerprint != request_fingerprint:
                raise IdempotencyConflictError()
            if record.status in {
                DocumentOperationStatus.SUCCEEDED,
                DocumentOperationStatus.TERMINAL_FAILURE,
                DocumentOperationStatus.DEAD_LETTERED,
            }:
                connection.commit()
                return DocumentOperationClaim(record, False)
            row_lease = row["lease_until"]
            if (
                record.status is DocumentOperationStatus.PROCESSING
                and row_lease is not None
                and row_lease > now
            ):
                connection.commit()
                return DocumentOperationClaim(record, False)

            connection.execute(
                """
                UPDATE document_operations
                SET status = ?, attempt_count = attempt_count + 1,
                    updated_at = ?, lease_until = ?, error_json = NULL
                WHERE idempotency_key = ?
                """,
                (
                    DocumentOperationStatus.PROCESSING.value,
                    now,
                    lease_until,
                    idempotency_key,
                ),
            )
            row = connection.execute(
                "SELECT * FROM document_operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            connection.commit()
            return DocumentOperationClaim(DocumentOperationRecord.from_row(row), True)
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    async def get(self, idempotency_key: str) -> DocumentOperationRecord | None:
        return await asyncio.to_thread(self._get_sync, idempotency_key)

    def _get_sync(self, idempotency_key: str) -> DocumentOperationRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM document_operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return DocumentOperationRecord.from_row(row) if row is not None else None

    async def finish(
        self,
        idempotency_key: str,
        *,
        status: DocumentOperationStatus,
        result: Mapping[str, Any] | None,
        error: Mapping[str, Any] | None,
        outbound_topic: str,
        outbound_message_id: str,
        outbound_payload: Mapping[str, Any],
    ) -> DocumentOperationRecord:
        return await asyncio.to_thread(
            self._finish_sync,
            idempotency_key,
            status,
            dict(result) if result is not None else None,
            dict(error) if error is not None else None,
            outbound_topic,
            outbound_message_id,
            dict(outbound_payload),
        )

    def _finish_sync(
        self,
        idempotency_key: str,
        status: DocumentOperationStatus,
        result: dict[str, Any] | None,
        error: dict[str, Any] | None,
        outbound_topic: str,
        outbound_message_id: str,
        outbound_payload: dict[str, Any],
    ) -> DocumentOperationRecord:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM document_operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise KeyError(idempotency_key)
            existing = DocumentOperationRecord.from_row(row)
            if existing.status is DocumentOperationStatus.SUCCEEDED:
                connection.commit()
                return existing
            connection.execute(
                """
                UPDATE document_operations
                SET status = ?, result_json = ?, error_json = ?,
                    updated_at = ?, lease_until = NULL,
                    publication_state = ?, outbound_topic = ?,
                    outbound_message_id = ?, outbound_payload_json = ?
                WHERE idempotency_key = ?
                """,
                (
                    status.value,
                    _dump_optional_json(result),
                    _dump_optional_json(error),
                    _utc_now(),
                    PublicationState.PENDING.value,
                    outbound_topic,
                    outbound_message_id,
                    canonical_json(outbound_payload),
                    idempotency_key,
                ),
            )
            row = connection.execute(
                "SELECT * FROM document_operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            connection.commit()
            return DocumentOperationRecord.from_row(row)
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    async def record_retryable_failure(
        self,
        idempotency_key: str,
        error: Mapping[str, Any],
    ) -> DocumentOperationRecord:
        return await asyncio.to_thread(
            self._record_retryable_sync,
            idempotency_key,
            dict(error),
        )

    def _record_retryable_sync(
        self,
        idempotency_key: str,
        error: dict[str, Any],
    ) -> DocumentOperationRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM document_operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                raise KeyError(idempotency_key)
            if row["status"] != DocumentOperationStatus.SUCCEEDED.value:
                connection.execute(
                    """
                    UPDATE document_operations
                    SET status = ?, error_json = ?, updated_at = ?, lease_until = NULL
                    WHERE idempotency_key = ?
                    """,
                    (
                        DocumentOperationStatus.RETRYABLE_FAILURE.value,
                        canonical_json(error),
                        _utc_now(),
                        idempotency_key,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM document_operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return DocumentOperationRecord.from_row(row)

    async def mark_published(self, idempotency_key: str) -> DocumentOperationRecord:
        return await asyncio.to_thread(self._publication_update_sync, idempotency_key, True)

    async def increment_publication_attempt(
        self,
        idempotency_key: str,
    ) -> DocumentOperationRecord:
        return await asyncio.to_thread(self._publication_update_sync, idempotency_key, False)

    def _publication_update_sync(
        self,
        idempotency_key: str,
        published: bool,
    ) -> DocumentOperationRecord:
        assignment = (
            "publication_state = ?"
            if published
            else "publication_attempts = publication_attempts + 1"
        )
        parameters: tuple[Any, ...]
        if published:
            parameters = (PublicationState.PUBLISHED.value, _utc_now(), idempotency_key)
        else:
            parameters = (_utc_now(), idempotency_key)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE document_operations SET {assignment}, updated_at = ? WHERE idempotency_key = ?",
                parameters,
            )
            row = connection.execute(
                "SELECT * FROM document_operations WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            raise KeyError(idempotency_key)
        return DocumentOperationRecord.from_row(row)

    async def pending_publications(self, limit: int = 100) -> list[DocumentOperationRecord]:
        return await asyncio.to_thread(self._pending_sync, limit)

    def _pending_sync(self, limit: int) -> list[DocumentOperationRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM document_operations
                WHERE publication_state = ?
                ORDER BY updated_at, operation_id
                LIMIT ?
                """,
                (PublicationState.PENDING.value, max(limit, 0)),
            ).fetchall()
        return [DocumentOperationRecord.from_row(row) for row in rows]


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _dump_optional_json(value: Mapping[str, Any] | None) -> str | None:
    return canonical_json(value) if value is not None else None


def _load_optional_json(value: str | None) -> dict[str, Any] | None:
    return json.loads(value) if value is not None else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_after(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(seconds, 0.0))).isoformat()
