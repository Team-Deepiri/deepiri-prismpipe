"""Tests for Redis-backed ComputationGraph + single-flight coalescing."""

from __future__ import annotations

import asyncio
import math
import time

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
    """In-memory stand-in that honours `ex` — without expiry it cannot model
    the case where a hydrated L1 copy outlives the key it came from."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.expiry: dict[str, float] = {}

    def ping(self):
        return True

    def _sweep(self, key: str) -> bool:
        exp = self.expiry.get(key)
        if exp is not None and time.time() >= exp:
            self.store.pop(key, None)
            self.expiry.pop(key, None)
            return True
        return False

    def get(self, key: str):
        self._sweep(key)
        return self.store.get(key)

    def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ):
        self._sweep(key)
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is None:
            self.expiry.pop(key, None)
        else:
            self.expiry[key] = time.time() + ex
        return True

    def ttl(self, key: str) -> int:
        self._sweep(key)
        if key not in self.store:
            return -2
        exp = self.expiry.get(key)
        # Real Redis rounds the remaining TTL up to whole seconds.
        return -1 if exp is None else max(0, math.ceil(exp - time.time()))

    def delete(self, key: str):
        self.store.pop(key, None)
        self.expiry.pop(key, None)
        return 1

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._ops: list[tuple[str, str]] = []

    def get(self, key: str):
        self._ops.append(("get", key))
        return self

    def ttl(self, key: str):
        self._ops.append(("ttl", key))
        return self

    def execute(self):
        return [getattr(self._redis, op)(key) for op, key in self._ops]


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


def test_hydrated_entry_expires_with_the_key_it_came_from(monkeypatch):
    """A worker that hydrates from Redis must not pin the result forever.

    Regression: _hydrate_from_redis stored the L1 copy without `expires_at`,
    and get_cached_result reads a missing `expires_at` as "never expires". On a
    multi-worker deploy the worker that hydrated kept serving a cached session
    long after both the Redis key and its TTL were gone, so the capability TTL
    was not actually enforced for that worker.
    """
    # deepiri.session.bootstrap carries its own per-capability TTL, so the
    # constructor's ttl_seconds does not apply to it.
    monkeypatch.setenv("COMPUTATION_TTL_SESSION_S", "1")
    redis = FakeRedis()
    writer = ComputationGraph(redis_client=redis, ttl_seconds=1)
    writer.register_computation(
        capability="deepiri.session.bootstrap",
        input_data={"authorization": "Bearer t"},
        output_data={"session": {"authenticated": True}},
        latency_ms=1.0,
        success=True,
        next_capability=None,
    )

    reader = ComputationGraph(redis_client=redis, ttl_seconds=1)
    hydrated = reader.get_cached_result(
        "deepiri.session.bootstrap", {"authorization": "Bearer t"}
    )
    assert hydrated is not None, "reader should hydrate the writer's entry"

    time.sleep(1.1)

    assert reader.get_cached_result(
        "deepiri.session.bootstrap", {"authorization": "Bearer t"}
    ) is None, "hydrated L1 copy outlived the Redis TTL it was cached under"


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


def test_l1_cache_is_lru_bounded(monkeypatch):
    """The L1 dicts must not grow one entry per distinct input, forever.

    Entries were only dropped when a lookup happened to find them expired, so an
    input nobody queried again was never swept. Eviction must also drop the
    matching _hash_to_node / _nodes rows, not just _outputs.
    """
    monkeypatch.setenv("COMPUTATION_CACHE_MAX_ENTRIES", "16")
    g = ComputationGraph(ttl_seconds=300)
    for i in range(200):
        g.register_computation(
            capability="x",
            input_data={"i": i},
            output_data={"out": i},
            latency_ms=1.0,
            success=True,
            next_capability=None,
        )

    assert len(g._outputs) == 16
    assert len(g._hash_to_node) == 16, "eviction leaked _hash_to_node rows"
    assert len(g._nodes) == 16, "eviction leaked _nodes rows"

    stats = g.get_deduplication_stats()
    assert stats["evictions"] == 184
    assert stats["l1_entries"] == 16

    # Most recent survives, oldest is gone.
    assert g.get_cached_result("x", {"i": 199}) is not None
    assert g.get_cached_result("x", {"i": 0}) is None


def test_lru_keeps_recently_read_entries(monkeypatch):
    monkeypatch.setenv("COMPUTATION_CACHE_MAX_ENTRIES", "4")
    g = ComputationGraph(ttl_seconds=300)
    for i in range(4):
        g.register_computation(
            capability="x", input_data={"i": i}, output_data={"out": i},
            latency_ms=1.0, success=True, next_capability=None,
        )

    assert g.get_cached_result("x", {"i": 0}) is not None  # refresh recency

    g.register_computation(
        capability="x", input_data={"i": 99}, output_data={"out": 99},
        latency_ms=1.0, success=True, next_capability=None,
    )

    assert g.get_cached_result("x", {"i": 0}) is not None, "recently read entry evicted"
    assert g.get_cached_result("x", {"i": 1}) is None, "LRU victim should be i=1"


def test_flight_lock_nx(monkeypatch):
    redis = FakeRedis()
    g = ComputationGraph(redis_client=redis, ttl_seconds=30)
    assert g.try_acquire_flight_lock("cap:hash", 5) is True
    assert g.try_acquire_flight_lock("cap:hash", 5) is False
    g.release_flight_lock("cap:hash")
    assert g.try_acquire_flight_lock("cap:hash", 5) is True


@pytest.mark.asyncio
async def test_self_routing_capability_terminates(monkeypatch):
    """Capability routing is cyclic by design, so it must be hop-bounded.

    Regression: a node that set_next() to its own capability spun the organism
    executor forever (measured 3,652 hops in 5s with no termination), which
    would hang a worker the first time a fault zone re-routed to itself.
    """
    monkeypatch.setenv("ORGANISM_MAX_HOPS", "25")

    class SelfRoutingNode(Node):
        capability = "loop.self"
        calls = 0

        def process(self, envelope):
            SelfRoutingNode.calls += 1
            envelope.state["n"] = SelfRoutingNode.calls
            envelope.set_next("loop.self")
            return NodeResult(envelope=envelope)

    engine = PrismEngine()
    engine.register_node(SelfRoutingNode())
    org = engine.spawn_organism(
        intent="loop", input_data={}, initial_capability="loop.self"
    )

    result = await asyncio.wait_for(engine.execute_organism(org), timeout=20.0)

    assert result.terminated
    assert SelfRoutingNode.calls <= 26, f"ran {SelfRoutingNode.calls} hops past the bound"
