"""Tests for ComputationGraph output restore and organism dedup."""

import pytest

from prismpipe.core.node import Node, NodeResult
from prismpipe.engine import ComputationGraph, Organism, OrganismExecutor, PrismEngine


class CountingNode(Node):
    capability = "compute.value"
    call_count = 0

    def process(self, envelope):
        CountingNode.call_count += 1
        value = envelope.input.get("n", 0) * 2
        envelope.state["result"] = value
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


@pytest.mark.asyncio
async def test_cached_result_restores_outputs_without_rerunning_node():
    CountingNode.call_count = 0
    engine = PrismEngine()
    engine.register_node(CountingNode())

    org1 = engine.spawn_organism(
        intent="compute",
        input_data={"n": 21},
        initial_capability="compute.value",
    )
    await engine.execute_organism(org1)
    assert org1.state["result"] == 42
    assert CountingNode.call_count == 1

    org2 = engine.spawn_organism(
        intent="compute",
        input_data={"n": 21},
        initial_capability="compute.value",
    )
    await engine.execute_organism(org2)

    assert org2.state["result"] == 42
    assert org2.state.get("_from_shared") is True
    assert CountingNode.call_count == 1

    stats = engine.computation_graph.get_deduplication_stats()
    assert stats["hits"] >= 1
    assert stats["hit_ratio"] > 0


@pytest.mark.asyncio
async def test_divergent_inputs_miss_cache():
    CountingNode.call_count = 0
    engine = PrismEngine()
    engine.register_node(CountingNode())

    org1 = engine.spawn_organism(
        intent="compute",
        input_data={"n": 1},
        initial_capability="compute.value",
    )
    await engine.execute_organism(org1)

    org2 = engine.spawn_organism(
        intent="compute",
        input_data={"n": 2},
        initial_capability="compute.value",
    )
    await engine.execute_organism(org2)

    assert org1.state["result"] == 2
    assert org2.state["result"] == 4
    assert CountingNode.call_count == 2
    assert org2.state.get("_from_shared") is not True


def test_register_and_get_cached_result_roundtrip():
    graph = ComputationGraph()
    graph.register_computation(
        capability="x",
        input_data={"a": 1},
        output_data={"out": 9},
        latency_ms=1.0,
        success=True,
        next_capability=None,
    )
    cached = graph.get_cached_result("x", {"a": 1})
    assert cached is not None
    assert cached.state["out"] == 9
    stats = graph.get_deduplication_stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 0
