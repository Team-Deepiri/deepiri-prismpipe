"""PrismPipe Partial Knowledge - Early responses with confidence."""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from enum import Enum

from prismpipe.core import RequestEnvelope


class ConfidenceLevel(float, Enum):
    """Confidence levels for partial results."""
    HIGH = 0.9
    MEDIUM = 0.7
    LOW = 0.5
    GUESS = 0.3


@dataclass
class PartialResult:
    """A partial result with confidence."""
    data: Any
    confidence: float
    partial: bool = True
    background_task_id: str | None = None
    is_final: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BackgroundTask:
    """Track a background computation task."""
    id: str
    request_id: str
    status: str = "running"  # running, completed, failed
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    completed_at: float | None = None


class PartialKnowledgeEngine:
    """
    Enable partial knowledge responses.
    
    Respond immediately with confidence, continue processing in background.
    """

    def __init__(self, min_confidence: float = 0.7):
        self.min_confidence = min_confidence
        self._background_tasks: dict[str, BackgroundTask] = {}
        self._task_handlers: dict[str, Callable[..., Awaitable[Any]]] = {}

    async def process_with_partial(
        self,
        envelope: RequestEnvelope,
        process_fn: Callable[[RequestEnvelope], Awaitable[Any]],
        accept_partial: bool = True,
        min_confidence: float | None = None
    ) -> PartialResult:
        """
        Process request with partial knowledge support.
        
        Returns early if confidence threshold is met,
        otherwise waits for full result.
        """
        min_conf = min_confidence or self.min_confidence

        if not accept_partial:
            # Full processing
            result = await process_fn(envelope)
            return PartialResult(
                data=result,
                confidence=1.0,
                partial=False,
                is_final=True
            )

        # Try quick path first
        quick_result = await self._quick_path(envelope)
        if quick_result and quick_result.confidence >= min_conf:
            return quick_result

        # Full processing
        full_result = await process_fn(envelope)

        return PartialResult(
            data=full_result,
            confidence=1.0,
            partial=False,
            is_final=True
        )

    async def _quick_path(self, envelope: RequestEnvelope) -> PartialResult | None:
        """Try a quick/cached path for early response."""
        # Check cache first
        cache_key = self._get_cache_key(envelope)
        if cache_key in self._background_tasks:
            task = self._background_tasks[cache_key]
            if task.status == "completed":
                return PartialResult(
                    data=task.result,
                    confidence=0.85,
                    partial=True,
                    is_final=True
                )

        # Could implement heuristic estimation here
        # For now, return None to trigger full processing
        return None

    def _get_cache_key(self, envelope: RequestEnvelope) -> str:
        """Generate cache key for envelope."""
        return f"{envelope.intent}:{json.dumps(envelope.input, sort_keys=True)}"

    async def start_background(
        self,
        request_id: str,
        process_fn: Callable[[RequestEnvelope], Awaitable[Any]],
        envelope: RequestEnvelope
    ) -> str:
        """Start background processing for later retrieval."""
        task_id = str(uuid.uuid4())
        
        task = BackgroundTask(
            id=task_id,
            request_id=request_id
        )
        self._background_tasks[task_id] = task

        # Run in background
        asyncio.create_task(self._run_background(task, process_fn, envelope))

        return task_id

    async def _run_background(
        self,
        task: BackgroundTask,
        process_fn: Callable[[RequestEnvelope], Awaitable[Any]],
        envelope: RequestEnvelope
    ) -> None:
        """Run background task."""
        try:
            result = await process_fn(envelope)
            task.result = result
            task.status = "completed"
        except Exception as e:
            task.error = str(e)
            task.status = "failed"
        finally:
            task.completed_at = time.time()

    def get_background_result(self, task_id: str) -> BackgroundTask | None:
        """Get background task result."""
        return self._background_tasks.get(task_id)

    def estimate_confidence(
        self,
        envelope: RequestEnvelope,
        available_data: dict[str, Any]
    ) -> float:
        """Estimate confidence based on available data."""
        confidence = 0.5  # Base confidence

        # Boost for cached data
        if available_data.get('from_cache'):
            confidence += 0.2

        # Boost for validated data
        if available_data.get('validated'):
            confidence += 0.15

        # Boost based on data completeness
        expected_keys = envelope.metadata.custom.get('expected_keys', [])
        if expected_keys:
            available_keys = set(available_data.keys())
            completeness = len(available_keys & set(expected_keys)) / len(expected_keys)
            confidence = confidence * 0.5 + completeness * 0.5

        return min(1.0, confidence)


# Global instance
_default_partial_engine: PartialKnowledgeEngine | None = None


def get_partial_engine() -> PartialKnowledgeEngine:
    """Get default partial knowledge engine."""
    global _default_partial_engine
    if _default_partial_engine is None:
        _default_partial_engine = PartialKnowledgeEngine()
    return _default_partial_engine


def set_partial_engine(engine: PartialKnowledgeEngine) -> None:
    """Set default partial knowledge engine."""
    global _default_partial_engine
    _default_partial_engine = engine


# Helper for quick responses
def create_partial_response(
    data: Any,
    confidence: float,
    metadata: dict[str, Any] | None = None
) -> PartialResult:
    """Create a partial result response."""
    return PartialResult(
        data=data,
        confidence=confidence,
        partial=True,
        is_final=confidence >= 0.9,
        metadata=metadata or {}
    )
