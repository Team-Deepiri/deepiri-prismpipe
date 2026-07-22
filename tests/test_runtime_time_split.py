"""Focused tests for speculative time-split execution."""

import asyncio

import pytest

from prismpipe.core import CapabilityRouter, Intent, Node, NodeResult
from prismpipe.engine import ComputationGraph, Organism, TimeSplitter


class BranchNode(Node):
    def __init__(
        self,
        capability: str,
        delay: float,
        *,
        succeeds: bool = True,
        cancelled: set[str] | None = None,
        started: set[str] | None = None,
    ):
        super().__init__()
        self.capability = capability
        self.delay = delay
        self.succeeds = succeeds
        self.cancelled = cancelled
        self.started = started

    async def process(self, envelope):
        if self.started is not None:
            self.started.add(self.capability)
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            if self.cancelled is not None:
                self.cancelled.add(self.capability)
            raise
        if not self.succeeds:
            raise RuntimeError(f"{self.capability} failed")
        envelope.state["winner"] = self.capability
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


def register(router, *nodes):
    for node in nodes:
        router.register(node.capability, node)


@pytest.mark.asyncio
async def test_time_split_returns_first_success_and_cancels_remaining_branches():
    router = CapabilityRouter()
    cancelled: set[str] = set()
    register(
        router,
        BranchNode("branch.failure", 0.005, succeeds=False),
        BranchNode("branch.winner", 0.02),
        BranchNode("branch.slow", 1, cancelled=cancelled),
    )
    organism = Organism(Intent.CUSTOM, {"request": "split"})
    splitter = TimeSplitter(router, timeout_ms=500)

    result = await splitter.execute_time_split(
        organism,
        ["branch.failure", "branch.winner", "branch.slow"],
        ComputationGraph(),
    )

    assert result.terminated is False
    assert result.state["winner"] == "branch.winner"
    assert [entry.capability for entry in result.history] == ["branch.winner"]
    assert "branch.slow" in cancelled
    assert len(result.children) == 3


@pytest.mark.asyncio
async def test_time_split_reports_when_all_branches_fail():
    router = CapabilityRouter()
    register(
        router,
        BranchNode("branch.failure.one", 0, succeeds=False),
        BranchNode("branch.failure.two", 0, succeeds=False),
    )
    organism = Organism(Intent.CUSTOM, {"request": "fail"})

    result = await TimeSplitter(router, timeout_ms=500).execute_time_split(
        organism,
        ["branch.failure.one", "branch.failure.two"],
        ComputationGraph(),
    )

    assert result.terminated is True
    assert result.state["_error"] == "All time-split branches failed"


@pytest.mark.asyncio
async def test_time_split_enforces_overall_timeout():
    router = CapabilityRouter()
    cancelled: set[str] = set()
    register(
        router,
        BranchNode("branch.slow.one", 1, cancelled=cancelled),
        BranchNode("branch.slow.two", 1, cancelled=cancelled),
    )
    organism = Organism(Intent.CUSTOM, {"request": "timeout"})

    result = await TimeSplitter(router, timeout_ms=20).execute_time_split(
        organism,
        ["branch.slow.one", "branch.slow.two"],
        ComputationGraph(),
    )

    assert result.terminated is True
    assert result.state["_error"] == "Time split timed out after 0.02s"
    assert cancelled == {"branch.slow.one", "branch.slow.two"}


@pytest.mark.asyncio
async def test_time_split_preserves_caller_cancellation():
    router = CapabilityRouter()
    cancelled: set[str] = set()
    started: set[str] = set()
    expected = {"branch.cancel.one", "branch.cancel.two"}
    register(
        router,
        BranchNode(
            "branch.cancel.one",
            1,
            cancelled=cancelled,
            started=started,
        ),
        BranchNode(
            "branch.cancel.two",
            1,
            cancelled=cancelled,
            started=started,
        ),
    )
    organism = Organism(Intent.CUSTOM, {"request": "cancel"})
    task = asyncio.create_task(
        TimeSplitter(router, timeout_ms=1000).execute_time_split(
            organism,
            ["branch.cancel.one", "branch.cancel.two"],
            ComputationGraph(),
        )
    )
    async def wait_until_started():
        while started != expected:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_until_started(), timeout=0.1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled == expected
