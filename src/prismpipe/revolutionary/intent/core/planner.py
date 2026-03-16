"""Path planner base."""

import re
from abc import ABC, abstractmethod
from typing import Any

from prismpipe.revolutionary.intent.core.intent import Intent, IntentType
from prismpipe.revolutionary.intent.core.path import CapabilityPath


class PathPlanner(ABC):
    """Base class for path planners."""
    
    @abstractmethod
    def plan_path(self, intent: Intent, context: dict[str, Any] | None = None) -> CapabilityPath:
        """Plan a capability path for an intent."""
        pass
    
    @abstractmethod
    def register_path(self, intent_pattern: str, capabilities: list[str]) -> None:
        """Register a path for an intent pattern."""
        pass


# Default path templates
DEFAULT_PATHS = {
    IntentType.FETCH: ["auth.validate", "data.fetch", "format.json"],
    IntentType.CREATE: ["auth.validate", "validate.input", "data.create", "format.json"],
    IntentType.UPDATE: ["auth.validate", "validate.input", "data.update", "format.json"],
    IntentType.DELETE: ["auth.validate", "data.delete", "format.json"],
    IntentType.ANALYZE: ["auth.validate", "data.fetch", "analytics.compute", "format.json"],
    IntentType.TRANSFORM: ["auth.validate", "transform.apply", "format.json"],
    IntentType.AGGREGATE: ["auth.validate", "data.fetch", "analytics.aggregate", "format.json"],
    IntentType.SEARCH: ["auth.validate", "search.execute", "format.json"],
    IntentType.RECOMMEND: ["auth.validate", "data.fetch", "recommend.compute", "format.json"],
}


class RegexPathPlanner(PathPlanner):
    """Path planner using regex patterns."""
    
    PATTERNS = [
        (re.compile(r'^(get|fetch|retrieve|list)\s+(\w+)', re.I), IntentType.FETCH),
        (re.compile(r'^(create|add|insert)\s+(\w+)', re.I), IntentType.CREATE),
        (re.compile(r'^(update|modify|edit)\s+(\w+)', re.I), IntentType.UPDATE),
        (re.compile(r'^(delete|remove)\s+(\w+)', re.I), IntentType.DELETE),
        (re.compile(r'^(analyze|analyse)\s+(\w+)', re.I), IntentType.ANALYZE),
        (re.compile(r'^(search|find)\s+(\w+)', re.I), IntentType.SEARCH),
        (re.compile(r'^(recommend|suggest)\s+(\w+)', re.I), IntentType.RECOMMEND),
    ]
    
    def __init__(self):
        self._custom_paths: dict[str, list[str]] = {}
    
    def plan_path(self, intent: Intent, context: dict[str, Any] | None = None) -> CapabilityPath:
        """Plan path using regex matching."""
        # Check custom paths first
        if intent.raw in self._custom_paths:
            return CapabilityPath(capabilities=self._custom_paths[intent.raw])
        
        # Try regex patterns
        for pattern, intent_type in self.PATTERNS:
            match = pattern.match(intent.raw)
            if match:
                entities = match.groups()
                path = DEFAULT_PATHS.get(intent_type, []).copy()
                # Customize path with entities
                if entities:
                    path = [p.replace("{entity}", entities[0]) for p in path]
                return CapabilityPath(capabilities=path, confidence=0.9)
        
        return CapabilityPath(capabilities=[], confidence=0.0)
    
    def register_path(self, intent_pattern: str, capabilities: list[str]) -> None:
        """Register custom path."""
        self._custom_paths[intent_pattern] = capabilities
