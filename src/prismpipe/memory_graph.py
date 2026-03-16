"""PrismPipe Request Memory Graph - Experience reuse system."""

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from collections import defaultdict

from prismpipe.core import RequestEnvelope


@dataclass
class RequestNode:
    """A node in the request memory graph."""
    id: str
    intent: str
    input_hash: str
    state_keys: list[str]
    path: list[str]
    success: bool
    latency_ms: float
    created_at: float = field(default_factory=time.time)
    parent_id: str | None = None
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SimilarRequest:
    """A similar request found in memory."""
    request_id: str
    similarity: float
    path: list[str]
    success: bool
    avg_latency_ms: float


class RequestMemoryGraph:
    """
    Store requests in a graph for experience reuse.
    
    New requests can find similar past requests and inherit computation.
    """

    def __init__(self):
        self._nodes: dict[str, RequestNode] = {}
        self._intent_index: dict[str, list[str]] = defaultdict(list)
        self._input_hash_index: dict[str, list[str]] = defaultdict(list)
        self._path_index: dict[str, list[str]] = defaultdict(list)

    def add_request(self, envelope: RequestEnvelope, success: bool, latency_ms: float) -> str:
        """Add a request to the memory graph."""
        request_id = envelope.id or str(uuid.uuid4())
        
        input_hash = self._hash_input(envelope.input)
        
        node = RequestNode(
            id=request_id,
            intent=envelope.intent,
            input_hash=input_hash,
            state_keys=list(envelope.state.keys()),
            path=envelope.plan or [],
            success=success,
            latency_ms=latency_ms,
            parent_id=envelope.parent_id,
        )

        self._nodes[request_id] = node
        
        # Index for fast lookup
        self._intent_index[envelope.intent].append(request_id)
        self._input_hash_index[input_hash].append(request_id)
        
        for cap in node.path:
            self._path_index[cap].append(request_id)

        return request_id

    def find_similar(
        self,
        intent: str | None = None,
        input_data: dict[str, Any] | None = None,
        path: list[str] | None = None,
        limit: int = 5
    ) -> list[SimilarRequest]:
        """Find similar requests in the graph."""
        candidates: dict[str, float] = defaultdict(lambda: 0.0)
        
        # Score by intent
        if intent:
            for req_id in self._intent_index.get(intent, []):
                candidates[req_id] += 0.4

        # Score by input hash
        if input_data:
            input_hash = self._hash_input(input_data)
            for req_id in self._input_hash_index.get(input_hash, []):
                candidates[req_id] += 0.4

        # Score by path
        if path:
            path_set = set(path)
            for cap in path_set:
                for req_id in self._path_index.get(cap, []):
                    candidates[req_id] += 0.1 / len(path_set)

        # Calculate final similarity scores
        results = []
        for req_id, score in sorted(candidates.items(), key=lambda x: x[1], reverse=True)[:limit]:
            node = self._nodes.get(req_id)
            if node:
                results.append(SimilarRequest(
                    request_id=req_id,
                    similarity=min(1.0, score),
                    path=node.path,
                    success=node.success,
                    avg_latency_ms=node.latency_ms
                ))

        return results

    def inherit_state(self, source_request_id: str, target_envelope: RequestEnvelope) -> dict[str, Any]:
        """Inherit computed state from a similar request."""
        source = self._nodes.get(source_request_id)
        if not source:
            return {}

        # Return the state keys that were computed
        return {
            "inherited_keys": source.state_keys,
            "source_request": source_request_id,
            "path_used": source.path
        }

    def get_ancestry(self, request_id: str) -> list[RequestNode]:
        """Get full ancestry chain for a request."""
        ancestry = []
        current_id = request_id
        
        while current_id and current_id in self._nodes:
            node = self._nodes[current_id]
            ancestry.append(node)
            current_id = node.parent_id

        return ancestry

    def get_execution_patterns(self) -> dict[str, dict[str, Any]]:
        """Analyze execution patterns across all requests."""
        patterns: dict[str, dict[str, Any]] = defaultdict(lambda: {
            'count': 0,
            'successes': 0,
            'total_latency': 0.0,
            'paths': defaultdict(int)
        })

        for node in self._nodes.values():
            intent = node.intent
            patterns[intent]['count'] += 1
            if node.success:
                patterns[intent]['successes'] += 1
            patterns[intent]['total_latency'] += node.latency_ms
            
            path_key = "->".join(node.path)
            patterns[intent]['paths'][path_key] += 1

        # Calculate averages
        for intent, data in patterns.items():
            if data['count'] > 0:
                data['success_rate'] = data['successes'] / data['count']
                data['avg_latency'] = data['total_latency'] / data['count']
                # Find most common path
                if data['paths']:
                    data['most_common_path'] = max(data['paths'].items(), key=lambda x: x[1])[0]

        return dict(patterns)

    def _hash_input(self, input_data: dict[str, Any]) -> str:
        """Create a hash of input data for indexing."""
        import json
        return hashlib.sha256(json.dumps(input_data, sort_keys=True).encode()).hexdigest()[:16]

    def size(self) -> int:
        """Return number of requests in memory."""
        return len(self._nodes)

    def clear(self) -> None:
        """Clear all requests from memory."""
        self._nodes.clear()
        self._intent_index.clear()
        self._input_hash_index.clear()
        self._path_index.clear()


# Global instance
_default_memory_graph: RequestMemoryGraph | None = None


def get_request_memory_graph() -> RequestMemoryGraph:
    """Get default request memory graph."""
    global _default_memory_graph
    if _default_memory_graph is None:
        _default_memory_graph = RequestMemoryGraph()
    return _default_memory_graph


def set_request_memory_graph(graph: RequestMemoryGraph) -> None:
    """Set default request memory graph."""
    global _default_memory_graph
    _default_memory_graph = graph
