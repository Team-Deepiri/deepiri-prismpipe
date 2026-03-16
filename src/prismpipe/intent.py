"""PrismPipe Intent Parser and Path Planning."""

import re
from dataclasses import dataclass, field
from typing import Any
from enum import Enum

from prismpipe.core import CapabilityRouter


class IntentType(str, Enum):
    """Types of intents."""
    FETCH = "fetch"           # Get data
    CREATE = "create"         # Create resource
    UPDATE = "update"         # Update resource
    DELETE = "delete"         # Delete resource
    ANALYZE = "analyze"      # Analyze data
    TRANSFORM = "transform"   # Transform data
    AGGREGATE = "aggregate"   # Aggregate data
    FORMAT = "format"         # Format output
    VALIDATE = "validate"     # Validate input
    COMPUTE = "compute"       # Run computation
    UNKNOWN = "unknown"


@dataclass
class Intent:
    """Parsed intent from natural language or structured input."""
    raw: str
    type: IntentType = IntentType.UNKNOWN
    entities: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass
class CapabilityPath:
    """A planned path of capabilities to fulfill an intent."""
    capabilities: list[str]
    confidence: float = 1.0
    estimated_latency_ms: float = 0.0
    fallback_paths: list[list[str]] = field(default_factory=list)


class IntentParser:
    """
    Parse natural language intents into structured intents.
    
    Example:
    "get user orders" -> Intent(type=FETCH, entities={resource: "orders", subject: "user"})
    "summarize sales data" -> Intent(type=ANALYZE, entities={data: "sales"})
    """

    # Intent patterns
    PATTERNS: list[tuple[re.Pattern, IntentType]] = [
        (re.compile(r'^(get|fetch|retrieve|list|show)\s+(?P<res>\w+)', re.I), IntentType.FETCH),
        (re.compile(r'^(create|add|insert|new)\s+(?P<res>\w+)', re.I), IntentType.CREATE),
        (re.compile(r'^(update|modify|edit|change)\s+(?P<res>\w+)', re.I), IntentType.UPDATE),
        (re.compile(r'^(delete|remove|drop)\s+(?P<res>\w+)', re.I), IntentType.DELETE),
        (re.compile(r'^(analyze|analyse|examine|inspect)\s+(?P<data>\w+)', re.I), IntentType.ANALYZE),
        (re.compile(r'^(transform|convert|map)\s+(?P<data>\w+)', re.I), IntentType.TRANSFORM),
        (re.compile(r'^(aggregate|sum|count|average|total)\s+(?P<data>\w+)', re.I), IntentType.AGGREGATE),
        (re.compile(r'^(format|render|output)\s+(?P<data>\w+)', re.I), IntentType.FORMAT),
        (re.compile(r'^(validate|check|verify)\s+(?P<data>\w+)', re.I), IntentType.VALIDATE),
        (re.compile(r'^(compute|calculate|run)\s+(?P<code>\w+)', re.I), IntentType.COMPUTE),
    ]

    # Entity extractors
    ENTITY_EXTRACTORS = {
        'user': re.compile(r'(?:the\s+)?user(?:\'s)?\s+(?P<attr>\w+)', re.I),
        'order': re.compile(r'(?:the\s+)?order(s)?(?:\s+for)?\s+(?P<attr>\w+)', re.I),
        'product': re.compile(r'(?:the\s+)?product(s)?(?:\s+from)?\s+(?P<attr>\w+)', re.I),
        'time': re.compile(r'(last|this|next)\s+(?P<period>week|month|year|day)', re.I),
    }

    def parse(self, intent_text: str) -> Intent:
        """Parse natural language intent."""
        intent_text = intent_text.strip()
        
        # Try pattern matching
        for pattern, intent_type in self.PATTERNS:
            match = pattern.match(intent_text)
            if match:
                entities = match.groupdict()
                # Clean up entities
                entities = {k: v for k, v in entities.items() if v}
                return Intent(
                    raw=intent_text,
                    type=intent_type,
                    entities=entities,
                    confidence=0.9
                )

        # Fallback
        return Intent(
            raw=intent_text,
            type=IntentType.UNKNOWN,
            confidence=0.1
        )

    def parse_structured(self, intent_data: dict[str, Any]) -> Intent:
        """Parse structured intent."""
        return Intent(
            raw=intent_data.get("raw", ""),
            type=IntentType(intent_data.get("type", "unknown")),
            entities=intent_data.get("entities", {}),
            confidence=float(intent_data.get("confidence", 1.0)),
            constraints=intent_data.get("constraints", {})
        )


