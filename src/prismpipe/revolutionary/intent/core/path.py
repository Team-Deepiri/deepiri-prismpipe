"""Capability path planning."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapabilityPath:
    """A planned path of capabilities to fulfill an intent."""
    capabilities: list[str]
    confidence: float = 1.0
    estimated_latency_ms: float = 0.0
    estimated_cost: float = 0.0
    fallback_paths: list[list[str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @property
    def path_key(self) -> str:
        """Get string key for path."""
        return " -> ".join(self.capabilities)
