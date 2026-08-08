"""
Deepiri platform bus topic constants for Prismpipe routing.

Aligns Prismpipe organism routing with Cyrex AGI / Helox / Sugar Glider stream
namespaces so routing bridges can publish/consume the same contracts.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Protocol


class DeepiriStreamTopics(str, Enum):
    """Canonical Redis Stream topics shared with ModelKit / shared-utils."""

    MODEL_EVENTS = "model-events"
    INFERENCE_EVENTS = "inference-events"
    PLATFORM_EVENTS = "platform-events"
    AGI_DECISIONS = "agi-decisions"
    TRAINING_EVENTS = "training-events"
    TRAINING_JOBS = "training-jobs"
    DOCUMENT_VECTORIZE = "document.vectorize"
    DOCUMENT_TRAINING = "document.training"
    DOCUMENT_STRUCTURED = "document.structured"
    DOCUMENT_ARTIFACTS = "document.artifacts"
    HELOX_TRAINING_RAW = "pipeline.helox-training.raw"
    HELOX_TRAINING_STRUCTURED = "pipeline.helox-training.structured"
    PIPELINE_PRESSURE_EVENTS = "pipeline.pressure.events"
    PIPELINE_ARTIFACT_INVALIDATION = "pipeline.artifact.invalidation"
    PIPELINE_SPLICE_EVENTS = "pipeline.splice.events"
    PIPELINE_DEAD_LETTER = "pipeline.dead-letter"
    PIPELINE_METRICS = "pipeline.metrics"

    @classmethod
    def all(cls) -> List[str]:
        return [t.value for t in cls]

    @classmethod
    def sugar_glider_allowlist(cls) -> List[str]:
        return cls.all()


# Prismpipe envelope kind → Deepiri bus topic (routing runtime bridge).
ENVELOPE_TO_STREAM: Dict[str, str] = {
    "train": DeepiriStreamTopics.TRAINING_JOBS.value,
    "training_progress": DeepiriStreamTopics.TRAINING_EVENTS.value,
    "model_ready": DeepiriStreamTopics.MODEL_EVENTS.value,
    "inference": DeepiriStreamTopics.INFERENCE_EVENTS.value,
    "pressure": DeepiriStreamTopics.PIPELINE_PRESSURE_EVENTS.value,
    "invalidation": DeepiriStreamTopics.PIPELINE_ARTIFACT_INVALIDATION.value,
    "splice": DeepiriStreamTopics.PIPELINE_SPLICE_EVENTS.value,
    "platform": DeepiriStreamTopics.PLATFORM_EVENTS.value,
    "agi": DeepiriStreamTopics.AGI_DECISIONS.value,
    "helox_raw": DeepiriStreamTopics.HELOX_TRAINING_RAW.value,
    "helox_structured": DeepiriStreamTopics.HELOX_TRAINING_STRUCTURED.value,
    "document_vectorize": DeepiriStreamTopics.DOCUMENT_VECTORIZE.value,
    "document_artifacts": DeepiriStreamTopics.DOCUMENT_ARTIFACTS.value,
    "dead_letter": DeepiriStreamTopics.PIPELINE_DEAD_LETTER.value,
}


def resolve_stream_for_envelope_kind(kind: str) -> str:
    """Map a Prismpipe envelope/routing kind onto a Deepiri stream name."""
    key = (kind or "").strip().lower()
    if key in ENVELOPE_TO_STREAM:
        return ENVELOPE_TO_STREAM[key]
    return DeepiriStreamTopics.PLATFORM_EVENTS.value

class DeepiriTransportError(RuntimeError):
    # Retryable failure at the broker transport boundary.

    pass


@dataclass
class DeepiriMessage:
    # Broker-neutral Deepiri message with stable delivery metadata.

    message_id: str
    payload: dict[str, Any]
    headers: dict[str, Any] = field(default_factory=dict)
    delivery_attempt: int = 1
    topic: str | None = None


class DeepiriTransport(Protocol):
    # Minimal managed transport boundary. A production broker adapter can implement it.

    @property
    def connected(self) -> bool:
        ...

    async def start(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    def consume(self, topic: str) -> AsyncIterator[DeepiriMessage]:
        ...

    async def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        message_id: str,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        ...

    async def acknowledge(self, message: DeepiriMessage) -> None:
        ...

    async def retry(self, message: DeepiriMessage) -> None:
        ...


class InMemoryDeepiriTransport:
    # Deterministic transport for tests and local embedding; not a production broker.

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[DeepiriMessage]] = defaultdict(asyncio.Queue)
        self._started = False
        self._connected = False
        self.published: list[tuple[str, DeepiriMessage]] = []
        self.acknowledged: list[str] = []
        self.retried: list[str] = []
        self.fail_publications = 0

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        self._started = True
        self._connected = True

    async def stop(self) -> None:
        self._started = False
        self._connected = False

    async def send(self, topic: str, message: DeepiriMessage) -> None:
        message.topic = topic
        await self._queues[topic].put(message)

    async def consume(self, topic: str) -> AsyncIterator[DeepiriMessage]:
        while self._started:
            try:
                message = await self._queues[topic].get()
            except asyncio.CancelledError:
                raise
            yield message

    async def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        message_id: str,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        if not self._connected:
            raise DeepiriTransportError("Deepiri transport is disconnected")
        if self.fail_publications > 0:
            self.fail_publications -= 1
            raise DeepiriTransportError("Injected publication failure")
        message = DeepiriMessage(
            message_id=message_id,
            payload=dict(payload),
            headers=dict(headers or {}),
            topic=topic,
        )
        self.published.append((topic, message))

    async def acknowledge(self, message: DeepiriMessage) -> None:
        if not self._connected:
            raise DeepiriTransportError("Deepiri transport is disconnected")
        if message.message_id not in self.acknowledged:
            self.acknowledged.append(message.message_id)

    async def retry(self, message: DeepiriMessage) -> None:
        if not self._connected:
            raise DeepiriTransportError("Deepiri transport is disconnected")
        self.retried.append(message.message_id)
        topic = message.topic or DeepiriStreamTopics.DOCUMENT_VECTORIZE.value
        await self._queues[topic].put(
            DeepiriMessage(
                message_id=message.message_id,
                payload=dict(message.payload),
                headers=dict(message.headers),
                delivery_attempt=message.delivery_attempt + 1,
                topic=topic,
            )
        )
