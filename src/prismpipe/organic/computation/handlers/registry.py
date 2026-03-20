"""Handler registry for computation handlers."""

from typing import Any, Callable, Awaitable


class HandlerRegistry:
    """Registry for computation handlers."""
    
    def __init__(self):
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {}
    
    def register(self, name: str, handler: Callable[..., Awaitable[Any]]) -> None:
        """Register a handler."""
        self._handlers[name] = handler
    
    def get(self, name: str) -> Callable[..., Awaitable[Any]] | None:
        """Get a handler by name."""
        return self._handlers.get(name)
    
    def list_handlers(self) -> list[str]:
        """List all registered handlers."""
        return list(self._handlers.keys())
    
    def unregister(self, name: str) -> None:
        """Unregister a handler."""
        self._handlers.pop(name, None)