class PathPlanner:
    """
    Plan capability paths from intents.
    
    Uses the capability router to find and chain capabilities.
    """

    # Default capability chains for intent types
    DEFAULT_PATHS: dict[IntentType, list[str]] = {
        IntentType.FETCH: ["auth.validate", "data.fetch", "format.json"],
        IntentType.CREATE: ["auth.validate", "validate.input", "data.create", "format.json"],
        IntentType.UPDATE: ["auth.validate", "validate.input", "data.update", "format.json"],
        IntentType.DELETE: ["auth.validate", "data.delete", "format.json"],
        IntentType.ANALYZE: ["auth.validate", "data.fetch", "analytics.compute", "format.json"],
        IntentType.TRANSFORM: ["auth.validate", "transform.apply", "format.json"],
        IntentType.AGGREGATE: ["auth.validate", "data.fetch", "analytics.aggregate", "format.json"],
        IntentType.FORMAT: ["format.apply"],
        IntentType.VALIDATE: ["validate.input"],
        IntentType.COMPUTE: ["auth.validate", "compute.execute", "format.json"],
    }

    def __init__(self, router: CapabilityRouter | None = None):
        self.router = router
        self._custom_paths: dict[str, list[str]] = {}

    def plan_path(
        self,
        intent: Intent,
        context: dict[str, Any] | None = None
    ) -> CapabilityPath:
        """Plan a capability path for an intent."""
        context = context or {}

        # Check custom paths first
        if intent.raw in self._custom_paths:
            return CapabilityPath(
                capabilities=self._custom_paths[intent.raw],
                confidence=1.0
            )

        # Get base path for intent type
        base_path = self.DEFAULT_PATHS.get(intent.type, [])

        if not base_path:
            return CapabilityPath(
                capabilities=[],
                confidence=0.0,
                error=f"No path found for intent type: {intent.type}"
            )

        # Customize based on entities
        capabilities = self._customize_path(base_path, intent, context)

        return CapabilityPath(
            capabilities=capabilities,
            confidence=intent.confidence
        )

    def _customize_path(
        self,
        base_path: list[str],
        intent: Intent,
        context: dict[str, Any]
    ) -> list[str]:
        """Customize path based on intent entities."""
        capabilities = []

        for cap in base_path:
            # Replace placeholders
            if '{resource}' in cap:
                resource = intent.entities.get('res', intent.entities.get('resource', 'data'))
                cap = cap.replace('{resource}', resource)
            if '{data}' in cap:
                data = intent.entities.get('data', 'default')
                cap = cap.replace('{data}', data)

            # Check if capability exists in router
            if self.router:
                try:
                    self.router.resolve(cap)
                    capabilities.append(cap)
                except Exception:
                    # Try variations
                    variations = [
                        cap,
                        f"{cap}.{intent.entities.get('res', 'default')}",
                        f"{cap}.{intent.entities.get('data', 'default')}",
                    ]
                    for var in variations:
                        try:
                            self.router.resolve(var)
                            capabilities.append(var)
                            break
                        except Exception:
                            continue
            else:
                capabilities.append(cap)

        return capabilities

    def register_path(self, intent_pattern: str, capabilities: list[str]) -> None:
        """Register a custom path for an intent pattern."""
        self._custom_paths[intent_pattern] = capabilities

    def learn_path(self, intent: Intent, capabilities: list[str], success: bool) -> None:
        """Learn from execution results to improve future paths."""
        if success and intent.confidence < 1.0:
            self._custom_paths[intent.raw] = capabilities


class AdaptivePathPlanner(PathPlanner):
    """
    Path planner that learns from execution history.
    
    Tracks success rates and latencies to optimize paths.
    """

    def __init__(self, router: CapabilityRouter | None = None):
        super().__init__(router)
        self._path_stats: dict[str, dict[str, Any]] = {}

    def plan_path(
        self,
        intent: Intent,
        context: dict[str, Any] | None = None
    ) -> CapabilityPath:
        """Plan path with learning."""
        # Get base path
        path = super().plan_path(intent, context)

        # Optimize based on history
        if path.capabilities:
            path = self._optimize_path(path, intent)

        return path

    def _optimize_path(self, path: CapabilityPath, intent: Intent) -> CapabilityPath:
        """Optimize path based on historical performance."""
        path_key = " -> ".join(path.capabilities)
        stats = self._path_stats.get(path_key, {})

        # Adjust confidence based on historical success
        success_rate = stats.get('success_rate', 1.0)
        avg_latency = stats.get('avg_latency_ms', 0.0)

        path.confidence = path.confidence * success_rate
        path.estimated_latency_ms = avg_latency

        return path

    def record_execution(
        self,
        capabilities: list[str],
        success: bool,
        latency_ms: float
    ) -> None:
        """Record execution for learning."""
        path_key = " -> ".join(capabilities)
        
        if path_key not in self._path_stats:
            self._path_stats[path_key] = {
                'total': 0,
                'successes': 0,
                'latencies': [],
            }

        stats = self._path_stats[path_key]
        stats['total'] += 1
        if success:
            stats['successes'] += 1
        stats['latencies'].append(latency_ms)

        # Keep only last 100
        stats['latencies'] = stats['latencies'][-100:]

        # Update rates
        stats['success_rate'] = stats['successes'] / stats['total']
        stats['avg_latency_ms'] = sum(stats['latencies']) / len(stats['latencies'])
