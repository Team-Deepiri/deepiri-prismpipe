"""Tests for Redis-backed ComputationGraph + single-flight coalescing."""

from __future__ import annotations

import asyncio

import pytest

from prismpipe.core.node import Node, NodeResult
from prismpipe.engine import ComputationGraph, PrismEngine


class SlowCountingNode(Node):
    capability = "compute.slow"
    call_count = 0

    def process(self, envelope):
        SlowCountingNode.call_count += 1
        import time

        time.sleep(0.05)
        envelope.state["result"] = int(envelope.input.get("n", 0)) * 2
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def ping(self):
        return True

    def get(self, key: str):
        return self.store.get(key)

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, key: str):
        self.store.pop(key, None)
        return 1


def test_redis_backed_graph_shares_across_instances():
    redis = FakeRedis()
    g1 = ComputationGraph(redis_client=redis, ttl_seconds=30)
    g1.register_computation(
        capability="x",
        input_data={"a": 1},
        output_data={"out": 9},
        latency_ms=1.0,
        success=True,
        next_capability=None,
    )
    g2 = ComputationGraph(redis_client=redis, ttl_seconds=30)
    cached = g2.get_cached_result("x", {"a": 1})
    assert cached is not None
    assert cached.state["out"] == 9
    stats = g2.get_deduplication_stats()
    assert stats["redis_enabled"] is True
    assert stats["redis_hits"] >= 1


@pytest.mark.asyncio
async def test_single_flight_coalesces_concurrent_misses():
    SlowCountingNode.call_count = 0
    engine = PrismEngine()
    engine.register_node(SlowCountingNode())

    async def one():
        org = engine.spawn_organism(
            intent="compute",
            input_data={"n": 7},
            initial_capability="compute.slow",
        )
        await engine.execute_organism(org)
        return org.state.get("result"), org.state.get("_from_single_flight")

    results = await asyncio.gather(*[one() for _ in range(8)])
    assert all(r[0] == 14 for r in results)
    # Only one real node execute; waiters reuse via cache/single-flight
    assert SlowCountingNode.call_count == 1
    assert any(r[1] for r in results)


def test_negative_cache_uses_short_ttl(monkeypatch):
    monkeypatch.setenv("COMPUTATION_NEGATIVE_TTL_S", "2")
    g = ComputationGraph(ttl_seconds=30)
    assert g.ttl_for("deepiri.session.bootstrap", success=False) == 2
    assert g.ttl_for("deepiri.health.parallel", success=True) == 30


def test_l1_cache_expires(monkeypatch):
    monkeypatch.setenv("COMPUTATION_NEGATIVE_TTL_S", "1")
    g = ComputationGraph(ttl_seconds=30)
    g.register_computation(
        capability="x",
        input_data={"a": 1},
        output_data={"out": "fail"},
        latency_ms=1.0,
        success=False,
        next_capability=None,
    )
    assert g.get_cached_result("x", {"a": 1}) is not None
    # Force expiry without sleeping the full TTL.
    node_id = next(iter(g._outputs))
    g._outputs[node_id]["expires_at"] = 0
    assert g.get_cached_result("x", {"a": 1}) is None


def test_flight_lock_nx(monkeypatch):
    redis = FakeRedis()
    g = ComputationGraph(redis_client=redis, ttl_seconds=30)
    assert g.try_acquire_flight_lock("cap:hash", 5) is True
    assert g.try_acquire_flight_lock("cap:hash", 5) is False
    g.release_flight_lock("cap:hash")
    assert g.try_acquire_flight_lock("cap:hash", 5) is True
