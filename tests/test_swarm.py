"""Tests for SwarmCoordinator worker execution."""

import pytest

from prismpipe.core.node import Node, NodeResult
from prismpipe.engine import PrismEngine


class PartitionSumNode(Node):
    capability = "swarm.sum"

    def process(self, envelope):
        data = envelope.input.get("partition_data", [])
        envelope.state["partition_result"] = sum(data)
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


@pytest.mark.asyncio
async def test_swarm_runs_workers_and_reduces():
    engine = PrismEngine()
    engine.register_node(PartitionSumNode())

    template = engine.spawn_organism(
        intent="swarm",
        input_data={},
        initial_capability="swarm.sum",
    )
    workers = engine.create_swarm(template, count=4, swarm_id="s1")
    assert len(workers) == 4

    engine.swarm_coordinator.set_partition_fn(lambda x: str(x % 4))
    engine.swarm_coordinator.register_reducer("s1", lambda results: sum(results))

    data = list(range(20))
    reduced = await engine.execute_swarm("s1", "swarm.sum", data)

    assert reduced == sum(data)
    stats = engine.swarm_coordinator.get_last_swarm_stats()
    assert stats["worker_count"] == 4
    assert stats["error_count"] == 0


@pytest.mark.asyncio
async def test_swarm_fail_fast_raises():
    class BoomNode(Node):
        capability = "swarm.boom"

        def process(self, envelope):
            raise RuntimeError("boom")

    engine = PrismEngine()
    engine.register_node(BoomNode())
    template = engine.spawn_organism(intent="swarm", input_data={})
    engine.create_swarm(template, count=2, swarm_id="s2")

    with pytest.raises(RuntimeError, match="boom"):
        await engine.execute_swarm("s2", "swarm.boom", [1, 2, 3], fail_fast=True)
