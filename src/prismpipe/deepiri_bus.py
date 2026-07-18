"""
Deepiri platform bus topic constants for Prismpipe routing.

Aligns Prismpipe organism routing with Cyrex AGI / Helox / Sugar Glider stream
namespaces so routing bridges can publish/consume the same contracts.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, List


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
}


def resolve_stream_for_envelope_kind(kind: str) -> str:
    """Map a Prismpipe envelope/routing kind onto a Deepiri stream name."""
    key = (kind or "").strip().lower()
    if key in ENVELOPE_TO_STREAM:
        return ENVELOPE_TO_STREAM[key]
    return DeepiriStreamTopics.PLATFORM_EVENTS.value
