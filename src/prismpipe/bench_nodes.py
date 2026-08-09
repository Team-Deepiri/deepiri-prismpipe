"""Shared deterministic nodes for benches and the demo server."""

from __future__ import annotations

import time

from prismpipe.core.node import Node, NodeResult


class BenchComputeNode(Node):
    """Deterministic node for benchmarks / organism API demos."""

    capability = "bench.compute"
    offload = False

    def process(self, envelope):
        n = int(envelope.input.get("n", 1))
        envelope.state["result"] = n * 2
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class PartitionSumNode(Node):
    """Sum a partition payload — used by swarm benches."""

    capability = "bench.partition_sum"
    offload = False

    def process(self, envelope):
        data = envelope.input.get("partition_data", [])
        envelope.state["partition_result"] = sum(data) if isinstance(data, list) else 0
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


# Alias kept for server.py historical name.
BenchPartitionNode = PartitionSumNode


class FastBranch(Node):
    capability = "bench.fast"
    offload = False

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
