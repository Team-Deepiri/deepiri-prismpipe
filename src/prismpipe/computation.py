"""PrismPipe Computation Engine - Execute code within envelopes."""

import ast
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Callable
from enum import Enum

from prismpipe.exceptions import PrismPipeError


class ExecutionMode(str, Enum):
    """Execution mode for payload."""
    SAFE = "safe"      # Restricted Python eval
    WASM = "wasm"      # WebAssembly
    NATIVE = "native"  # Direct execution (dangerous)


@dataclass
class ComputationPayload:
    """Payload containing executable code."""
    code: str
    language: str = "python"
    args: dict[str, Any] = field(default_factory=dict)
    timeout_ms: int = 5000


@dataclass
class ExecutionResult:
    """Result of payload execution."""
    success: bool
    result: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    confidence: float = 1.0


class ComputationError(PrismPipeError):
    """Error during computation."""
    code = "COMPUTATION_ERROR"


class SandboxViolationError(PrismPipeError):
    """Sandbox security violation."""
    code = "SANDBOX_VIOLATION"


class ComputationEngine:
    """
    Execute code within request envelopes.
    
    The request carries computation, not just data.
    """

    # Forbidden AST nodes
    FORBIDDEN_NODES = {
        'Assign', 'AugAssign', 'AnnAssign',  # Assignments
        'Import', 'ImportFrom',                # Imports
        'Global', 'Nonlocal',                  # Scope
        'FunctionDef', 'ClassDef',             # Definitions
        'Raise', 'Try', 'With', 'WithItem',   # Control flow
        'Delete',                              # Deletion
    }

    # Allowed builtins
    ALLOWED_BUILTINS = {
        'len', 'str', 'int', 'float', 'bool', 'list', 'dict', 'set', 'tuple',
        'range', 'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed',
        'sum', 'min', 'max', 'abs', 'round', 'pow', 'divmod',
        'isinstance', 'issubclass', 'hasattr', 'getattr', 'setattr',
        'all', 'any', 'ord', 'chr', 'bin', 'hex', 'oct', 'slice',
    }

    def __init__(self, mode: ExecutionMode = ExecutionMode.SAFE):
        self.mode = mode

    def validate_code(self, code: str) -> bool:
        """Validate code for safety."""
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if type(node).__name__ in self.FORBIDDEN_NODES:
                    raise SandboxViolationError(
                        f"Forbidden operation: {type(node).__name__}"
                    )
            return True
        except SyntaxError as e:
            raise ComputationError(f"Invalid syntax: {e}")

    def execute_payload(self, payload: ComputationPayload, context: dict[str, Any]) -> ExecutionResult:
        """Execute a computation payload within context."""
        import time
        start = time.perf_counter()

        try:
            if payload.language == "python":
                if self.mode == ExecutionMode.SAFE:
                    return self._execute_safe(payload, context, start)
                else:
                    return self._execute_native(payload, context, start)
            elif payload.language == "wasm":
                return self._execute_wasm(payload, context, start)
            else:
                return ExecutionResult(
                    success=False,
                    error=f"Unsupported language: {payload.language}"
                )
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            return ExecutionResult(
                success=False,
                error=str(e),
                duration_ms=duration
            )

    def _execute_safe(
        self,
        payload: ComputationPayload,
        context: dict[str, Any],
        start: float
    ) -> ExecutionResult:
        """Execute in safe mode with restricted eval."""
        self.validate_code(payload.code)

        # Create safe globals
        safe_globals = {
            '__builtins__': {k: __builtins__[k] for k in self.ALLOWED_BUILTINS if k in __builtins__},
            '_context': context,
            '_args': payload.args,
        }

        # Wrap code to access context
        wrapped_code = f"""
_code = {repr(payload.code)}
_context = _context
_args = _args
_result = eval(_code, {{'_context': _context, '_args': _args, 'len': len, 'str': str, 'int': int, 'float': float, 'bool': bool, 'list': list, 'dict': dict, 'set': set, 'tuple': tuple, 'range': range, 'enumerate': enumerate, 'zip': zip, 'map': map, 'filter': filter, 'sorted': sorted, 'reversed': reversed, 'sum': sum, 'min': min, 'max': max, 'abs': abs, 'round': round, 'pow': pow, 'divmod': divmod, 'isinstance': isinstance, 'issubclass': issubclass, 'hasattr': hasattr, 'getattr': getattr, 'all': all, 'any': any, 'ord': ord, 'chr': chr, 'bin': bin, 'hex': hex, 'oct': oct, 'slice': slice}}, {{}})
"""

        result = eval(wrapped_code, safe_globals)
        duration = (time.perf_counter() - start) * 1000

        return ExecutionResult(
            success=True,
            result=result,
            duration_ms=duration
        )

    def _execute_native(
        self,
        payload: ComputationPayload,
        context: dict[str, Any],
        start: float
    ) -> ExecutionResult:
        """Execute without restrictions (DANGER)."""
        local_ns = {**context, **payload.args}
        try:
            exec(payload.code, {'__builtins__': __builtins__}, local_ns)
            result = local_ns.get('_result')
            duration = (time.perf_counter() - start) * 1000
            return ExecutionResult(
                success=True,
                result=result,
                duration_ms=duration
            )
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            return ExecutionResult(
                success=False,
                error=str(e),
                duration_ms=duration
            )

    def _execute_wasm(
        self,
        payload: ComputationPayload,
        context: dict[str, Any],
        start: float
    ) -> ExecutionResult:
        """Execute WASM module (placeholder)."""
        return ExecutionResult(
            success=False,
            error="WASM execution not yet implemented"
        )


# Global engine instance
_default_engine: ComputationEngine | None = None


def get_computation_engine(mode: ExecutionMode = ExecutionMode.SAFE) -> ComputationEngine:
    """Get default computation engine."""
    global _default_engine
    if _default_engine is None:
        _default_engine = ComputationEngine(mode)
    return _default_engine


def set_computation_engine(engine: ComputationEngine) -> None:
    """Set default computation engine."""
    global _default_engine
    _default_engine = engine


# Convenience function
def execute_code(code: str, context: dict[str, Any] | None = None, **kwargs: Any) -> ExecutionResult:
    """Execute code in a computation payload."""
    engine = get_computation_engine()
    payload = ComputationPayload(code=code, args=kwargs)
    return engine.execute_payload(payload, context or {})
