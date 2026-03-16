"""Capability Router - Routes envelopes to capability nodes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from prismpipe.core.node import Node


class NodeNotFoundError(Exception):
    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(f"No node registered for capability: {capability}")


@dataclass
class CapabilityRegistration:
    capability: str
    node: Node
    priority: int = 0
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


class CapabilityRouter:
    """Routes request envelopes to appropriate nodes based on capability."""

    def __init__(self) -> None:
        self._registrations: dict[str, CapabilityRegistration] = {}
        self._capability_aliases: dict[str, str] = {}

    def register(
        self,
        capability: str,
        node: Node,
        priority: int = 0,
        **metadata: Any,
    ) -> "CapabilityRouter":
        self._registrations[capability] = CapabilityRegistration(
            capability=capability,
            node=node,
            priority=priority,
            metadata=metadata,
        )
        return self

    def unregister(self, capability: str) -> bool:
        if capability in self._registrations:
            del self._registrations[capability]
            return True
        if capability in self._capability_aliases:
            del self._capability_aliases[capability]
            return True
        return False

    def alias(self, alias_name: str, capability: str) -> None:
        if capability not in self._registrations:
            raise ValueError(f"Cannot alias to unregistered capability: {capability}")
        self._capability_aliases[alias_name] = capability

    def resolve(self, capability: str) -> Node:
        resolved = self._resolve_capability(capability)
        if resolved not in self._registrations:
            raise NodeNotFoundError(capability)
        return self._registrations[resolved].node

    def resolve_registration(self, capability: str) -> CapabilityRegistration | None:
        resolved = self._resolve_capability(capability)
        return self._registrations.get(resolved)

    def _resolve_capability(self, capability: str) -> str:
        if capability in self._capability_aliases:
            return self._capability_aliases[capability]
        return capability

    def has_capability(self, capability: str) -> bool:
        resolved = self._resolve_capability(capability)
        return resolved in self._registrations

    def list_capabilities(self) -> list[str]:
        return list(self._registrations.keys())

    def get_node(self, capability: str) -> Node | None:
        try:
            return self.resolve(capability)
        except NodeNotFoundError:
            return None

    def __contains__(self, capability: str) -> bool:
        return self.has_capability(capability)

    def __getitem__(self, capability: str) -> Node:
        return self.resolve(capability)
