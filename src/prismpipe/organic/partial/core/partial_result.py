"""Partial result types."""

from dataclasses import dataclass
from enum import Enum


class ConfidenceLevel(Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


@dataclass
class PartialResult:
    data: dict
    confidence: float = 0.5
    confidence_level: ConfidenceLevel = ConfidenceLevel.UNKNOWN
    is_complete: bool = False
    missing_keys: list = None

    def __post_init__(self):
        if self.missing_keys is None:
            self.missing_keys = []