"""Integration tests for RedisStreamsDeepiriTransport and the full
document.vectorize producer -> consumer -> CyrexVectorizer path, against a
real Redis instance.

Requires REDIS_TEST_URL (defaults to redis://localhost:16379/0, matching the
throwaway `prismpipe-redis-verify` container used for local verification).
Skips cleanly if Redis is unreachable.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid

import httpx
import pytest

from prismpipe.deepiri_bus import DeepiriStreamTopics
from prismpipe.document import CyrexVectorizer, DocumentVectorizeConsumer, DocumentVectorizeProcessor
from prismpipe.document.operations import DocumentOperationStatus, SQLiteDocumentOperationStore
from prismpipe.document.processing import build_document_idempotency_key
from prismpipe.document.vectorize import DocumentVectorizeInput
from prismpipe.redis_streams_transport import RedisStreamsDeepiriTransport

REDIS_TEST_URL = os.environ.get("REDIS_TEST_URL", "redis://localhost:16379/0")


@pytest.fixture()
async def redis_available():
    try:
        import redis.asyncio as redis
    except ImportError:
        pytest.skip("redis package not installed")
    client = redis.from_url(REDIS_TEST_URL, decode_responses=True)
    try:
        await client.ping()
    except Exception as exc:
        pytest.skip(f"Redis not reachable at {REDIS_TEST_URL!r}: {exc}")
    finally:
        await client.aclose()


def canonical_payload(document_id: str) -> dict:
    return {
        "routeId": f"route-{document_id}",
        "documentId": document_id,
        "manifestVersion": "1.0",
        "documentType": "lease",
        "destination": "vectorize",
        "qualityScore": 0.95,
        "document": {"documentId": document_id, "title": "Test", "mimeType": "text/plain"},
        "chunks": [
            {"chunkId": f"{document_id}-0", "index": 0, "text": "Tenant shall pay rent."},
        ],
        "storageReferences": [],
        "options": {"dimensions": None, "normalize": False, "metadata": {}},
    }


@pytest.mark.asyncio
async def test_publish_and_consume_round_trip(redis_available):
    topic = f"test.document.vectorize.{uuid.uuid4().hex[:8]}"
    transport = RedisStreamsDeepiriTransport(redis_url=REDIS_TEST_URL, consumer_group="test-group")
    await transport.start()
    try:
        await transport.publish(topic, {"hello": "world"}, message_id="msg-1")

        received = None
        async for message in transport.consume(topic):
            received = message
            break

        assert received is not None
        assert received.payload == {"hello": "world"}
        assert received.message_id == "msg-1"
        assert received.delivery_attempt == 1

        await transport.acknowledge(received)
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_retry_increments_delivery_attempt(redis_available):
    topic = f"test.document.vectorize.{uuid.uuid4().hex[:8]}"
    transport = RedisStreamsDeepiriTransport(redis_url=REDIS_TEST_URL, consumer_group="test-group")
    await transport.start()
    try:
        await transport.publish(topic, {"n": 1}, message_id="msg-retry")

        gen = transport.consume(topic)
        first = await gen.__anext__()
        assert first.delivery_attempt == 1
        await transport.retry(first)

        second = await gen.__anext__()
        assert second.message_id == "msg-retry"
        assert second.delivery_attempt == 2
        await transport.acknowledge(second)
    finally:
        await transport.stop()


@pytest.mark.asyncio
async def test_end_to_end_vectorize_pipeline_through_cyrex_mock(redis_available, tmp_path):
    """LIS-shaped payload -> Redis Streams -> DocumentVectorizeConsumer ->
    CyrexVectorizer (mocked HTTP) -> Milvus insert call -> completed record."""

    document_id = f"doc-{uuid.uuid4().hex[:8]}"
    topic = DeepiriStreamTopics.DOCUMENT_VECTORIZE.value
    payload = canonical_payload(document_id)
    message_id = f"msg-{document_id}"

    indexed_bodies: list[dict] = []

    def cyrex_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        indexed_bodies.append(body)
        return httpx.Response(
            200,
            json={
                "success": True,
                "document_id": body["document_id"],
                "dimensions": 3,
                "chunks": [
                    {"chunk_id": c["chunk_id"], "vector": [0.1, 0.2, 0.3]}
                    for c in body["chunks"]
                ],
            },
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(cyrex_handler))
    vectorizer = CyrexVectorizer(base_url="http://cyrex-test", client=mock_client)
    transport = RedisStreamsDeepiriTransport(
        redis_url=REDIS_TEST_URL, consumer_group=f"g-{uuid.uuid4().hex[:8]}"
    )
    operation_store = SQLiteDocumentOperationStore(path=tmp_path / "ops.sqlite3")
    processor = DocumentVectorizeProcessor(vectorizer=vectorizer, operation_store=operation_store)
    consumer = DocumentVectorizeConsumer(transport=transport, processor=processor)

    normalized = DocumentVectorizeInput.from_payload(payload).to_payload()
    idempotency_key = build_document_idempotency_key(normalized, message_id=message_id)

    await transport.start()
    try:
        # DocumentVectorizeConsumer always consumes the real, fixed
        # DOCUMENT_VECTORIZE topic (matching production), so a fresh consumer
        # group backfills the whole stream. Clear it first so this test only
        # sees its own message, regardless of what earlier runs left behind.
        await transport._client.delete(topic)
        await transport.publish(topic, payload, message_id=message_id)
        await consumer.start()

        record = None
        for _ in range(100):
            record = await operation_store.get(idempotency_key)
            if record is not None and record.status in (
                DocumentOperationStatus.SUCCEEDED,
                DocumentOperationStatus.TERMINAL_FAILURE,
            ):
                break
            await asyncio.sleep(0.05)

        assert record is not None, "document.vectorize message was never processed"
        assert record.status == DocumentOperationStatus.SUCCEEDED
        assert record.result is not None
        assert record.result["documentId"] == document_id
        assert record.result["dimensions"] == 3
        assert len(record.result["chunks"]) == 1
        assert record.result["chunks"][0]["vector"] == [0.1, 0.2, 0.3]

        # The chunk actually reached Cyrex (and, via CyrexVectorizer, Milvus).
        assert len(indexed_bodies) == 1
        assert indexed_bodies[0]["document_id"] == document_id
        assert indexed_bodies[0]["chunks"][0]["chunk_id"] == f"{document_id}-0"
    finally:
        await consumer.stop()
        await transport.stop()
        await mock_client.aclose()
