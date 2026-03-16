"""Runtime base class."""

from abc import ABC, abstractmethod
from typing import Any

from prismpipe.revolutionary.computation.core.payload import ComputationPayload, ExecutionContext
from prismpipe.revolutionary.computation.core.result import ExecutionResult


class Runtime(ABC):
    """
    Base class for computation runtimes.
    
    A runtime executes ComputationPayloads in a specific language/environment.
    """
    
    @abstractmethod
    async def execute(
        self,
        payload: ComputationPayload,
        context: ExecutionContext
    ) -> ExecutionResult:
        """
        Execute a computation payload.
        
        Args:
            payload: The computation to execute
            context: Execution context with state and capabilities
            
        Returns:
            ExecutionResult with the outcome
        """
        pass
    
    @abstractmethod
    def validate(self, payload: ComputationPayload) -> tuple[bool, str | None]:
        """
        Validate a payload can be executed.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        pass
    
    @abstractmethod
    def get_supported_languages(self) -> list[str]:
        """Get list of supported language identifiers."""
        pass
