"""PrismPipe event system."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Awaitable
from enum import Enum


class EventType(str, Enum):
    """Event types."""
    REQUEST_STARTED = "request.started"
    REQUEST_COMPLETED = "request.completed"
    REQUEST_FAILED = "request.failed"
    NODE_EXECUTED = "node.executed"
    NODE_FAILED = "node.failed"
    CACHE_HIT = "cache.hit"
    CACHE_MISS = "cache.miss"
    PIPELINE_STARTED = "pipeline.started"
    PIPELINE_COMPLETED = "pipeline.completed"


@dataclass
class Event:
    """Event data."""
    type: EventType
    timestamp: datetime
    data: dict[str, Any]


EventHandler = Callable[[Event], Awaitable[None]]


class EventBus:
    """Event bus for node-to-node communication."""

    def __init__(self):
        self._handlers: dict[EventType, list[EventHandler]] = defaultdict(list)
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Subscribe to an event type."""
        self._handlers[event_type].append(handler)

    def subscribe_all(self, handler: EventHandler) -> None:
        """Subscribe to all events."""
        self._subscribers["_all_"].append(handler)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Unsubscribe from an event type."""
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    async def publish(self, event: Event) -> None:
        """Publish an event."""
        tasks = []

        for handler in self._handlers[event.type]:
            tasks.append(handler(event))

        for handler in self._subscribers["_all_"]:
            tasks.append(handler(event))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def emit_node_executed(
        self,
        capability: str,
        latency_ms: float,
        success: bool,
        request_id: str,
    ) -> None:
        """Emit node executed event."""
        await self.publish(Event(
            type=EventType.NODE_EXECUTED if success else EventType.NODE_FAILED,
            timestamp=datetime.utcnow(),
            data={
                "capability": capability,
                "latency_ms": latency_ms,
                "success": success,
                "request_id": request_id,
            }
        ))

    async def emit_request_started(self, request_id: str, intent: str) -> None:
        """Emit request started event."""
        await self.publish(Event(
            type=EventType.REQUEST_STARTED,
            timestamp=datetime.utcnow(),
            data={
                "request_id": request_id,
                "intent": intent,
            }
        ))

    async def emit_request_completed(self, request_id: str, duration_ms: float) -> None:
        """Emit request completed event."""
        await self.publish(Event(
            type=EventType.REQUEST_COMPLETED,
            timestamp=datetime.utcnow(),
            data={
                "request_id": request_id,
                "duration_ms": duration_ms,
            }
        ))


# Default event bus
_default_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get default event bus."""
    global _default_event_bus
    if _default_event_bus is None:
        _default_event_bus = EventBus()
    return _default_event_bus


def set_event_bus(bus: EventBus) -> None:
    """Set default event bus."""
    global _default_event_bus
    _default_event_bus = bus
