"""Computation payload and context."""

from dataclasses import dataclass, field
from typing import Any
from enum import Enum


class Language(str, Enum):
    """Supported computation languages."""
    PYTHON = "python"
    WASM = "wasm"
    JAVASCRIPT = "javascript"


class IsolationLevel(str, Enum):
    """Computation isolation levels."""
    NONE = "none"
    PROCESS = "process"
    VM = "vm"
    CONTAINER = "container"


@dataclass
class ComputationPayload:
    """
    A payload containing executable computation.
    
    The request carries the computation, not just data.
    """
    code: str
    language: Language = Language.PYTHON
    args: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 5000
    memory_limit_mb: int = 128
    isolation: IsolationLevel = IsolationLevel.PROCESS
    dependencies: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionContext:
    """
    Context for computation execution.
    
    Provides access to envelope state and capabilities.
    """
    request_id: str
    tenant_id: str | None = None
    state: dict[str, Any] = field(default_factory=dict)
    input_data: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    env_vars: dict[str, str] = field(default_factory=dict)
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get value from state."""
        return self.state.get(key, default)
    
    def set(self, key: str, value: Any) -> None:
        """Set value in state."""
        self.state[key] = value
    
    def call_capability(self, name: str, **kwargs: Any) -> Any:
        """Call a capability."""
        if name in self.capabilities:
            return self.capabilities[name](**kwargs)
        raise ValueError(f"Capability not found: {name}")
