"""PrismPipe Engine - The complete execution system with all advanced features."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
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
    """The complete PrismPipe execution engine."""

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

    def register_node(
        self,
        node: Node,
        spec: CapabilitySpec | None = None,
        cost: NodeCost | None = None,
    ) -> "PrismEngine":
        self.router.register(node.capability, node)
        
        if spec:
            self.capability_graph.register(spec)
            
        if cost:
            self.cost_optimizer.register_cost(cost)
            
        return self

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


__all__ = [
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
]
