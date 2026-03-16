"""PrismPipe Swarm Computing - Multiple envelopes collaborate."""

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from collections import defaultdict

from prismpipe.core import RequestEnvelope


@dataclass
class SwarmEnvelope:
    """An envelope participating in swarm computation."""
    id: str
    envelope: RequestEnvelope
    partition_key: str | None = None
    partition_index: int = 0
    total_partitions: int = 1


@dataclass
class SwarmResult:
    """Result from swarm computation."""
    swarm_id: str
    results: list[Any]
    merged_data: dict[str, Any] = field(default_factory=dict)
    partition_results: dict[int, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    success: bool = True
    error: str | None = None


class SwarmEngine:
    """
    Enable multiple envelopes to collaborate on problems.
    
    Implements map-reduce style computation across envelopes.
    """

    def __init__(self):
        self._active_swarms: dict[str, list[SwarmEnvelope]] = {}
        self._shared_state: dict[str, dict[str, Any]] = {}

    async def split_and_execute(
        self,
        envelope: RequestEnvelope,
        data_list: list[Any],
        process_fn: Callable[[RequestEnvelope, Any], Awaitable[Any]],
        partition_key: str | None = None,
        max_concurrent: int = 10
    ) -> SwarmResult:
        """Split work across multiple envelope instances."""
        import time
        start = time.perf_counter()

        swarm_id = str(uuid.uuid4())
        
        # Create partitions
        partitions = []
        for i, data in enumerate(data_list):
            # Clone envelope for each partition
            partition_envelope = envelope.model_copy(deep=True)
            partition_envelope.id = f"{envelope.id or swarm_id}_p{i}"
            partition_envelope.input = {**partition_envelope.input, "_partition_data": data}
            
            partitions.append(SwarmEnvelope(
                id=partition_envelope.id,
                envelope=partition_envelope,
                partition_key=partition_key,
                partition_index=i,
                total_partitions=len(data_list)
            ))

        self._active_swarms[swarm_id] = partitions

        # Execute with concurrency limit
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def execute_partition(pe: SwarmEnvelope):
            async with semaphore:
                try:
                    return await process_fn(pe.envelope, pe.envelope.input.get("_partition_data"))
                except Exception as e:
                    return {"error": str(e), "partition": pe.partition_index}

        # Run all partitions
        tasks = [execute_partition(p) for p in partitions]
        partition_results = await asyncio.gather(*tasks)

        duration = (time.perf_counter() - start) * 1000

        # Clean up
        del self._active_swarms[swarm_id]

        return SwarmResult(
            swarm_id=swarm_id,
            results=partition_results,
            partition_results={i: r for i, r in enumerate(partition_results)},
            duration_ms=duration,
            success=all(not isinstance(r, dict) or "error" not in r for r in partition_results)
        )

    async def gather_with_shared_state(
        self,
        envelope: RequestEnvelope,
        compute_fn: Callable[[RequestEnvelope, dict[str, Any]], Awaitable[Any]],
        shared_keys: list[str]
    ) -> dict[str, Any]:
        """Execute with shared state across envelopes."""
        swarm_id = envelope.id or str(uuid.uuid4())
        
        # Initialize shared state
        self._shared_state[swarm_id] = {k: None for k in shared_keys}
        
        try:
            result = await compute_fn(envelope, self._shared_state[swarm_id])
            return result
        finally:
            # Cleanup
            if swarm_id in self._shared_state:
                del self._shared_state[swarm_id]

    def update_shared_state(self, swarm_id: str, key: str, value: Any) -> None:
        """Update shared state for a swarm."""
        if swarm_id in self._shared_state:
            self._shared_state[swarm_id][key] = value

    def get_shared_state(self, swarm_id: str) -> dict[str, Any]:
        """Get shared state for a swarm."""
        return self._shared_state.get(swarm_id, {})

    async def collective_decision(
        self,
        envelopes: list[RequestEnvelope],
        decide_fn: Callable[[RequestEnvelope], Awaitable[bool]]
    ) -> dict[str, Any]:
        """Make collective decision across envelopes."""
        results = await asyncio.gather(*[decide_fn(e) for e in envelopes])
        
        true_count = sum(1 for r in results if r)
        false_count = len(results) - true_count
        
        return {
            "total": len(results),
            "true": true_count,
            "false": false_count,
            "decision": true_count > false_count,
            "unanimous": true_count == len(results) or false_count == len(results)
        }

    def get_swarm_status(self, swarm_id: str) -> dict[str, Any]:
        """Get status of a swarm."""
        partitions = self._active_swarms.get(swarm_id, [])
        return {
            "swarm_id": swarm_id,
            "partition_count": len(partitions),
            "partitions": [
                {
                    "id": p.id,
                    "index": p.partition_index,
                    "key": p.partition_key
                }
                for p in partitions
            ]
        }


# Global instance
_default_swarm_engine: SwarmEngine | None = None


def get_swarm_engine() -> SwarmEngine:
    """Get default swarm engine."""
    global _default_swarm_engine
    if _default_swarm_engine is None:
        _default_swarm_engine = SwarmEngine()
    return _default_swarm_engine


def set_swarm_engine(engine: SwarmEngine) -> None:
    """Set default swarm engine."""
    global _default_swarm_engine
    _default_swarm_engine = engine
