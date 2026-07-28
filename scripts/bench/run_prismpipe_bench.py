#!/usr/bin/env python3
"""PrismPipe benchmark harness."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from prismpipe.core.node import Node, NodeResult  # noqa: E402
from prismpipe.engine import PrismEngine  # noqa: E402
from prismpipe.storage import MemoryStorage  # noqa: E402


class BenchComputeNode(Node):
    capability = "bench.compute"

    def process(self, envelope):
        n = int(envelope.input.get("n", 1))
        envelope.state["result"] = n * 2
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class PartitionSumNode(Node):
    capability = "bench.partition_sum"

    def process(self, envelope):
        data = envelope.input.get("partition_data", [])
        envelope.state["partition_result"] = sum(data) if isinstance(data, list) else 0
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class FastBranch(Node):
    capability = "bench.fast"

    def process(self, envelope):
        envelope.state["path"] = "fast"
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class SlowBranch(Node):
    capability = "bench.slow"

    def process(self, envelope):
        time.sleep(0.05)
        envelope.state["path"] = "slow"
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


def _engine() -> PrismEngine:
    engine = PrismEngine()
    for node in (BenchComputeNode(), PartitionSumNode(), FastBranch(), SlowBranch()):
        engine.register_node(node)
    engine.organism_persistence = type(engine.organism_persistence)(
        storage_backend=MemoryStorage()
    )
    return engine


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
    return ordered[idx]


async def scenario_dedup_identical_100(cfg: dict[str, Any]) -> dict[str, Any]:
    engine = _engine()
    n = int(cfg["n"])
    start = time.perf_counter()
    for i in range(n):
        org = engine.spawn_organism(
            intent="bench",
            input_data={"n": 42},
            initial_capability="bench.compute",
        )
        await engine.execute_organism(org)
    wall_ms = (time.perf_counter() - start) * 1000
    stats = engine.computation_graph.get_deduplication_stats()
    return {
        "wall_ms": wall_ms,
        "deduplication_ratio": stats["deduplication_ratio"],
        "hit_ratio": stats["hit_ratio"],
        "unique_computations": stats["unique_computations"],
        "hits": stats["hits"],
        "misses": stats["misses"],
    }


async def scenario_lineage_spawn_50(cfg: dict[str, Any]) -> dict[str, Any]:
    engine = _engine()
    parent = engine.spawn_organism(
        intent="bench",
        input_data={"n": 1},
        initial_capability="bench.compute",
    )
    await engine.execute_organism(parent)
    parent.ingest_knowledge("score", parent.state.get("result"), 1.0)

    latencies: list[float] = []
    for _ in range(int(cfg["n"])):
        t0 = time.perf_counter()
        child = parent.spawn_child(patch_input={"n": 1})
        _ = child.get_knowledge("score")
        latencies.append((time.perf_counter() - t0) * 1000)
    return {"avg_ms": statistics.mean(latencies), "p95_ms": _percentile(latencies, 0.95)}


async def scenario_time_split_3branch(cfg: dict[str, Any]) -> dict[str, Any]:
    engine = _engine()
    latencies: list[float] = []
    for _ in range(int(cfg["n"])):
        org = engine.spawn_organism(intent="split", input_data={})
        t0 = time.perf_counter()
        await engine.execute_organism_time_split(
            org, ["bench.fast", "bench.slow"], timeout_ms=2000
        )
        latencies.append((time.perf_counter() - t0) * 1000)
    return {"avg_ms": statistics.mean(latencies), "p95_ms": _percentile(latencies, 0.95)}


async def scenario_swarm_mapreduce_1k(cfg: dict[str, Any]) -> dict[str, Any]:
    engine = _engine()
    template = engine.spawn_organism(intent="swarm", input_data={})
    swarm_id = "bench_swarm"
    engine.create_swarm(template, count=int(cfg["workers"]), swarm_id=swarm_id)
    engine.swarm_coordinator.set_partition_fn(lambda x: str(x % int(cfg["workers"])))
    engine.swarm_coordinator.register_reducer(swarm_id, lambda xs: sum(xs))
    data = list(range(int(cfg["n"])))
    t0 = time.perf_counter()
    reduced = await engine.execute_swarm(swarm_id, "bench.partition_sum", data)
    wall_ms = (time.perf_counter() - t0) * 1000
    swarm_stats = engine.swarm_coordinator.get_last_swarm_stats()
    return {
        "wall_ms": wall_ms,
        "reduced": reduced,
        "expected": sum(data),
        "error_rate": swarm_stats["error_rate"],
        "worker_count": swarm_stats["worker_count"],
    }


async def scenario_persist_hibernate_wake(cfg: dict[str, Any]) -> dict[str, Any]:
    engine = _engine()
    wake_ms: list[float] = []
    for i in range(int(cfg["n"])):
        org = engine.spawn_organism(
            intent="persist",
            input_data={"n": i},
            initial_capability="bench.compute",
        )
        await engine.execute_organism(org)
        hib_id = await engine.organism_persistence.hibernate(org)
        t0 = time.perf_counter()
        restored = await engine.organism_persistence.wake(hib_id)
        wake_ms.append((time.perf_counter() - t0) * 1000)
        assert restored is not None
    return {"avg_wake_ms": statistics.mean(wake_ms), "p95_wake_ms": _percentile(wake_ms, 0.95)}


async def scenario_http_organism_execute_p95(cfg: dict[str, Any]) -> dict[str, Any]:
    """In-process HTTP-equivalent path (spawn+execute) under concurrency."""
    engine = _engine()
    n = int(cfg["n"])
    concurrency = int(cfg["concurrency"])
    latencies: list[float] = []
    sem = asyncio.Semaphore(concurrency)

    async def one(i: int) -> None:
        async with sem:
            t0 = time.perf_counter()
            org = engine.spawn_organism(
                intent="http",
                input_data={"n": i % 10},
                initial_capability="bench.compute",
            )
            await engine.execute_organism(org)
            latencies.append((time.perf_counter() - t0) * 1000)

    await asyncio.gather(*(one(i) for i in range(n)))
    return {
        "p95_ms": _percentile(latencies, 0.95),
        "p99_ms": _percentile(latencies, 0.99),
        "avg_ms": statistics.mean(latencies),
    }


SCENARIOS = {
    "dedup_identical_100": scenario_dedup_identical_100,
    "lineage_spawn_50": scenario_lineage_spawn_50,
    "time_split_3branch": scenario_time_split_3branch,
    "swarm_mapreduce_1k": scenario_swarm_mapreduce_1k,
    "persist_hibernate_wake": scenario_persist_hibernate_wake,
    "http_organism_execute_p95": scenario_http_organism_execute_p95,
}


async def run_all(scenario_cfg: dict[str, Any]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for name, fn in SCENARIOS.items():
        cfg = scenario_cfg.get(name, {})
        results[name] = await fn(cfg)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PrismPipe benches")
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=Path(__file__).with_name("scenarios.yaml"),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).with_name("last_results.json"),
    )
    parser.add_argument("--scenario", type=str, default=None)
    args = parser.parse_args()

    raw = yaml.safe_load(args.scenarios.read_text())
    scenario_cfg = raw.get("scenarios", raw)

    if args.scenario:
        if args.scenario not in SCENARIOS:
            print(f"Unknown scenario: {args.scenario}", file=sys.stderr)
            return 2
        results = {
            args.scenario: asyncio.run(SCENARIOS[args.scenario](scenario_cfg[args.scenario]))
        }
    else:
        results = asyncio.run(run_all(scenario_cfg))

    args.out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
