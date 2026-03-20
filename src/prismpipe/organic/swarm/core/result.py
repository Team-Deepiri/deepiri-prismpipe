"""Swarm result for collecting task results."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SwarmResult:
    """Result from a swarm task."""
    task_id: str
    partition: int
    data: Any = None
    error: str | None = None
    success: bool = True
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "partition": self.partition,
            "data": self.data,
            "error": self.error,
            "success": self.success,
            "metadata": self.metadata,
        }