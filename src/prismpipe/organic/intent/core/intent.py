"""Intent types."""

from dataclasses import dataclass, field
from typing import Any
from enum import Enum


class IntentType(Enum):
    """Types of intents."""
    FETCH = "fetch"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ANALYZE = "analyze"
    TRANSFORM = "transform"
    AGGREGATE = "aggregate"
    FORMAT = "format"
    VALIDATE = "validate"
    COMPUTE = "compute"
    SEARCH = "search"
    RECOMMEND = "recommend"
    UNKNOWN = "unknown"


@dataclass
class Intent:
    """A parsed intent from natural language or structured input."""
    raw: str
    type: IntentType = IntentType.UNKNOWN
    entities: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    constraints: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
