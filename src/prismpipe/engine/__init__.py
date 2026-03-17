"""PrismPipe Engine - The complete execution system with organic request processing."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncGenerator, Callable

import httpx

from prismpipe.core.envelope import (
    ExecutionPlan,
    HistoryEntry,
    Intent,
    Metadata,
    RequestEnvelope,
    StateDiff,
    create_envelope,
)
from prismpipe.core.node import Node, NodeResult
from prismpipe.core.router import CapabilityRouter, NodeNotFoundError


class OrganismState(str, Enum):
    SPAWNED = "spawned"
    EXPLORING = "exploring"
    EXECUTING = "executing"
    LEARNING = "learning"
    STORED = "stored"
    EVOLVING = "evolving"
    TERMINATED = "terminated"
    SUSPENDED = "suspended"


@dataclass
class KnowledgeAtom:
    key: str
    value: Any
    confidence: float = 1.0
    source_capability: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tags: list[str] = field(default_factory=list)


@dataclass
class ComputationNode:
    id: str
    capability: str
    input_hash: str
    output_hash: str
    execution_count: int = 1
    avg_latency_ms: float = 0.0
    success_rate: float = 1.0
    child_ids: list[str] = field(default_factory=list)
    parent_id: str | None = None


class ComputationGraph:
    """Shared computation deduplication - compute once, reuse forever."""

    def __init__(self) -> None:
        self._nodes: dict[str, ComputationNode] = {}
        self._hash_to_node: dict[str, str] = {}
        self._capability_stats: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "total_latency": 0.0, "failures": 0}
        )

    def compute_input_hash(self, capability: str, input_data: dict[str, Any]) -> str:
        content = json.dumps(
            {"capability": capability, "input": input_data},
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def find_shared_computation(
        self,
        capability: str,
        input_data: dict[str, Any],
    ) -> ComputationNode | None:
        input_hash = self.compute_input_hash(capability, input_data)
        lookup_key = f"{capability}:{input_hash}"
        
        if lookup_key in self._hash_to_node:
            node_id = self._hash_to_node[lookup_key]
            node = self._nodes[node_id]
            node.execution_count += 1
            return node
        return None

    def register_computation(
        self,
        capability: str,
        input_data: dict[str, Any],
        output_data: dict[str, Any],
        latency_ms: float,
        success: bool,
        parent_node_id: str | None = None,
    ) -> ComputationNode:
        input_hash = self.compute_input_hash(capability, input_data)
        output_hash = hashlib.sha256(
            json.dumps(output_data, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        
        node_id = f"comp_{uuid.uuid4().hex[:8]}"
        lookup_key = f"{capability}:{input_hash}"
        
        if lookup_key in self._hash_to_node:
            existing_id = self._hash_to_node[lookup_key]
            existing = self._nodes[existing_id]
            existing.execution_count += 1
            existing.avg_latency_ms = (
                (existing.avg_latency_ms * (existing.execution_count - 1) + latency_ms)
                / existing.execution_count
            )
            return existing
        
        node = ComputationNode(
            id=node_id,
            capability=capability,
            input_hash=input_hash,
            output_hash=output_hash,
            avg_latency_ms=latency_ms,
            success_rate=1.0 if success else 0.0,
            parent_id=parent_node_id,
        )
        
        self._nodes[node_id] = node
        self._hash_to_node[lookup_key] = node_id
        
        if parent_node_id and parent_node_id in self._nodes:
            self._nodes[parent_node_id].child_ids.append(node_id)
        
        stats = self._capability_stats[capability]
        stats["count"] += 1
        stats["total_latency"] += latency_ms
        if not success:
            stats["failures"] += 1
        
        return node

    def get_deduplication_stats(self) -> dict[str, Any]:
        total_computations = len(self._nodes)
        total_reuses = sum(n.execution_count - 1 for n in self._nodes.values())
        
        return {
            "unique_computations": total_computations,
            "total_reuses": total_reuses,
            "deduplication_ratio": total_reuses / max(total_computations, 1),
            "capability_stats": {
                cap: {
                    "count": stats["count"],
                    "avg_latency": stats["total_latency"] / max(stats["count"], 1),
                    "success_rate": 1 - (stats["failures"] / max(stats["count"], 1)),
                }
                for cap, stats in self._capability_stats.items()
            },
        }

    def get_computation_path(self, node_id: str) -> list[ComputationNode]:
        path = []
        current_id = node_id
        while current_id and current_id in self._nodes:
            node = self._nodes[current_id]
            path.insert(0, node)
            current_id = node.parent_id
        return path


class Organism:
    """
    THE CORE INNOVATION: A request that lives forever.
    
    Instead of: request → pipeline → response → dead
    We have:    Client → Spawn Organism → Evolves → Stores Knowledge → Future requests inherit
    
    Organisms are PERSISTENT COMPUTATIONAL ENTITIES that:
    - Have memory (store derived knowledge)
    - Have ancestry (inherit from previous organisms)
    - Can evolve (self-modify their pipeline)
    - Can collaborate (work with other organisms)
    - Can explore futures (time-split execution)
    """

    def __init__(
        self,
        intent: Intent | str,
        input_data: dict[str, Any] | None = None,
        initial_capability: str | None = None,
        parent_organism_id: str | None = None,
    ):
        self.id = f"org_{uuid.uuid4().hex[:12]}"
        if isinstance(intent, str):
            try:
                self.intent = Intent(intent)
            except ValueError:
                self.intent = intent
        else:
            self.intent = intent
        self.input = input_data or {}
        
        self.state: dict[str, Any] = {}
        self.history: list[HistoryEntry] = []
        self.metadata = Metadata()
        
        self._state = OrganismState.SPAWNED
        self._plan = ExecutionPlan()
        self._next_capability = initial_capability
        self._parent_organism_id = parent_organism_id
        
        self.knowledge: list[KnowledgeAtom] = []
        self._knowledge_index: dict[str, KnowledgeAtom] = {}
        
        self._execution_count = 0
        self._created_at = datetime.now(timezone.utc)
        self._last_executed_at: datetime | None = None
        
        self._computation_node_id: str | None = None
        
        self._evolved_pipeline: list[str] = []
        self._original_pipeline: list[str] = []
        
        self._children: list[Organism] = []
        self._merged_results: dict[str, Any] = {}

    @property
    def envelope(self) -> RequestEnvelope:
        # Convert intent to enum if it's a custom string
        intent_value = self.intent
        if isinstance(intent_value, str):
            try:
                intent_value = Intent(intent_value)
            except ValueError:
                intent_value = Intent.CUSTOM
        
        return RequestEnvelope(
            id=self.id,
            intent=intent_value,
            input=self.input,
            state=self.state,
            history=self.history,
            metadata=self.metadata,
            plan=self._plan,
            next=self._next_capability,
            parent_id=self._parent_organism_id,
        )

    @classmethod
    def from_envelope(cls, envelope: RequestEnvelope) -> "Organism":
        org = cls(
            intent=envelope.intent,
            input_data=envelope.input,
            parent_organism_id=envelope.parent_id,
        )
        org.id = envelope.id
        org.state = dict(envelope.state)
        org.history = list(envelope.history)
        org.metadata = envelope.metadata
        org._plan = envelope.plan
        org._next_capability = envelope.next
        if envelope.terminated:
            org._state = OrganismState.TERMINATED
        return org

    def spawn_child(
        self,
        intent: Intent | str | None = None,
        patch_input: dict[str, Any] | None = None,
    ) -> "Organism":
        child_intent = intent or self.intent
        child_input = dict(self.input)
        
        if patch_input:
            child_input.update(patch_input)
            
        child = Organism(
            intent=child_intent,
            input_data=child_input,
            initial_capability=self._next_capability,
            parent_organism_id=self.id,
        )
        
        child.state = dict(self.state)
        
        for atom in self.knowledge:
            if atom.confidence > 0.5:
                child.ingest_knowledge(atom.key, atom.value, atom.confidence * 0.9)
        
        self._children.append(child)
        return child

    def inherit_from(
        self,
        parent: "Organism",
        inherit_state: bool = True,
        inherit_knowledge: bool = True,
    ) -> None:
        self._parent_organism_id = parent.id
        
        if inherit_state:
            self.state.update(parent.state)
            
        if inherit_knowledge:
            for atom in parent.knowledge:
                if atom.confidence > 0.3:
                    self.ingest_knowledge(
                        atom.key,
                        atom.value,
                        atom.confidence * 0.8,
                    )

    def ingest_knowledge(
        self,
        key: str,
        value: Any,
        confidence: float = 1.0,
        capability: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        if key in self._knowledge_index:
            existing = self._knowledge_index[key]
            existing.value = value
            existing.confidence = max(existing.confidence, confidence)
            existing.timestamp = datetime.now(timezone.utc)
        else:
            atom = KnowledgeAtom(
                key=key,
                value=value,
                confidence=confidence,
                source_capability=capability,
                tags=tags or [],
            )
            self.knowledge.append(atom)
            self._knowledge_index[key] = atom

    def get_knowledge(self, key: str) -> KnowledgeAtom | None:
        return self._knowledge_index.get(key)

    def evolve_pipeline(self, new_capabilities: list[str]) -> None:
        if not self._original_pipeline:
            self._original_pipeline = list(self._plan.capabilities)
        self._evolved_pipeline = new_capabilities
        self._plan.capabilities = new_capabilities
        self._state = OrganismState.EVOLVING

    def add_capability(self, capability: str, position: int | None = None) -> None:
        if position is not None:
            self._plan.insert(position, capability)
        else:
            self._plan.add(capability)

    def remove_capability(self, capability: str) -> None:
        self._plan.remove(capability)

    def record_execution(
        self,
        capability: str,
        duration_ms: float,
        success: bool = True,
    ) -> None:
        self._execution_count += 1
        self._last_executed_at = datetime.now(timezone.utc)
        
        self.record(
            node_id=f"node_{capability}",
            node_type="organism",
            capability=capability,
            action="executed",
            duration_ms=duration_ms,
        )

    def record(
        self,
        node_id: str,
        node_type: str,
        capability: str,
        action: str,
        duration_ms: float | None = None,
    ) -> None:
        entry = HistoryEntry(
            node_id=node_id,
            node_type=node_type,
            capability=capability,
            action=action,
            duration_ms=duration_ms,
            input_snapshot=dict(self.state),
        )
        self.history.append(entry)

    def set_next(self, capability: str | None) -> None:
        self._next_capability = capability

    def get_capability(self) -> str | None:
        if not self._next_capability:
            return None
        if self._next_capability.startswith("capability:"):
            return self._next_capability.split(":", 1)[1]
        return self._next_capability

    def terminate(self, error: str | None = None) -> None:
        self._state = OrganismState.TERMINATED
        if error:
            self.state["_error"] = error

    @property
    def terminated(self) -> bool:
        return self._state == OrganismState.TERMINATED

    @property
    def lineage(self) -> list[str]:
        return [self._parent_organism_id] if self._parent_organism_id else []

    @property
    def execution_time_ms(self) -> float | None:
        if not self.history:
            return None
        durations = [e.duration_ms for e in self.history if e.duration_ms is not None]
        return sum(durations) if durations else None

    @property
    def pipeline(self) -> list[str]:
        return self._evolved_pipeline or self._original_pipeline or self._plan.capabilities

    @property
    def children(self) -> list["Organism"]:
        return self._children


class TimeSplitter:
    """
    EXPLORE MULTIPLE FUTURES IN PARALLEL.
    
    Fork execution into multiple branches, first successful wins.
    
         organism
           / | \\
         A    B  C
         ↓    ↓   ↓
      fast  slow crash
         \\   |   /
          result ← wins
    """

    def __init__(self, router: CapabilityRouter) -> None:
        self._router = router
        self._max_branches = 4
        self._timeout_ms = 5000

    def split(
        self,
        organism: Organism,
        branch_capabilities: list[str],
    ) -> list[Organism]:
        branches = []
        for i, cap in enumerate(branch_capabilities[:self._max_branches]):
            branch = organism.spawn_child()
            branch._next_capability = cap
            branch._state = OrganismState.EXPLORING
            branches.append(branch)
        return branches

    async def execute_branch(
        self,
        branch: Organism,
        computation_graph: ComputationGraph,
    ) -> Organism:
        start_time = time.perf_counter()
        
        while branch._next_capability and not branch.terminated:
            capability = branch.get_capability()
            if not capability:
                break
                
            shared = computation_graph.find_shared_computation(
                capability,
                branch.input,
            )
            
            if shared:
                branch.state["_from_shared"] = True
                branch.state["_shared_node_id"] = shared.id
            else:
                try:
                    node = self._router.resolve(capability)
                    result = node.execute(branch.envelope)
                    branch.state = dict(result.envelope.state)
                    branch.history = list(result.envelope.history)
                    
                    latency = (time.perf_counter() - start_time) * 1000
                    
                    computation_graph.register_computation(
                        capability=capability,
                        input_data=branch.input,
                        output_data=branch.state,
                        latency_ms=latency,
                        success=True,
                    )
                except Exception as e:
                    branch.terminate(str(e))
                    
            branch._next_capability = branch._plan.next()
            
        branch._state = OrganismState.LEARNING
        return branch

    async def execute_time_split(
        self,
        organism: Organism,
        branch_capabilities: list[str],
        computation_graph: ComputationGraph,
    ) -> Organism:
        branches = self.split(organism, branch_capabilities)
        
        async def run_branch(b: Organism) -> Organism:
            try:
                return await self.execute_branch(b, computation_graph)
            except Exception as e:
                b.terminate(str(e))
                return b
        
        tasks = [run_branch(b) for b in branches]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        successful = [
            r for r in results
            if isinstance(r, Organism) and not r.terminated
        ]
        
        if successful:
            winner = min(successful, key=lambda r: r.execution_time_ms or float('inf'))
            organism.state = dict(winner.state)
            organism.history = list(winner.history)
            organism._children = branches
            organism._state = OrganismState.LEARNING
            return organism
        
        organism.terminate("All branches failed")
        return organism


class PipelineEvolver:
    """
    INFRASTRUCTURE THAT LEARNS.
    
    Track pipeline performance and automatically evolve to optimal paths.
    """

    def __init__(self) -> None:
        self._pipeline_performance: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"executions": 0, "total_latency": 0.0, "failures": 0}
        )
        self._optimal_pipelines: dict[str, list[str]] = {}
        self._intent_pipeline_map: dict[str, list[str]] = {}

    def record_execution(
        self,
        pipeline: list[str],
        latency_ms: float,
        success: bool,
    ) -> None:
        pipeline_key = "->".join(pipeline)
        stats = self._pipeline_performance[pipeline_key]
        
        stats["executions"] += 1
        stats["total_latency"] += latency_ms
        
        if not success:
            stats["failures"] += 1

    def register_intent_pipeline(self, intent: str | Intent, pipeline: list[str]) -> None:
        intent_str = intent.value if isinstance(intent, Intent) else intent
        if intent_str not in self._intent_pipeline_map:
            self._intent_pipeline_map[intent_str] = []
        if pipeline not in self._intent_pipeline_map[intent_str]:
            self._intent_pipeline_map[intent_str].append(pipeline)

    def get_optimal_pipeline(self, intent: str | Intent) -> list[str] | None:
        intent_str = intent.value if isinstance(intent, Intent) else intent
        return self._optimal_pipelines.get(intent_str)

    def evolve(
        self,
        intent: str | Intent,
        available_pipelines: list[list[str]] | None = None,
    ) -> list[str]:
        intent_str = intent.value if isinstance(intent, Intent) else intent
        
        pipelines = available_pipelines or self._intent_pipeline_map.get(intent_str, [])
        if not pipelines:
            return []
        
        candidates = []
        
        for pipeline in pipelines:
            pipeline_key = "->".join(pipeline)
            stats = self._pipeline_performance[pipeline_key]
            
            if stats["executions"] == 0:
                score = 0.5
            else:
                avg_latency = stats["total_latency"] / stats["executions"]
                success_rate = 1 - (stats["failures"] / stats["executions"])
                score = success_rate * 1000 / (avg_latency + 100)
                
            candidates.append((pipeline, score))
            
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        if candidates:
            optimal = candidates[0][0]
            self._optimal_pipelines[intent_str] = optimal
            return optimal
            
        return pipelines[0] if pipelines else []

    def get_performance_report(self) -> dict[str, Any]:
        report = {}
        for pipeline_key, stats in self._pipeline_performance.items():
            if stats["executions"] > 0:
                report[pipeline_key] = {
                    "executions": stats["executions"],
                    "avg_latency_ms": stats["total_latency"] / stats["executions"],
                    "success_rate": 1 - (stats["failures"] / stats["executions"]),
                }
        return report


class IntentPlanner:
    """
    KILL THE ENDPOINTS. INTENT-BASED APIs.
    
    Instead of: GET /users
    Clients send: intent: "active users last week"
    PrismPipe figures out: fetch_users → filter_active → aggregate_week → format
    """

    def __init__(self, router: CapabilityRouter) -> None:
        self._router = router
        self._intent_mappings: dict[str, list[str]] = {}
        self._capability_descriptions: dict[str, str] = {}
        self._keyword_index: dict[str, list[str]] = defaultdict(list)

    def register_capability(
        self,
        capability: str,
        description: str,
        keywords: list[str] | None = None,
    ) -> None:
        self._capability_descriptions[capability] = description
        
        if keywords:
            for kw in keywords:
                self._keyword_index[kw.lower()].append(capability)
                if kw not in self._intent_mappings:
                    self._intent_mappings[kw] = []
                if capability not in self._intent_mappings[kw]:
                    self._intent_mappings[kw].append(capability)

    def plan(self, intent: str) -> list[str]:
        intent_lower = intent.lower()
        words = intent_lower.split()
        
        matched_capabilities: list[tuple[str, int]] = []
        
        for kw, capabilities in self._keyword_index.items():
            if kw in intent_lower:
                for cap in capabilities:
                    matched_capabilities.append((cap, len(kw)))
        
        matched_capabilities.sort(key=lambda x: x[1], reverse=True)
        
        unique_caps = []
        seen = set()
        for cap, _ in matched_capabilities:
            if cap not in seen:
                unique_caps.append(cap)
                seen.add(cap)
                
        return unique_caps

    def plan_with_fallback(
        self,
        intent: str,
        fallback_pipeline: list[str],
    ) -> list[str]:
        planned = self.plan(intent)
        return planned if planned else fallback_pipeline


class SwarmCoordinator:
    """
    ORGANISM SWARM COMPUTING.
    
    Multiple organisms collaborate on a single problem.
    Like MapReduce, but request-native.
    """

    def __init__(self) -> None:
        self._swarms: dict[str, list[Organism]] = {}
        self._shared_state: dict[str, dict[str, Any]] = {}
        self._partition_fn: Callable[[Any], str] | None = None
        self._reducers: dict[str, Callable[[list[Any]], Any]] = {}

    def create_swarm(
        self,
        swarm_id: str,
        template: Organism,
        count: int,
    ) -> list[Organism]:
        swarm = []
        for i in range(count):
            worker = template.spawn_child()
            worker.id = f"{swarm_id}_worker_{i}"
            worker._state = OrganismState.EXPLORING
            swarm.append(worker)
            
        self._swarms[swarm_id] = swarm
        self._shared_state[swarm_id] = {}
        return swarm

    def set_partition_fn(self, fn: Callable[[Any], str]) -> None:
        self._partition_fn = fn

    def register_reducer(self, swarm_id: str, reducer: Callable[[list[Any]], Any]) -> None:
        self._reducers[swarm_id] = reducer

    async def execute_swarm(
        self,
        swarm_id: str,
        capability: str,
        computation_graph: ComputationGraph,
        data: list[Any],
    ) -> Any:
        if swarm_id not in self._swarms:
            raise ValueError(f"Swarm {swarm_id} not found")
            
        swarm = self._swarms[swarm_id]
        
        if self._partition_fn:
            partitions: dict[str, list[Any]] = defaultdict(list)
            for item in data:
                partition_key = self._partition_fn(item)
                partitions[partition_key].append(item)
                
            for i, worker in enumerate(swarm):
                partition_data = list(partitions.values())[i % len(partitions)] if partitions else []
                worker.input["partition_data"] = partition_data
        
        results = []
        for worker in swarm:
            worker.set_next(capability)
            
        reducer = self._reducers.get(swarm_id, lambda x: x)
        return reducer(results)

    def get_swarm_results(self, swarm_id: str) -> list[Organism]:
        return self._swarms.get(swarm_id, [])


class OrganismRegistry:
    """
    PERSISTENT ORGANISM STORAGE.
    
    Stores all living organisms and their knowledge.
    Enables queries like "find similar organisms" or "get lineage".
    """

    def __init__(self) -> None:
        self._organisms: dict[str, Organism] = {}
        self._by_intent: dict[str, list[str]] = defaultdict(list)
        self._by_lineage: dict[str, list[str]] = defaultdict(list)

    def register(self, organism: Organism) -> None:
        self._organisms[organism.id] = organism
        
        intent_str = organism.intent.value if isinstance(organism.intent, Intent) else str(organism.intent)
        self._by_intent[intent_str].append(organism.id)
        
        if organism._parent_organism_id:
            self._by_lineage[organism._parent_organism_id].append(organism.id)

    def get(self, organism_id: str) -> Organism | None:
        return self._organisms.get(organism_id)

    def find_by_intent(self, intent: str | Intent) -> list[Organism]:
        intent_str = intent.value if isinstance(intent, Intent) else intent
        ids = self._by_intent.get(intent_str, [])
        return [self._organisms[i] for i in ids if i in self._organisms]

    def find_similar(
        self,
        organism: Organism,
        max_results: int = 5,
    ) -> list[Organism]:
        intent_str = organism.intent.value if isinstance(organism.intent, Intent) else str(organism.intent)
        candidates = self.find_by_intent(intent_str)
        
        scored = []
        for cand in candidates:
            if cand.id == organism.id:
                continue
            score = self._compute_similarity(organism, cand)
            scored.append((cand, score))
        
        scored.sort(key=lambda x: x[1], reverse=True)
        return [c for c, _ in scored[:max_results]]

    def _compute_similarity(self, a: Organism, b: Organism) -> float:
        score = 0.0
        
        a_intent = a.intent.value if isinstance(a.intent, Intent) else str(a.intent)
        b_intent = b.intent.value if isinstance(b.intent, Intent) else str(b.intent)
        
        if a_intent == b_intent:
            score += 0.5
        
        a_keys = set(a.state.keys())
        b_keys = set(b.state.keys())
        if a_keys & b_keys:
            score += 0.3 * len(a_keys & b_keys) / max(len(a_keys | b_keys), 1)
        
        return score

    def get_lineage(self, organism_id: str) -> list[Organism]:
        lineage = []
        current_id = organism_id
        
        while current_id:
            if current_id in self._organisms:
                lineage.append(self._organisms[current_id])
                current_id = self._organisms[current_id]._parent_organism_id
            else:
                break
                
        return lineage

    def get_descendants(self, organism_id: str) -> list[Organism]:
        descendants = []
        queue = [organism_id]
        
        while queue:
            current = queue.pop(0)
            children = self._by_lineage.get(current, [])
            for child_id in children:
                if child_id in self._organisms:
                    descendants.append(self._organisms[child_id])
                    queue.append(child_id)
                    
        return descendants

    def query_knowledge(
        self,
        key: str,
        min_confidence: float = 0.5,
    ) -> list[tuple[Organism, KnowledgeAtom]]:
        results = []
        for org in self._organisms.values():
            atom = org.get_knowledge(key)
            if atom and atom.confidence >= min_confidence:
                results.append((org, atom))
        return results


class GravityEngine:
    """
    COMPUTE MOVES TO DATA.
    
    Instead of moving data to compute, move compute to data.
    Routes organisms to the cluster where their data resides.
    """

    def __init__(self) -> None:
        self._data_locations: dict[str, str] = {}
        self._cluster_capabilities: dict[str, list[str]] = {}

    def register_data_location(self, data_id: str, cluster: str) -> None:
        self._data_locations[data_id] = cluster

    def register_cluster_capabilities(self, cluster: str, capabilities: list[str]) -> None:
        self._cluster_capabilities[cluster] = capabilities

    def get_optimal_cluster(
        self,
        required_data: list[str],
        preferred_capabilities: list[str],
    ) -> str | None:
        cluster_scores: dict[str, float] = defaultdict(float)
        
        for data_id in required_data:
            cluster = self._data_locations.get(data_id)
            if cluster:
                cluster_scores[cluster] += 1.0
        
        for cluster, caps in self._cluster_capabilities.items():
            for pref in preferred_capabilities:
                if pref in caps:
                    cluster_scores[cluster] += 0.5
        
        if not cluster_scores:
            return None
            
        return max(cluster_scores.items(), key=lambda x: x[1])[0]


class OrganismExecutor:
    """
    EXECUTES ORGANISMS THROUGH CAPABILITY PIPELINES.
    
    Handles the full lifecycle: spawn → explore → execute → learn → store.
    """

    def __init__(self, router: CapabilityRouter) -> None:
        self._router = router

    async def execute(
        self,
        organism: Organism,
        computation_graph: ComputationGraph | None = None,
    ) -> Organism:
        computation_graph = computation_graph or ComputationGraph()
        
        organism._state = OrganismState.EXECUTING
        start_time = time.perf_counter()
        
        # Create envelope ONCE and keep reference to it
        # Don't use organism.envelope property in the loop!
        intent_value = organism.intent
        if isinstance(intent_value, str):
            try:
                intent_value = Intent(intent_value)
            except ValueError:
                intent_value = Intent.CUSTOM
        
        envelope = RequestEnvelope(
            id=organism.id,
            intent=intent_value,
            input=organism.input,
            state=organism.state,
            history=organism.history,
            metadata=organism.metadata,
            plan=organism._plan,
            next=organism._next_capability,
            parent_id=organism._parent_organism_id,
        )
        
        while organism._next_capability and not organism.terminated:
            capability = organism.get_capability()
            if not capability:
                break
            
            shared = computation_graph.find_shared_computation(capability, organism.input)
            
            if shared:
                organism.state["_from_shared"] = True
                organism.state["_shared_node_id"] = shared.id
                organism._computation_node_id = shared.id
                # Terminate since we're reusing shared computation
                envelope.next = None
            else:
                try:
                    node = self._router.resolve(capability)
                    result = node.execute(envelope)
                    
                    # Update envelope reference from result
                    envelope = result.envelope
                    organism.state = dict(envelope.state)
                    organism.history = list(envelope.history)
                    
                    latency = (time.perf_counter() - start_time) * 1000
                    
                    computation_graph.register_computation(
                        capability=capability,
                        input_data=organism.input,
                        output_data=organism.state,
                        latency_ms=latency,
                        success=True,
                    )
                except Exception as e:
                    organism.terminate(str(e))
                    break
            
            # Sync next capability from envelope
            organism._next_capability = envelope.next
        
        organism._state = OrganismState.LEARNING
        return organism


class ReplayEngine:
    """Git-like version control for requests."""

    def __init__(self) -> None:
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._forks: dict[str, list[str]] = {}

    def snapshot(self, envelope: RequestEnvelope, label: str | None = None) -> str:
        snapshot_id = label or f"snap_{uuid.uuid4().hex[:8]}"
        
        self._snapshots[snapshot_id] = {
            "id": envelope.id,
            "intent": envelope.intent.value if isinstance(envelope.intent, Intent) else envelope.intent,
            "input": envelope.input,
            "state": dict(envelope.state),
            "history": [h.model_dump() for h in envelope.history],
            "plan": envelope.plan.model_dump() if envelope.plan else None,
            "next": envelope.next,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        if envelope.id not in self._forks:
            self._forks[envelope.id] = []
        self._forks[envelope.id].append(snapshot_id)
        
        return snapshot_id

    def restore(self, snapshot_id: str) -> RequestEnvelope | None:
        if snapshot_id not in self._snapshots:
            return None
            
        snap = self._snapshots[snapshot_id]
        
        envelope = RequestEnvelope(
            id=snap["id"],
            intent=Intent(snap["intent"]) if isinstance(snap["intent"], str) else snap["intent"],
            input=snap["input"],
            state=snap["state"],
            next=snap["next"],
        )
        
        envelope.history = [HistoryEntry(**h) for h in snap["history"]]
        
        if snap.get("plan"):
            envelope.plan = ExecutionPlan(**snap["plan"])
            
        return envelope

    def replay_from(
        self,
        envelope: RequestEnvelope,
        from_capability: str,
    ) -> RequestEnvelope:
        replay_index = 0
        for i, entry in enumerate(envelope.history):
            if entry.capability == from_capability:
                replay_index = i + 1
                break
        
        if replay_index < len(envelope.history):
            state_at_point = envelope.history[replay_index - 1].input_snapshot
            if state_at_point:
                envelope.state = dict(state_at_point)
        
        envelope.history = envelope.history[:replay_index]
        envelope.terminated = False
        envelope.error = None
        envelope.set_next(from_capability)
        
        return envelope

    def fork(
        self,
        envelope: RequestEnvelope,
        patch: dict[str, Any] | None = None,
    ) -> RequestEnvelope:
        fork_id = f"fork_{uuid.uuid4().hex[:8]}"
        
        new_envelope = RequestEnvelope(
            id=fork_id,
            intent=envelope.intent,
            input=envelope.input,
            state=dict(envelope.state),
            history=list(envelope.history),
            metadata=Metadata(
                correlation_id=envelope.id,
                trace_id=envelope.metadata.trace_id,
            ),
            plan=ExecutionPlan(
                capabilities=list(envelope.plan.capabilities) if envelope.plan else [],
                current_index=envelope.plan.current_index if envelope.plan else 0,
            ),
            next=envelope.next,
            parent_id=envelope.id,
        )
        
        if patch:
            new_envelope.state.update(patch)
            
        self.snapshot(new_envelope, fork_id)
        
        return new_envelope


class DiffEngine:
    """Tracks state mutations per node."""

    def __init__(self) -> None:
        self._diffs: dict[str, list[StateDiff]] = {}

    def compute_diff(
        self,
        envelope: RequestEnvelope,
        before_state: dict[str, Any],
    ) -> StateDiff:
        after_state = envelope.state
        diff = StateDiff(
            node_id=envelope.history[-1].node_id if envelope.history else "unknown",
            capability=envelope.history[-1].capability if envelope.history else "unknown",
        )

        all_keys = set(before_state.keys()) | set(after_state.keys())

        for key in all_keys:
            before = before_state.get(key)
            after = after_state.get(key)

            if key not in before_state:
                diff.added[key] = after
            elif key not in after_state:
                diff.removed.append(key)
            elif before != after:
                diff.modified[key] = (before, after)

        return diff

    def record(self, envelope: RequestEnvelope, diff: StateDiff) -> None:
        if envelope.id not in self._diffs:
            self._diffs[envelope.id] = []
        self._diffs[envelope.id].append(diff)

    def get_timeline(self, envelope_id: str) -> list[StateDiff]:
        return self._diffs.get(envelope_id, [])


class NodeLocation(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    CLUSTER = "cluster"
    GPU_WORKER = "gpu_worker"


@dataclass 
class RemoteNodeConfig:
    location: NodeLocation = NodeLocation.LOCAL
    endpoint: str | None = None


class RemoteExecutor:
    """Execute nodes on remote machines."""

    def __init__(self) -> None:
        self._remote_endpoints: dict[str, str] = {}
        self._node_configs: dict[str, RemoteNodeConfig] = {}

    def register_remote(
        self,
        capability: str,
        endpoint: str,
        location: NodeLocation = NodeLocation.REMOTE,
    ) -> None:
        self._remote_endpoints[capability] = endpoint
        self._node_configs[capability] = RemoteNodeConfig(
            location=location,
            endpoint=endpoint,
        )

    async def execute_remote(
        self,
        capability: str,
        envelope: RequestEnvelope,
    ) -> RequestEnvelope:
        if capability not in self._remote_endpoints:
            raise ValueError(f"No remote endpoint for {capability}")
            
        endpoint = self._remote_endpoints[capability]
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                endpoint,
                json=envelope.model_dump(),
                timeout=30.0,
            )
            response.raise_for_status()
            result_data = response.json()
            
        return RequestEnvelope(**result_data)

    def is_remote(self, capability: str) -> bool:
        return capability in self._remote_endpoints


@dataclass
class StreamChunk:
    capability: str
    data: Any
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_final: bool = False
    progress: float = 0.0


class StreamManager:
    """Progressive responses."""

    def __init__(self) -> None:
        self._streams: dict[str, asyncio.Queue[StreamChunk | None]] = {}

    @asynccontextmanager
    async def stream(
        self,
        envelope: RequestEnvelope,
    ) -> AsyncGenerator[asyncio.Queue[StreamChunk | None], None]:
        queue: asyncio.Queue[StreamChunk | None] = asyncio.Queue()
        self._streams[envelope.id] = queue
        
        try:
            yield queue
        finally:
            if envelope.id in self._streams:
                del self._streams[envelope.id]

    async def emit(
        self,
        envelope_id: str,
        capability: str,
        data: Any,
        progress: float = 0.0,
        is_final: bool = False,
    ) -> None:
        if envelope_id not in self._streams:
            return
            
        chunk = StreamChunk(
            capability=capability,
            data=data,
            progress=progress,
            is_final=is_final,
        )
        await self._streams[envelope_id].put(chunk)
        
        if is_final:
            await self._streams[envelope_id].put(None)


@dataclass
class Branch:
    name: str
    capability: str
    condition: Callable[[RequestEnvelope], bool] | None = None


@dataclass
class BranchResult:
    branch_name: str
    envelope: RequestEnvelope
    success: bool
    duration_ms: float


class ParallelExecutor:
    """Execute branches in parallel."""

    def __init__(self, router: CapabilityRouter) -> None:
        self._router = router
        self._merge_handlers: dict[str, Callable] = {}

    def register_merge(
        self,
        capability: str,
        handler: Callable[[list[BranchResult]], RequestEnvelope],
    ) -> None:
        self._merge_handlers[capability] = handler

    async def execute_parallel(
        self,
        envelope: RequestEnvelope,
        branches: list[Branch],
    ) -> RequestEnvelope:
        
        async def run_branch(branch: Branch) -> BranchResult:
            start = time.perf_counter()
            branch_envelope = envelope.model_copy(deep=True)
            branch_envelope.set_next(branch.capability)
            
            while branch_envelope.next and not branch_envelope.terminated:
                capability = branch_envelope.get_capability()
                if not capability:
                    break
                    
                try:
                    node = self._router.resolve(capability)
                    result = node.execute(branch_envelope)
                    branch_envelope = result.envelope
                except NodeNotFoundError:
                    branch_envelope.terminate(f"Node not found: {capability}")
                    break
                    
            duration = (time.perf_counter() - start) * 1000
            
            return BranchResult(
                branch_name=branch.name,
                envelope=branch_envelope,
                success=not branch_envelope.terminated,
                duration_ms=duration,
            )
        
        tasks = [run_branch(b) for b in branches]
        results: list[BranchResult | BaseException] = await asyncio.gather(*tasks, return_exceptions=True)
        
        successful_results = [r for r in results if isinstance(r, BranchResult) and r.success]
        
        merged_state = {}
        for r in successful_results:
            merged_state[r.branch_name] = r.envelope.state
            
        envelope.state["_merged_branches"] = merged_state
        
        return envelope


@dataclass
class NodeCost:
    capability: str
    cost_per_call: float = 0.0
    latency_ms: float = 0.0
    reliability: float = 1.0


class CostOptimizer:
    """Optimize routing based on cost."""

    def __init__(self) -> None:
        self._costs: dict[str, list[NodeCost]] = {}

    def register_cost(self, cost: NodeCost) -> None:
        if cost.capability not in self._costs:
            self._costs[cost.capability] = []
        self._costs[cost.capability].append(cost)

    def select_node(
        self,
        capability: str,
        mode: str = "balanced",
    ) -> NodeCost | None:
        candidates = self._costs.get(capability, [])
        if not candidates:
            return None
            
        if mode == "fastest":
            return min(candidates, key=lambda c: c.latency_ms)
        elif mode == "cheapest":
            return min(candidates, key=lambda c: c.cost_per_call)
        elif mode == "balanced":
            return min(
                candidates,
                key=lambda c: (
                    c.latency_ms * 0.4 +
                    c.cost_per_call * 100 * 0.3 +
                    (1 - c.reliability) * 100 * 0.3
                ),
            )
            
        return candidates[0]


@dataclass
class CapabilitySpec:
    capability: str
    description: str = ""
    input_schema: dict[str, str] = field(default_factory=dict)
    output_schema: dict[str, str] = field(default_factory=dict)
    required_capabilities: list[str] = field(default_factory=list)


class CapabilityGraph:
    """Auto-discovery of capability paths."""

    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilitySpec] = {}
        self._edges: dict[str, list[str]] = {}

    def register(self, spec: CapabilitySpec) -> None:
        self._capabilities[spec.capability] = spec
        
        if spec.capability not in self._edges:
            self._edges[spec.capability] = []
            
        for required in spec.required_capabilities:
            if required not in self._edges:
                self._edges[required] = []
            self._edges[required].append(spec.capability)

    def discover_path(
        self,
        intent: str,
        start_capability: str | None = None,
    ) -> list[str] | None:
        if start_capability:
            return self._bfs_path(start_capability)
        return None

    def _bfs_path(self, start: str) -> list[str]:
        from collections import deque
        
        queue = deque([(start, [start])])
        visited = {start}
        
        while queue:
            current, path = queue.popleft()
            
            for next_cap in self._edges.get(current, []):
                if next_cap not in visited:
                    visited.add(next_cap)
                    queue.append((next_cap, path + [next_cap]))
                    
        return [start]


class SemanticCache:
    """Intent-based caching."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[RequestEnvelope, datetime]] = {}
        self._intent_index: dict[str, list[str]] = {}

    def _compute_intent_hash(self, intent: str, state: dict[str, Any]) -> str:
        content = json.dumps({"intent": intent, "state": state}, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def get(self, intent: str, state: dict[str, Any]) -> RequestEnvelope | None:
        intent_hash = self._compute_intent_hash(intent, state)
        
        if intent_hash in self._cache:
            return self._cache[intent_hash][0]
                
        for indexed_intent, hashes in self._intent_index.items():
            if self._intents_similar(intent, indexed_intent):
                for h in hashes:
                    if h in self._cache:
                        return self._cache[h][0]
                        
        return None

    def set(
        self,
        intent: str,
        state: dict[str, Any],
        envelope: RequestEnvelope,
    ) -> None:
        intent_hash = self._compute_intent_hash(intent, state)
        self._cache[intent_hash] = (envelope, datetime.now(timezone.utc))
        
        if intent not in self._intent_index:
            self._intent_index[intent] = []
        self._intent_index[intent].append(intent_hash)

    def _intents_similar(self, intent1: str, intent2: str) -> bool:
        words1 = set(intent1.lower().split())
        words2 = set(intent2.lower().split())
        
        intersection = words1 & words2
        union = words1 | words2
        
        if not union:
            return False
            
        return len(intersection) / len(union) > 0.7


class AncestryTree:
    """Track request lineages."""

    def __init__(self) -> None:
        self._children: dict[str, list[str]] = {}

    def add_child(self, parent_id: str, child_id: str) -> None:
        if parent_id not in self._children:
            self._children[parent_id] = []
        self._children[parent_id].append(child_id)

    def get_lineage(self, request_id: str) -> list[str]:
        lineage = [request_id]
        
        current = request_id
        while True:
            parent = None
            for parent_id, children in self._children.items():
                if current in children:
                    parent = parent_id
                    break
                    
            if parent:
                lineage.insert(0, parent)
                current = parent
            else:
                break
                
        return lineage


class RequestMemory:
    """Persistent request storage."""

    def __init__(self) -> None:
        self._requests: dict[str, RequestEnvelope] = {}
        self._knowledge: dict[str, dict[str, Any]] = {}

    def store(self, envelope: RequestEnvelope) -> None:
        self._requests[envelope.id] = envelope
        
        if envelope.state:
            self._knowledge[envelope.id] = {
                "intent": envelope.intent.value if isinstance(envelope.intent, Intent) else envelope.intent,
                "derived": envelope.state.get("derived_knowledge", {}),
            }

    def retrieve(self, request_id: str) -> RequestEnvelope | None:
        return self._requests.get(request_id)

    def find_similar(
        self,
        intent: Intent | str,
        limit: int = 5,
    ) -> list[RequestEnvelope]:
        intent_str = intent.value if isinstance(intent, Intent) else intent
        
        similar = []
        for req in self._requests.values():
            req_intent = req.intent.value if isinstance(req.intent, Intent) else req.intent
            if req_intent == intent_str:
                similar.append(req)
                
        return similar[:limit]


class PrismEngine:
    """The complete PrismPipe execution engine with organic request processing."""

    def __init__(self) -> None:
        self.router = CapabilityRouter()
        self.replay_engine = ReplayEngine()
        self.diff_engine = DiffEngine()
        self.capability_graph = CapabilityGraph()
        self.remote_executor = RemoteExecutor()
        self.stream_manager = StreamManager()
        self.cost_optimizer = CostOptimizer()
        self.request_memory = RequestMemory()
        self.ancestry_tree = AncestryTree()
        self.semantic_cache = SemanticCache()
        
        self.computation_graph = ComputationGraph()
        self.organism_registry = OrganismRegistry()
        self.pipeline_evolver = PipelineEvolver()
        self.intent_planner = IntentPlanner(self.router)
        self.swarm_coordinator = SwarmCoordinator()
        self.gravity_engine = GravityEngine()
        self.organism_executor = OrganismExecutor(self.router)

    def register_node(
        self,
        node: Node,
        spec: CapabilitySpec | None = None,
        cost: NodeCost | None = None,
    ) -> "PrismEngine":
        self.router.register(node.capability, node)
        
        if spec:
            self.capability_graph.register(spec)
            self.intent_planner.register_capability(
                spec.capability,
                spec.description,
            )
            
        if cost:
            self.cost_optimizer.register_cost(cost)
            
        return self

    def spawn_organism(
        self,
        intent: Intent | str,
        input_data: dict[str, Any] | None = None,
        initial_capability: str | None = None,
        parent_organism_id: str | None = None,
    ) -> Organism:
        organism = Organism(
            intent=intent,
            input_data=input_data,
            initial_capability=initial_capability,
            parent_organism_id=parent_organism_id,
        )
        
        if initial_capability:
            organism._plan.add(initial_capability)
        
        self.organism_registry.register(organism)
        return organism

    async def execute_organism(
        self,
        organism: Organism,
        use_computation_sharing: bool = True,
    ) -> Organism:
        computation = self.computation_graph if use_computation_sharing else ComputationGraph()
        return await self.organism_executor.execute(organism, computation)

    async def execute_organism_time_split(
        self,
        organism: Organism,
        branch_capabilities: list[str],
    ) -> Organism:
        splitter = TimeSplitter(self.router)
        return await splitter.execute_time_split(organism, branch_capabilities, self.computation_graph)

    def create_swarm(
        self,
        template: Organism,
        count: int,
        swarm_id: str | None = None,
    ) -> list[Organism]:
        swarm_id = swarm_id or f"swarm_{uuid.uuid4().hex[:8]}"
        return self.swarm_coordinator.create_swarm(swarm_id, template, count)

    async def execute_swarm(
        self,
        swarm_id: str,
        capability: str,
        data: list[Any],
    ) -> Any:
        return await self.swarm_coordinator.execute_swarm(
            swarm_id, capability, self.computation_graph, data
        )

    def plan_intent(self, intent: str) -> list[str]:
        return self.intent_planner.plan(intent)

    def evolve_pipeline(
        self,
        intent: Intent | str,
        available_pipelines: list[list[str]] | None = None,
    ) -> list[str]:
        return self.pipeline_evolver.evolve(intent, available_pipelines)

    def inherit_from_similar(
        self,
        organism: Organism,
    ) -> Organism:
        similar = self.organism_registry.find_similar(organism)
        
        if similar:
            best = similar[0]
            organism.inherit_from(best, inherit_state=True, inherit_knowledge=True)
            
        return organism

    async def execute(self, envelope: RequestEnvelope) -> RequestEnvelope:
        intent_str = envelope.intent.value if isinstance(envelope.intent, Intent) else str(envelope.intent)
        
        cached = self.semantic_cache.get(intent_str, envelope.input)
        
        if cached:
            envelope.state["_from_cache"] = True
            envelope.state["_cached_result"] = cached.state
            return envelope
            
        start_state = dict(envelope.state)
        
        while envelope.next and not envelope.terminated:
            capability = envelope.get_capability()
            if not capability:
                break
                
            if self.remote_executor.is_remote(capability):
                envelope = await self.remote_executor.execute_remote(capability, envelope)
            else:
                try:
                    node = self.router.resolve(capability)
                    result = node.execute(envelope)
                    envelope = result.envelope
                    
                    diff = self.diff_engine.compute_diff(envelope, start_state)
                    self.diff_engine.record(envelope, diff)
                    
                except NodeNotFoundError:
                    envelope.terminate(f"Node not found: {capability}")
                    break
                    
            if envelope.plan and envelope.plan.capabilities:
                if not envelope.next:
                    envelope.next = envelope.plan.next()
                    
        self.request_memory.store(envelope)
        self.ancestry_tree.add_child(envelope.parent_id or "", envelope.id)
        
        if envelope.history:
            path = [h.capability for h in envelope.history]
            self.semantic_cache.set(intent_str, envelope.input, envelope)
            
        self.replay_engine.snapshot(envelope, f"final_{envelope.id}")
        
        return envelope


class OrganismMutation:
    """Tracks state mutations in an organism - like git diff for requests."""
    
    def __init__(self, organism_id: str):
        self.organism_id = organism_id
        self.changes: list[dict[str, Any]] = []
        self._previous_state: dict[str, Any] = {}
    
    def record_change(
        self,
        capability: str,
        key: str,
        old_value: Any,
        new_value: Any,
    ) -> None:
        change = {
            "capability": capability,
            "key": key,
            "old_value": old_value,
            "new_value": new_value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.changes.append(change)
        self._previous_state[key] = new_value
    
    def get_timeline(self) -> list[dict[str, Any]]:
        return self.changes
    
    def get_changed_keys(self) -> set[str]:
        return {c["key"] for c in self.changes}


class StreamingOrganism:
    """An organism that can emit partial results in real-time."""
    
    def __init__(self, organism: Organism):
        self.organism = organism
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._partial_results: list[dict[str, Any]] = []
    
    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._subscribers.append(callback)
    
    def emit_partial(self, data: dict[str, Any]) -> None:
        result = {
            "data": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "organism_id": self.organism.id,
        }
        self._partial_results.append(result)
        
        for callback in self._subscribers:
            try:
                callback(result)
            except Exception:
                pass
    
    def get_partial_results(self) -> list[dict[str, Any]]:
        return self._partial_results


class OrganismPersistence:
    """Persist organisms to storage - they can be hibernated and resumed."""
    
    def __init__(self, storage_backend: Any = None):
        self._storage = storage_backend
        self._persisted: dict[str, dict[str, Any]] = {}
    
    def hibernate(self, organism: Organism) -> str:
        """Save organism state and return hibernation ID."""
        hibernation_id = f"hib_{uuid.uuid4().hex[:8]}"
        
        data = {
            "id": organism.id,
            "intent": organism.intent.value if isinstance(organism.intent, Intent) else str(organism.intent),
            "input": organism.input,
            "state": organism.state,
            "history": [h.model_dump() for h in organism.history],
            "knowledge": [
                {"key": k.key, "value": k.value, "confidence": k.confidence}
                for k in organism.knowledge
            ],
            "_next_capability": organism._next_capability,
            "_parent_organism_id": organism._parent_organism_id,
            "created_at": organism._created_at.isoformat(),
            "hibernated_at": datetime.now(timezone.utc).isoformat(),
        }
        
        self._persisted[hibernation_id] = data
        
        if self._storage:
            # TODO: Write to actual storage
            pass
        
        return hibernation_id
    
    def wake(self, hibernation_id: str) -> Organism | None:
        """Restore organism from hibernation."""
        if hibernation_id not in self._persisted:
            return None
        
        data = self._persisted[hibernation_id]
        
        organism = Organism(
            intent=data["intent"],
            input_data=data["input"],
            parent_organism_id=data.get("_parent_organism_id"),
        )
        organism.id = data["id"]
        organism.state = data["state"]
        organism._next_capability = data.get("_next_capability")
        
        organism.history = [HistoryEntry(**h) for h in data.get("history", [])]
        
        for k in data.get("knowledge", []):
            organism.ingest_knowledge(k["key"], k["value"], k["confidence"])
        
        return organism
    
    def list_hibernated(self) -> list[str]:
        return list(self._persisted.keys())


class EventDrivenOrganism:
    """An organism that reacts to events - like event sourcing for requests."""
    
    def __init__(self, organism: Organism):
        self.organism = organism
        self._event_handlers: dict[str, list[Callable]] = defaultdict(list)
        self._event_history: list[dict[str, Any]] = []
    
    def on(self, event_type: str, handler: Callable) -> None:
        self._event_handlers[event_type].append(handler)
    
    def emit_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        event = {
            "type": event_type,
            "data": data or {},
            "organism_id": self.organism.id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._event_history.append(event)
        
        for handler in self._event_handlers.get(event_type, []):
            try:
                handler(event)
            except Exception:
                pass
    
    def get_event_history(self) -> list[dict[str, Any]]:
        return self._event_history


class MigratableOrganism:
    """An organism that can migrate between execution nodes."""
    
    def __init__(self, organism: Organism):
        self.organism = organism
        self._current_node: str | None = None
        self._migration_history: list[dict[str, Any]] = []
    
    def migrate_to(self, node_id: str) -> None:
        migration = {
            "from_node": self._current_node,
            "to_node": node_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "organism_state": {
                "id": self.organism.id,
                "state_keys": list(self.organism.state.keys()),
            }
        }
        self._migration_history.append(migration)
        self._current_node = node_id
    
    def get_current_node(self) -> str | None:
        return self._current_node
    
    def get_migration_history(self) -> list[dict[str, Any]]:
        return self._migration_history


class OrganismWatcher:
    """
    WATCH ORGANISMS IN REAL-TIME.
    
    Monitor organisms for debugging, analytics, or reactive patterns.
    """
    
    def __init__(self):
        self._watchers: dict[str, list[Callable[[Organism, str], None]]] = defaultdict(list)
        self._all_events: list[dict[str, Any]] = []
    
    def watch(
        self,
        organism_id: str,
        callback: Callable[[Organism, str], None],
    ) -> None:
        self._watchers[organism_id].append(callback)
    
    def notify(self, organism: Organism, event: str) -> None:
        event_data = {
            "organism_id": organism.id,
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "state": dict(organism.state),
        }
        self._all_events.append(event_data)
        
        for callback in self._watchers.get(organism.id, []):
            try:
                callback(organism, event)
            except Exception:
                pass
    
    def get_events(
        self,
        organism_id: str | None = None,
        event: str | None = None,
    ) -> list[dict[str, Any]]:
        events = self._all_events
        
        if organism_id:
            events = [e for e in events if e.get("organism_id") == organism_id]
        if event:
            events = [e for e in events if e.get("event") == event]
        
        return events


__all__ = [
    "OrganismState",
    "KnowledgeAtom",
    "ComputationNode",
    "ComputationGraph",
    "Organism",
    "TimeSplitter",
    "PipelineEvolver",
    "IntentPlanner",
    "SwarmCoordinator",
    "OrganismRegistry",
    "GravityEngine",
    "OrganismExecutor",
    "ReplayEngine",
    "DiffEngine",
    "RemoteExecutor",
    "NodeLocation",
    "RemoteNodeConfig",
    "StreamManager",
    "StreamChunk",
    "ParallelExecutor",
    "Branch",
    "BranchResult",
    "CostOptimizer",
    "NodeCost",
    "CapabilityGraph",
    "CapabilitySpec",
    "SemanticCache",
    "AncestryTree",
    "RequestMemory",
    "PrismEngine",
    # New Organic Features
    "OrganismMutation",
    "StreamingOrganism",
    "OrganismPersistence",
    "EventDrivenOrganism",
    "MigratableOrganism",
    "OrganismWatcher",
]
