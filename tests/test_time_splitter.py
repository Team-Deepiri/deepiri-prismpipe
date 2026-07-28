"""Tests for TimeSplitter winner selection and branch cancellation."""

import asyncio
import time

import pytest

from prismpipe.core.node import Node, NodeResult
from prismpipe.engine import PrismEngine


class FastNode(Node):
    capability = "branch.fast"

    def process(self, envelope):
        envelope.state["winner_path"] = "fast"
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class SlowNode(Node):
    capability = "branch.slow"

    def process(self, envelope):
        time.sleep(0.2)
        envelope.state["winner_path"] = "slow"
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class FailNode(Node):
    capability = "branch.fail"

    def process(self, envelope):
        envelope.terminate("intentional fail")
        return NodeResult(envelope=envelope, success=False, error="intentional fail")


@pytest.mark.asyncio
async def test_time_split_picks_first_successful_and_cancels_losers():
    engine = PrismEngine()
    engine.register_node(FastNode())
    engine.register_node(SlowNode())
    engine.register_node(FailNode())

    org = engine.spawn_organism(intent="split", input_data={})
    start = time.perf_counter()
    result = await engine.execute_organism_time_split(
        org,
        ["branch.fast", "branch.slow", "branch.fail"],
        timeout_ms=2000,
    )
    elapsed = time.perf_counter() - start

    assert not result.terminated
    assert result.state.get("winner_path") == "fast"
    assert elapsed < 0.15


@pytest.mark.asyncio
async def test_time_split_timeout_terminates_slow_branches():
    class VerySlowNode(Node):
        capability = "branch.veryslow"

        def process(self, envelope):
            time.sleep(1.0)
            envelope.state["winner_path"] = "veryslow"
            envelope.set_next(None)
            return NodeResult(envelope=envelope)

    engine = PrismEngine()
    engine.register_node(VerySlowNode())
    engine.register_node(FailNode())

    org = engine.spawn_organism(intent="split", input_data={})
    result = await engine.execute_organism_time_split(
        org,
        ["branch.veryslow", "branch.fail"],
        timeout_ms=50,
    )
    assert result.terminated
