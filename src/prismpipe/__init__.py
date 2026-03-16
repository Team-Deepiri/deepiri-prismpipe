"""PrismPipe - Capability-Routed API Pipeline."""

from prismpipe.core import (
    RequestEnvelope,
    HistoryEntry,
    Metadata,
    Intent,
    create_envelope,
    Node,
    NodeResult,
    CapabilityRouter,
    NodeNotFoundError,
    Pipeline,
    PipelineConfig,
    PipelineResult,
)
from prismpipe.engine import (
    PrismEngine,
    ReplayEngine,
    DiffEngine,
    RemoteExecutor,
    StreamManager,
    ParallelExecutor,
    CostOptimizer,
    CapabilityGraph,
    SemanticCache,
    AncestryTree,
    RequestMemory,
)
from prismpipe.sdk import PrismPipe, node, enrich, transform, validate
from prismpipe.exceptions import (
    PrismPipeError,
    NodeNotFoundError as NodeNotFoundError2,
    NodeExecutionError,
    CapabilityAccessDenied,
    RequestTimeoutError,
    CircuitOpenError,
    RateLimitError,
    ValidationError,
    StorageError,
)
from prismpipe.config import Config, load_config, get_config, set_config
from prismpipe.logging import configure_logging, get_logger, set_request_context, clear_request_context
from prismpipe.features import FeatureFlags, get_feature_flags, set_feature_flags
from prismpipe.tenancy import TenantManager, Tenant, TenantContext, get_tenant_manager, get_current_tenant
from prismpipe.storage import StorageBackend, MemoryStorage, FileStorage, get_snapshot_storage, get_request_storage
from prismpipe.events import EventBus, Event, EventType, get_event_bus
from prismpipe.resilience import CircuitBreaker, RateLimiter, with_retry, TimeoutManager
from prismpipe.computation import ComputationEngine, ComputationPayload, ExecutionResult, ExecutionMode, get_computation_engine, execute_code
from prismpipe.intent import IntentParser, PathPlanner, AdaptivePathPlanner, Intent, IntentType, CapabilityPath
from prismpipe.partial import PartialKnowledgeEngine, PartialResult, BackgroundTask, get_partial_engine
from prismpipe.memory_graph import RequestMemoryGraph, RequestNode, SimilarRequest, get_request_memory_graph
from prismpipe.swarm import SwarmEngine, SwarmEnvelope, SwarmResult, get_swarm_engine
from prismpipe.dna import PipelineDNA, PipelineGenome, get_pipeline_dna

__version__ = "0.2.0"

__all__ = [
    # Core
    "RequestEnvelope",
    "HistoryEntry",
    "Metadata",
    "Intent",
    "create_envelope",
    "Node",
    "NodeResult",
    "CapabilityRouter",
    "NodeNotFoundError",
    "Pipeline",
    "PipelineConfig",
    "PipelineResult",
    # Engine
    "PrismEngine",
    "ReplayEngine",
    "DiffEngine",
    "RemoteExecutor",
    "StreamManager",
    "ParallelExecutor",
    "CostOptimizer",
    "CapabilityGraph",
    "SemanticCache",
    "AncestryTree",
    "RequestMemory",
    # SDK
    "PrismPipe",
    "node",
    "enrich",
    "transform",
    "validate",
    # Exceptions
    "PrismPipeError",
    "NodeExecutionError",
    "CapabilityAccessDenied",
    "RequestTimeoutError",
    "CircuitOpenError",
    "RateLimitError",
    "ValidationError",
    "StorageError",
    # Config & Logging
    "Config",
    "load_config",
    "get_config",
    "set_config",
    "configure_logging",
    "get_logger",
    "set_request_context",
    "clear_request_context",
    # Features & Tenancy
    "FeatureFlags",
    "get_feature_flags",
    "set_feature_flags",
    "TenantManager",
    "Tenant",
    "TenantContext",
    "get_tenant_manager",
    "get_current_tenant",
    # Storage & Events
    "StorageBackend",
    "MemoryStorage",
    "FileStorage",
    "get_snapshot_storage",
    "get_request_storage",
    "EventBus",
    "Event",
    "EventType",
    "get_event_bus",
    # Resilience
    "CircuitBreaker",
    "RateLimiter",
    "with_retry",
    "TimeoutManager",
    # Revolutionary Features
    "ComputationEngine",
    "ComputationPayload",
    "ExecutionResult",
    "ExecutionMode",
    "get_computation_engine",
    "execute_code",
    "IntentParser",
    "PathPlanner",
    "AdaptivePathPlanner",
    "Intent",
    "IntentType",
    "CapabilityPath",
    "PartialKnowledgeEngine",
    "PartialResult",
    "BackgroundTask",
    "get_partial_engine",
    "RequestMemoryGraph",
    "RequestNode",
    "SimilarRequest",
    "get_request_memory_graph",
    "SwarmEngine",
    "SwarmEnvelope",
    "SwarmResult",
    "get_swarm_engine",
    "PipelineDNA",
    "PipelineGenome",
    "get_pipeline_dna",
]


def create_app(config: dict | None = None):
    """Create a PrismPipe application."""
    from prismpipe.core import PipelineConfig
    from prismpipe.sdk import PrismPipe
    if config:
        pipeline_config = PipelineConfig(
            max_iterations=config.get("pipeline.max_iterations", 100),
            timeout_seconds=config.get("pipeline.timeout_seconds", 30),
        )
    else:
        pipeline_config = None
    return PrismPipe(config=pipeline_config)
