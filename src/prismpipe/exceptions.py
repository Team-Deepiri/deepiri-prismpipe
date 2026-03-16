"""PrismPipe exceptions."""

from typing import Any


class PrismPipeError(Exception):
    """Base exception for all PrismPipe errors."""

    code: str = "PRISM_ERROR"
    details: dict[str, Any] | None = None

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.code,
            "message": self.message,
            "details": self.details,
        }


class NodeNotFoundError(PrismPipeError):
    """Raised when a capability is not found in the registry."""

    code = "NODE_NOT_FOUND"

    def __init__(self, capability: str):
        super().__init__(
            f"Capability '{capability}' not found",
            {"capability": capability}
        )
        self.capability = capability


class NodeExecutionError(PrismPipeError):
    """Raised when a node fails during execution."""

    code = "NODE_EXECUTION_ERROR"

    def __init__(self, node_name: str, message: str, cause: Exception | None = None):
        super().__init__(
            f"Node '{node_name}' failed: {message}",
            {"node": node_name, "cause": str(cause)} if cause else {"node": node_name}
        )
        self.node_name = node_name
        self.cause = cause


class CapabilityAccessDenied(PrismPipeError):
    """Raised when access to a capability is denied."""

    code = "ACCESS_DENIED"

    def __init__(self, capability: str, reason: str = "Unauthorized"):
        super().__init__(
            f"Access denied to capability '{capability}': {reason}",
            {"capability": capability, "reason": reason}
        )
        self.capability = capability
        self.reason = reason


class RequestTimeoutError(PrismPipeError):
    """Raised when a request times out."""

    code = "REQUEST_TIMEOUT"

    def __init__(self, request_id: str, timeout_seconds: float):
        super().__init__(
            f"Request '{request_id}' timed out after {timeout_seconds}s",
            {"request_id": request_id, "timeout_seconds": timeout_seconds}
        )
        self.request_id = request_id
        self.timeout_seconds = timeout_seconds


class CircuitOpenError(PrismPipeError):
    """Raised when circuit breaker is open."""

    code = "CIRCUIT_OPEN"

    def __init__(self, capability: str):
        super().__init__(
            f"Circuit breaker is open for capability '{capability}'",
            {"capability": capability, "retry_after": 60}
        )
        self.capability = capability


class RateLimitError(PrismPipeError):
    """Raised when rate limit is exceeded."""

    code = "RATE_LIMIT_EXCEEDED"

    def __init__(self, capability: str, limit: float):
        super().__init__(
            f"Rate limit exceeded for capability '{capability}'",
            {"capability": capability, "limit": limit}
        )
        self.capability = capability
        self.limit = limit


class ValidationError(PrismPipeError):
    """Raised when request validation fails."""

    code = "VALIDATION_ERROR"

    def __init__(self, errors: list[dict[str, Any]]):
        super().__init__(
            "Request validation failed",
            {"errors": errors}
        )
        self.errors = errors


class StorageError(PrismPipeError):
    """Raised when storage operations fail."""

    code = "STORAGE_ERROR"

    def __init__(self, operation: str, reason: str):
        super().__init__(
            f"Storage operation '{operation}' failed: {reason}",
            {"operation": operation, "reason": reason}
        )
        self.operation = operation
        self.reason = reason
