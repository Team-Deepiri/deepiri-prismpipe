"""Python runtime for computation execution."""

import ast
import time
from typing import Any

from prismpipe.organic.computation.core.payload import ComputationPayload, ExecutionContext, Language
from prismpipe.organic.computation.core.result import ExecutionResult, ExecutionStatus
from prismpipe.organic.computation.core.contract import ComputationContract, DEFAULT_CONTRACT
from prismpipe.organic.computation.runtime.base import Runtime


class PythonRuntime(Runtime):
    """
    Python runtime for executing computation payloads.
    
    Supports both sandboxed and native execution.
    """
    
    def __init__(self, contract: ComputationContract | None = None):
        self.contract = contract or DEFAULT_CONTRACT
    
    async def execute(
        self,
        payload: ComputationPayload,
        context: ExecutionContext
    ) -> ExecutionResult:
        """Execute Python code in the payload."""
        start_time = time.perf_counter()
        
        # Validate first
        is_valid, error = self.validate(payload)
        if not is_valid:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                error=error,
                error_type="ValidationError",
                duration_ms=(time.perf_counter() - start_time) * 1000
            )
        
        try:
            # Build globals for execution
            globals_dict = self._build_globals(context)
            locals_dict: dict[str, Any] = {}
            
            # Execute with timeout simulation
            exec(payload.code, globals_dict, locals_dict)
            
            # Get result
            result = locals_dict.get("_result")
            
            duration = (time.perf_counter() - start_time) * 1000
            
            return ExecutionResult(
                status=ExecutionStatus.SUCCESS,
                result=result,
                duration_ms=duration
            )
            
        except TimeoutError:
            return ExecutionResult(
                status=ExecutionStatus.TIMEOUT,
                error="Execution timed out",
                duration_ms=(time.perf_counter() - start_time) * 1000
            )
        except SecurityError as e:
            return ExecutionResult(
                status=ExecutionStatus.SANDBOX_VIOLATION,
                error=str(e),
                error_type="SecurityError",
                duration_ms=(time.perf_counter() - start_time) * 1000
            )
        except Exception as e:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=(time.perf_counter() - start_time) * 1000
            )
    
    def validate(self, payload: ComputationPayload) -> tuple[bool, str | None]:
        """Validate Python payload."""
        if payload.language != Language.PYTHON:
            return False, f"Unsupported language: {payload.language}"
        
        # Check code length
        if len(payload.code) > 100000:
            return False, "Code too long"
        
        # Try to parse
        try:
            ast.parse(payload.code)
        except SyntaxError as e:
            return False, f"Syntax error: {e}"
        
        return True, None
    
    def get_supported_languages(self) -> list[str]:
        """Get supported languages."""
        return [Language.PYTHON.value]
    
    def _build_globals(self, context: ExecutionContext) -> dict[str, Any]:
        """Build globals dictionary for execution."""
        # Start with allowed builtins
        allowed_builtins = {}
        for name in self.contract.allowed_builtins:
            if name in __builtins__:
                allowed_builtins[name] = __builtins__[name]
        
        # Build context
        globals_dict = {
            "__builtins__": allowed_builtins,
            "_context": context.state,
            "_args": payload.args,
            "_input": context.input_data,
        }
        
        return globals_dict


class SecurityError(Exception):
    """Raised when sandbox is violated."""
    pass
