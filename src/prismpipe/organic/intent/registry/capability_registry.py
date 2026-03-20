"""Capability registry for storing capability metadata."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CapabilityMetadata:
    """Metadata for a single capability."""
    name: str
    description: str
    version: str = "1.0.0"
    parameters: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


class CapabilityRegistry:
    """Registry for storing and retrieving capability metadata."""

    def __init__(self):
        self._capabilities: dict[str, CapabilityMetadata] = {}

    def register(self, capability: CapabilityMetadata) -> None:
        """Register a capability."""
        self._capabilities[capability.name] = capability

    def get(self, name: str) -> CapabilityMetadata | None:
        """Get a capability by name."""
        return self._capabilities.get(name)

    def list_all(self) -> list[CapabilityMetadata]:
        """List all registered capabilities."""
        return list(self._capabilities.values())

    def find_by_tag(self, tag: str) -> list[CapabilityMetadata]:
        """Find capabilities by tag."""
        return [c for c in self._capabilities.values() if tag in c.tags]

    def unregister(self, name: str) -> bool:
        """Unregister a capability."""
        if name in self._capabilities:
            del self._capabilities[name]
            return True
        return False