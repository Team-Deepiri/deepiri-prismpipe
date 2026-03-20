"""Request node for memory graph."""

from dataclasses import dataclass, field
from typing import Any
import uuid


@dataclass
class RequestNode:
    """A node representing a request in the memory graph."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str = ""
    prompt: str = ""
    intent_type: str = ""
    path: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "request_id": self.request_id,
            "prompt": self.prompt,
            "intent_type": self.intent_type,
            "path": self.path,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }