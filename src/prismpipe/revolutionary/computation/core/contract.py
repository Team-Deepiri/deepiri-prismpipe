"""Computation contract - specification for safe execution."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComputationContract:
    """
    Contract specifying what a computation can and cannot do.
    
    Used for sandboxing and security.
    """
    allowed_modules: list[str] = field(default_factory=list)
    blocked_modules: list[str] = field(default_factory=list)
    allowed_builtins: list[str] = field(default_factory=list)
    blocked_builtins: list[str] = field(default_factory=list)
    max_memory_mb: int = 128
    max_execution_time_ms: int = 5000
    max_output_size_bytes: int = 1024 * 1024
    allow_network: bool = False
    allow_filesystem: bool = False
    allowed_paths: list[str] = field(default_factory=list)
    env_vars: dict[str, str] = field(default_factory=dict)
    
    def is_module_allowed(self, module: str) -> bool:
        """Check if module is allowed."""
        if module in self.blocked_modules:
            return False
        if self.allowed_modules:
            return any(module.startswith(allowed) for allowed in self.allowed_modules)
        return True
    
    def is_builtin_allowed(self, builtin: str) -> bool:
        """Check if builtin is allowed."""
        if builtin in self.blocked_builtins:
            return False
        if self.allowed_builtins:
            return builtin in self.allowed_builtins
        return True


# Default contract for safe execution
DEFAULT_CONTRACT = ComputationContract(
    allowed_modules=[
        "json",
        "math",
        "random",
        "re",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "operator",
    ],
    allowed_builtins=[
        "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple",
        "range", "enumerate", "zip", "map", "filter", "sorted", "reversed",
        "sum", "min", "max", "abs", "round", "pow", "divmod",
        "isinstance", "issubclass", "hasattr", "getattr",
        "all", "any", "ord", "chr", "bin", "hex", "oct", "slice",
    ],
    max_memory_mb=128,
    max_execution_time_ms=5000,
    allow_network=False,
    allow_filesystem=False,
)
