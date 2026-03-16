"""Core: Request Envelope - The heart of PrismPipe."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Intent(str, Enum):
    HTTP_REQUEST = "http_request"
    WEBHOOK = "webhook"
    SCHEDULED_TASK = "scheduled_task"
    STREAM = "stream"
    BATCH = "batch"
    AI_TASK = "ai_task"
    CUSTOM = "custom"


class HistoryEntry(BaseModel):
    node_id: str
    node_type: str
    capability: str
    action: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float | None = None
    input_snapshot: dict[str, Any] | None = None
    output_snapshot: dict[str, Any] | None = None

    model_config = {"frozen": True}


class StateDiff(BaseModel):
    node_id: str
    capability: str
    added: dict[str, Any] = Field(default_factory=dict)
    modified: dict[str, tuple[Any, Any]] = Field(default_factory=dict)
    removed: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0


class Metadata(BaseModel):
    source: str | None = None
    correlation_id: str | None = None
    trace_id: str | None = None
    user_agent: str | None = None
    ip_address: str | None = None
    ttl: int | None = None
    priority: int = 0
    tags: dict[str, str] = Field(default_factory=dict)
    custom: dict[str, Any] = Field(default_factory=dict)


class ExecutionPlan(BaseModel):
    capabilities: list[str] = Field(default_factory=list)
    current_index: int = 0

    def add(self, capability: str) -> None:
        self.capabilities.append(capability)

    def insert(self, index: int, capability: str) -> None:
        self.capabilities.insert(index, capability)

    def remove(self, capability: str) -> None:
        if capability in self.capabilities:
            self.capabilities.remove(capability)

    def next(self) -> str | None:
        if self.current_index < len(self.capabilities):
            result = self.capabilities[self.current_index]
            self.current_index += 1
            return result
        return None

    def reset(self) -> None:
        self.current_index = 0


class RequestEnvelope(BaseModel):
    """The fundamental unit of computation in PrismPipe."""

    id: str = Field(default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}")
    intent: Intent = Intent.HTTP_REQUEST
    input: Any = {}
    state: dict[str, Any] = {}
    history: list[HistoryEntry] = []
    metadata: Metadata = Field(default_factory=Metadata)
    plan: ExecutionPlan = Field(default_factory=ExecutionPlan)
    next: str | None = None
    error: str | None = None
    terminated: bool = False
    parent_id: str | None = None

    model_config = {"frozen": False, "arbitrary_types_allowed": True}

    def record(
        self,
        node_id: str,
        node_type: str,
        capability: str,
        action: str,
        duration_ms: float | None = None,
    ) -> None:
        entry = HistoryEntry(
            node_id=node_id,
            node_type=node_type,
            capability=capability,
            action=action,
            duration_ms=duration_ms,
            input_snapshot=self.model_dump().get("state"),
        )
        self.history.append(entry)

    def set_next(self, capability: str | None) -> None:
        self.next = capability

    def terminate(self, error: str | None = None) -> None:
        self.terminated = True
        if error:
            self.error = error

    def get_capability(self) -> str | None:
        if not self.next:
            return None
        if self.next.startswith("capability:"):
            return self.next.split(":", 1)[1]
        return self.next

    @property
    def execution_time_ms(self) -> float | None:
        if not self.history:
            return None
        durations = [e.duration_ms for e in self.history if e.duration_ms is not None]
        return sum(durations) if durations else None

    @property
    def ancestry(self) -> list[str]:
        return [self.parent_id] if self.parent_id else []


def create_envelope(
    intent: Intent | str = Intent.HTTP_REQUEST,
    input_data: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
    next_capability: str | None = None,
    **metadata_kwargs: Any,
) -> RequestEnvelope:
    if isinstance(intent, str):
        intent = Intent(intent)

    return RequestEnvelope(
        intent=intent,
        input=input_data or {},
        state=state or {},
        next=next_capability,
        metadata=Metadata(**metadata_kwargs),
    )
