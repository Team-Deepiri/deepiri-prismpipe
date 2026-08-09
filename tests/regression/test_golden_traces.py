"""Regression golden traces for organism capability chains."""

import hashlib
import json

import pytest

from prismpipe.core.node import Node, NodeResult
from prismpipe.engine import PrismEngine


class GoldenA(Node):
    capability = "golden.a"

    def process(self, envelope):
        envelope.state["a"] = envelope.input.get("seed", 0) + 1
        envelope.set_next("golden.b")
        return NodeResult(envelope=envelope)


class GoldenB(Node):
    capability = "golden.b"

    def process(self, envelope):
        envelope.state["b"] = envelope.state["a"] * 3
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


def _state_hash(state: dict) -> str:
    public = {k: v for k, v in state.items() if not str(k).startswith("_")}
    payload = json.dumps(public, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


@pytest.mark.asyncio
async def test_golden_trace_hash_stable():
    engine = PrismEngine()
    engine.register_node(GoldenA())
    engine.register_node(GoldenB())

    org = engine.spawn_organism(
        intent="golden",
        input_data={"seed": 10},
        initial_capability="golden.a",
    )
    await engine.execute_organism(org, use_computation_sharing=False)

    assert org.state["a"] == 11
    assert org.state["b"] == 33
    expected = hashlib.sha256(
        json.dumps({"a": 11, "b": 33}, sort_keys=True).encode()
    ).hexdigest()[:16]
    assert _state_hash(org.state) == expected


@pytest.mark.asyncio
async def test_chaos_fail_fast_swarm():
    class Boom(Node):
        capability = "chaos.boom"

        def process(self, envelope):
            raise RuntimeError("chaos")

    engine = PrismEngine()
    engine.register_node(Boom())
    template = engine.spawn_organism(intent="chaos", input_data={})
    engine.create_swarm(template, count=3, swarm_id="chaos")
    with pytest.raises(RuntimeError):
        await engine.execute_swarm("chaos", "chaos.boom", [1, 2, 3], fail_fast=True)
