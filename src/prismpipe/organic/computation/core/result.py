"""Computation execution result."""

from dataclasses import dataclass, field
from typing import Any
from enum import Enum


class ExecutionStatus(str, Enum):
    """Status of computation execution."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    SANDBOX_VIOLATION = "sandbox_violation"


@dataclass
class ExecutionResult:
    """
    Result of computation execution.
    
    Contains the output, status, and metrics.
    """
    status: ExecutionStatus
    result: Any = None
    error: str | None = None
    error_type: str | None = None
    duration_ms: float = 0.0
    memory_used_mb: float = 0.0
    stdout: str = ""
    stderr: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @property
    def success(self) -> bool:
        """Check if execution was successful."""
        return self.status == ExecutionStatus.SUCCESS
    
    @property
    def failed(self) -> bool:
        """Check if execution failed."""
        return self.status in (ExecutionStatus.FAILED, ExecutionStatus.SANDBOX_VIOLATION)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "error_type": self.error_type,
            "duration_ms": self.duration_ms,
            "memory_used_mb": self.memory_used_mb,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "metadata": self.metadata,
        }
