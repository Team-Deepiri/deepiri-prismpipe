"""Focused tests for bounded swarm execution and reduction."""

import asyncio

import pytest

from prismpipe.core import CapabilityRouter, Intent, Node, NodeResult
from prismpipe.engine import (
    ComputationGraph,
    Organism,
    SwarmCoordinator,
)


class SwarmWorkerNode(Node):
    capability = "swarm.worker"

    def __init__(
        self,
        tracker: dict[str, int],
        *,
        fail_values: set[int] | None = None,
        cancelled: set[tuple[int, ...]] | None = None,
    ):
        super().__init__()
        self.tracker = tracker
        self.fail_values = fail_values or set()
        self.cancelled = cancelled

    async def process(self, envelope):
        partition = tuple(envelope.input["partition_data"])
        self.tracker["active"] += 1
        self.tracker["maximum"] = max(
            self.tracker["maximum"],
            self.tracker["active"],
        )
        try:
            await asyncio.sleep(0.02)
            if any(value in self.fail_values for value in partition):
                raise RuntimeError(f"partition {partition} failed")
            envelope.state["partition"] = list(partition)
            envelope.set_next(None)
            return NodeResult(envelope=envelope)
        except asyncio.CancelledError:
            if self.cancelled is not None:
                self.cancelled.add(partition)
            raise
        finally:
            self.tracker["active"] -= 1


def make_coordinator(node, *, count=4, max_concurrency=2):
    router = CapabilityRouter()
    router.register(node.capability, node)
    coordinator = SwarmCoordinator(
        router,
        max_concurrency=max_concurrency,
        worker_timeout_seconds=1,
    )
    coordinator.create_swarm(
        "test-swarm",
        Organism(Intent.CUSTOM, {"request": "swarm"}),
        count,
    )
    return coordinator


@pytest.mark.asyncio
async def test_swarm_executes_workers_with_bounded_concurrency_and_async_reducer():
    tracker = {"active": 0, "maximum": 0}
    coordinator = make_coordinator(
        SwarmWorkerNode(tracker),
        count=6,
        max_concurrency=2,
    )

    async def reducer(workers):
        await asyncio.sleep(0)
        return [worker.state["partition"] for worker in workers]

    coordinator.register_reducer("test-swarm", reducer)

    result = await coordinator.execute_swarm(
        "test-swarm",
        SwarmWorkerNode.capability,
        ComputationGraph(),
        list(range(6)),
    )

    assert result == [[0], [1], [2], [3], [4], [5]]
    assert tracker["maximum"] == 2
    assert tracker["active"] == 0


@pytest.mark.asyncio
async def test_swarm_reduces_successful_workers_when_one_worker_fails():
    tracker = {"active": 0, "maximum": 0}
    coordinator = make_coordinator(
        SwarmWorkerNode(tracker, fail_values={1}),
        count=3,
    )
    coordinator.register_reducer(
        "test-swarm",
        lambda workers: [worker.state["partition"][0] for worker in workers],
    )

    result = await coordinator.execute_swarm(
        "test-swarm",
        SwarmWorkerNode.capability,
        ComputationGraph(),
        [0, 1, 2],
    )

    assert result == [0, 2]
    workers = coordinator.get_swarm_results("test-swarm")
    assert workers[1].terminated is True
    assert "partition (1,) failed" in workers[1].state["_error"]


@pytest.mark.asyncio
async def test_swarm_reports_reducer_failure():
    tracker = {"active": 0, "maximum": 0}
    coordinator = make_coordinator(SwarmWorkerNode(tracker), count=1)

    def fail_reducer(workers):
        raise ValueError("cannot reduce")

    coordinator.register_reducer("test-swarm", fail_reducer)

    with pytest.raises(RuntimeError, match="Swarm reducer failed: cannot reduce"):
        await coordinator.execute_swarm(
            "test-swarm",
            SwarmWorkerNode.capability,
            ComputationGraph(),
            [1],
        )


@pytest.mark.asyncio
async def test_swarm_rejects_empty_worker_set():
    tracker = {"active": 0, "maximum": 0}
    coordinator = make_coordinator(SwarmWorkerNode(tracker), count=0)

    with pytest.raises(ValueError, match="has no workers"):
        await coordinator.execute_swarm(
            "test-swarm",
            SwarmWorkerNode.capability,
            ComputationGraph(),
            [],
        )


@pytest.mark.asyncio
async def test_swarm_preserves_caller_cancellation_and_stops_active_workers():
    tracker = {"active": 0, "maximum": 0}
    cancelled: set[tuple[int, ...]] = set()
    coordinator = make_coordinator(
        SwarmWorkerNode(tracker, cancelled=cancelled),
        count=4,
        max_concurrency=2,
    )
    task = asyncio.create_task(
        coordinator.execute_swarm(
            "test-swarm",
            SwarmWorkerNode.capability,
            ComputationGraph(),
            [0, 1, 2, 3],
        )
    )
    async def wait_until_started():
        while tracker["maximum"] < 2:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_started(), timeout=0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert tracker["active"] == 0
    assert cancelled == {(0,), (1,)}
