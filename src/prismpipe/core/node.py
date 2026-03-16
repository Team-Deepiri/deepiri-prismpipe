"""Core Node - Base class for all capability nodes."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from prismpipe.core.envelope import RequestEnvelope

T = TypeVar("T")


@dataclass
class NodeResult(Generic[T]):
    envelope: RequestEnvelope[T]
    success: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Node(ABC, Generic[T]):
    """Base class for all capability nodes."""

    capability: str = ""
    version: str = "1.0.0"
    description: str = ""
    timeout_ms: int | None = None

    def __init__(self) -> None:
        self._id = f"{self.__class__.__name__}_{id(self)}"

    @property
    def id(self) -> str:
        return self._id

    @property
    def node_type(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def process(self, envelope: RequestEnvelope[T]) -> NodeResult[T]:
        ...

    def execute(self, envelope: RequestEnvelope[T]) -> NodeResult[T]:
        start_time = time.perf_counter()
        try:
            result = self.process(envelope)
            duration_ms = (time.perf_counter() - start_time) * 1000

            result.envelope.record(
                node_id=self.id,
                node_type=self.node_type,
                capability=self.capability,
                action=self._get_action_description(result),
                duration_ms=duration_ms,
            )

            if not result.success:
                result.envelope.error = result.error

            return result

        except Exception as e:
            duration_ms = (time.perf_counter() - start_time) * 1000
            envelope.record(
                node_id=self.id,
                node_type=self.node_type,
                capability=self.capability,
                action=f"error: {type(e).__name__}",
                duration_ms=duration_ms,
            )
            envelope.terminate(str(e))
            return NodeResult(
                envelope=envelope,
                success=False,
                error=str(e),
            )

    def _get_action_description(self, result: NodeResult) -> str:
        if not result.success:
            return f"failed: {result.error}"
        return result.metadata.get("action", "completed")

    def can_handle(self, capability: str) -> bool:
        return self.capability == capability


class TransformNode(Node[T]):
    """Base class for nodes that transform input data."""

    def process(self, envelope: RequestEnvelope[T]) -> NodeResult[T]:
        transformed = self.transform(envelope.input, envelope.state)
        envelope.state["transformed"] = transformed
        return NodeResult(envelope=envelope)

    @abstractmethod
    def transform(self, input_data: T, state: dict[str, Any]) -> Any:
        ...


class EnrichmentNode(Node[T]):
    """Base class for nodes that enrich state."""

    def process(self, envelope: RequestEnvelope[T]) -> NodeResult[T]:
        enrichment = self.enrich(envelope.input, envelope.state)
        envelope.state.update(enrichment)
        return NodeResult(envelope=envelope)

    @abstractmethod
    def enrich(self, input_data: T, state: dict[str, Any]) -> dict[str, Any]:
        ...


class ValidationNode(Node[T]):
    """Base class for nodes that validate requests."""

    def process(self, envelope: RequestEnvelope[T]) -> NodeResult[T]:
        errors = self.validate(envelope.input, envelope.state)
        if errors:
            envelope.terminate(f"Validation failed: {', '.join(errors)}")
            return NodeResult(envelope=envelope, success=False, error=", ".join(errors))
        envelope.state["validation_passed"] = True
        return NodeResult(envelope=envelope)

    @abstractmethod
    def validate(self, input_data: T, state: dict[str, Any]) -> list[str]:
        ...
