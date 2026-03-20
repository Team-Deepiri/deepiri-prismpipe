"""Swarm envelope for distributed processing."""

from dataclasses import dataclass, field
from typing import Any
import uuid


@dataclass
class SwarmEnvelope:
    """Envelope for swarm tasks."""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    payload: Any = None
    partition: int = 0
    metadata: dict = field(default_factory=dict)
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "payload": self.payload,
            "partition": self.partition,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }