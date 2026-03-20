"""Sandbox base class."""

from abc import ABC, abstractmethod
from typing import Any


class Sandbox(ABC):
    """
    Base class for computation sandboxes.
    
    A sandbox restricts what code can do for security.
    """
    
    @abstractmethod
    def validate(self, code: str) -> tuple[bool, str | None]:
        """
        Validate code is safe to execute.
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        pass
    
    @abstractmethod
    def wrap_code(self, code: str) -> str:
        """
        Wrap code with safety measures.
        
        Returns:
            Wrapped code string
        """
        pass
    
    @abstractmethod
    def get_allowed_builtins(self) -> dict[str, Any]:
        """
        Get dictionary of allowed builtins.
        
        Returns:
            Dict of allowed builtin names to functions
        """
        pass
