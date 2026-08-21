"""Redis Streams implementation of DeepiriTransport.

Production broker adapter for the DeepiriTransport protocol
(prismpipe.deepiri_bus), using consumer groups (XADD/XREADGROUP/XACK) so
delivery matches the semantics DocumentVectorizeConsumer expects: at-least-
once, per-message acknowledgement, and explicit retry via re-publish.

This is the concrete transport that lets document.vectorize (and the other
topics in DeepiriStreamTopics) actually flow over the shared Redis Streams
bus used by shared-utils' StreamingClient and deepiri-modelkit, rather than
only the InMemoryDeepiriTransport used in tests.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Mapping
from typing import Any

from prismpipe.deepiri_bus import DeepiriMessage, DeepiriTransportError

_PAYLOAD_FIELD = "payload"
_HEADERS_FIELD = "headers"
_DELIVERY_ATTEMPT_FIELD = "delivery_attempt"


class RedisStreamsDeepiriTransport:
    """DeepiriTransport backed by Redis Streams consumer groups."""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        consumer_group: str = "prismpipe",
        consumer_name: str | None = None,
        block_ms: int = 5000,
        claim_idle_ms: int = 60_000,
    ) -> None:
        self._redis_url = redis_url or os.environ.get(
            "REDIS_URL", "redis://localhost:6379/0"
        )
        self._consumer_group = consumer_group
        self._consumer_name = consumer_name or f"{consumer_group}-{os.getpid()}"
        self._block_ms = block_ms
        self._claim_idle_ms = claim_idle_ms
        self._client: Any | None = None
        self._connected = False
        self._known_groups: set[str] = set()

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        try:
            import redis.asyncio as redis
        except ImportError as exc:
            raise DeepiriTransportError(
                "redis package required for RedisStreamsDeepiriTransport. "
                "pip install 'prismpipe[redis]'"
            ) from exc
        self._client = redis.from_url(self._redis_url, decode_responses=True)
        await self._client.ping()
        self._connected = True

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._connected = False

    async def _ensure_group(self, topic: str) -> None:
        assert self._client is not None
        if topic in self._known_groups:
            return
        try:
            await self._client.xgroup_create(
                name=topic, groupname=self._consumer_group, id="0", mkstream=True
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise DeepiriTransportError(
                    f"Failed to create consumer group for {topic!r}: {exc}"
                ) from exc
        self._known_groups.add(topic)

    async def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        message_id: str,
        headers: Mapping[str, Any] | None = None,
        delivery_attempt: int = 1,
    ) -> None:
        if not self._connected or self._client is None:
            raise DeepiriTransportError("Deepiri transport is disconnected")
        try:
            await self._client.xadd(
                topic,
                {
                    "message_id": message_id,
                    _PAYLOAD_FIELD: json.dumps(dict(payload)),
                    _HEADERS_FIELD: json.dumps(dict(headers or {})),
                    _DELIVERY_ATTEMPT_FIELD: str(delivery_attempt),
                },
            )
        except Exception as exc:
            raise DeepiriTransportError(f"Publish to {topic!r} failed: {exc}") from exc

    async def consume(self, topic: str) -> AsyncIterator[DeepiriMessage]:
        if self._client is None:
            raise DeepiriTransportError("Deepiri transport is not started")
        await self._ensure_group(topic)
        while self._connected:
            try:
                response = await self._client.xreadgroup(
                    groupname=self._consumer_group,
                    consumername=self._consumer_name,
                    streams={topic: ">"},
                    count=1,
                    block=self._block_ms,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise DeepiriTransportError(f"Consume from {topic!r} failed: {exc}") from exc

            if not response:
                continue

            for _stream, entries in response:
                for entry_id, fields in entries:
                    yield DeepiriMessage(
                        message_id=fields.get("message_id", entry_id),
                        payload=json.loads(fields.get(_PAYLOAD_FIELD, "{}")),
                        headers=json.loads(fields.get(_HEADERS_FIELD, "{}")),
                        delivery_attempt=int(fields.get(_DELIVERY_ATTEMPT_FIELD, "1")),
                        topic=topic,
                    )
                    self._entry_ids = getattr(self, "_entry_ids", {})
                    self._entry_ids[fields.get("message_id", entry_id)] = (topic, entry_id)

    async def acknowledge(self, message: DeepiriMessage) -> None:
        if not self._connected or self._client is None:
            raise DeepiriTransportError("Deepiri transport is disconnected")
        entry = getattr(self, "_entry_ids", {}).get(message.message_id)
        if entry is None:
            return
        topic, entry_id = entry
        await self._client.xack(topic, self._consumer_group, entry_id)

    async def retry(self, message: DeepiriMessage) -> None:
        if not self._connected or self._client is None:
            raise DeepiriTransportError("Deepiri transport is disconnected")
        topic = message.topic
        if topic is None:
            raise DeepiriTransportError("Cannot retry a message with no topic")
        await self.publish(
            topic,
            message.payload,
            message_id=message.message_id,
            headers=message.headers,
            delivery_attempt=message.delivery_attempt + 1,
        )
        entry = getattr(self, "_entry_ids", {}).get(message.message_id)
        if entry is not None:
            _, entry_id = entry
            await self._client.xack(topic, self._consumer_group, entry_id)
