"""Canonical Organic Pipe API surface.

Prefer imports from ``prismpipe`` / ``prismpipe.engine`` rather than the
parallel packages under ``prismpipe.organic.*``, which are experimental
duplicates and not the production execution path.
"""

from prismpipe.engine import (
    CachedComputation,
    ComputationGraph,
    ComputationNode,
    GravityEngine,
    IntentPlanner,
    KnowledgeAtom,
    Organism,
    OrganismExecutor,
    OrganismMutation,
    OrganismPersistence,
    OrganismRegistry,
    OrganismState,
    OrganismWatcher,
    PipelineEvolver,
    PrismEngine,
    SwarmCoordinator,
    TimeSplitter,
)

__all__ = [
    "CachedComputation",
    "ComputationGraph",
    "ComputationNode",
    "GravityEngine",
    "IntentPlanner",
    "KnowledgeAtom",
    "Organism",
    "OrganismExecutor",
    "OrganismMutation",
    "OrganismPersistence",
    "OrganismRegistry",
    "OrganismState",
    "OrganismWatcher",
    "PipelineEvolver",
    "PrismEngine",
    "SwarmCoordinator",
    "TimeSplitter",
]
