"""Focused tests for runtime limits, cancellation, and computation sharing."""

import asyncio
import threading
import time

import pytest

from prismpipe.core import (
    CapabilityRouter,
    HistoryEntry,
    Intent,
    Node,
    NodeResult,
    Pipeline,
    PipelineConfig,
    create_envelope,
)
from prismpipe.core.node import (
    NodeExecutionCapacityError,
    _BoundedDaemonExecutor,
)
from prismpipe.engine import (
    ComputationGraph,
    Organism,
    OrganismExecutor,
    OrganismState,
    PrismEngine,
)


class AsyncDelayNode(Node):
    capability = "runtime.delay"

    def __init__(self, delay: float = 0.1):
        super().__init__()
        self.delay = delay
        self.cancelled = asyncio.Event()

    async def process(self, envelope):
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        envelope.state["completed"] = True
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class SlowSyncNode(Node):
    capability = "runtime.slow_sync"

    def process(self, envelope):
        time.sleep(0.05)
        envelope.state["late_mutation"] = True
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class EnvelopeMutationNode(Node):
    capability = "runtime.commit"
    timeout_ms = 1000

    def __init__(self):
        super().__init__()
        self.working_envelope = None

    def process(self, envelope):
        self.working_envelope = envelope
        envelope.id = "committed-id"
        envelope.intent = Intent.BATCH
        envelope.input["nested"]["values"].append(2)
        envelope.state["nested"] = {"values": [3]}
        envelope.metadata.source = "committed"
        envelope.metadata.tags["phase"] = "complete"
        envelope.plan.add("runtime.after")
        envelope.plan.current_index = 1
        envelope.parent_id = "committed-parent"
        envelope.set_next("runtime.after")
        return NodeResult(envelope=envelope)


class LoopNode(Node):
    capability = "runtime.loop"

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def process(self, envelope):
        self.calls += 1
        envelope.state["calls"] = self.calls
        envelope.set_next(self.capability)
        return NodeResult(envelope=envelope)


class FirstCacheNode(Node):
    capability = "cache.first"

    def __init__(self):
        super().__init__()
        self.calls = 0

    def process(self, envelope):
        self.calls += 1
        envelope.input["values"].append(2)
        envelope.state["nested"] = {"values": [1, 2]}
        envelope.metadata.tags["cache"] = "restored"
        envelope.metadata.custom["nested"] = {"values": [3]}
        envelope.plan.add("cache.plan")
        envelope.parent_id = "cache-parent"
        envelope.set_next("cache.second")
        return NodeResult(envelope=envelope)


class SecondCacheNode(Node):
    capability = "cache.second"

    def __init__(self):
        super().__init__()
        self.calls = 0

    def process(self, envelope):
        self.calls += 1
        envelope.state["final"] = sum(envelope.input["values"])
        envelope.state["observed"] = {
            "metadata": envelope.metadata.tags["cache"],
            "plan": list(envelope.plan.capabilities),
            "parent": envelope.parent_id,
        }
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class FailOnceNode(Node):
    capability = "cache.fail_once"

    def __init__(self):
        super().__init__()
        self.calls = 0

    def process(self, envelope):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("transient failure")
        envelope.state["recovered"] = True
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class CountingNode(Node):
    capability = "cache.counting"

    def __init__(self):
        super().__init__()
        self.calls = 0

    def process(self, envelope):
        self.calls += 1
        envelope.state["calls"] = self.calls
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class ConcurrentCacheNode(Node):
    capability = "cache.concurrent"

    def __init__(self):
        super().__init__()
        self.calls = 0

    async def process(self, envelope):
        self.calls += 1
        await asyncio.sleep(0.01)
        envelope.state["payload"] = {"values": [envelope.input["value"]]}
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class UnserializableValue:
    pass


class TrackingComputationGraph(ComputationGraph):
    def __init__(self):
        super().__init__()
        self.hit_envelopes = []

    def find_shared_computation(self, *args, **kwargs):
        result = super().find_shared_computation(*args, **kwargs)
        if result is not None:
            self.hit_envelopes.append(kwargs["envelope"])
        return result


def make_cache_organism(identifier: str = "shared-cache-id"):
    organism = Organism(
        Intent.CUSTOM,
        {"values": [1]},
        initial_capability=FirstCacheNode.capability,
        parent_organism_id="original-parent",
    )
    organism.id = identifier
    return organism


def make_direct_envelopes(key: int):
    input_envelope = create_envelope(
        intent=Intent.CUSTOM,
        input_data={"key": key},
        next_capability="cache.direct",
    )
    input_envelope.id = f"direct-{key}"
    output_envelope = input_envelope.model_copy(deep=True)
    output_envelope.state["value"] = key
    output_envelope.set_next(None)
    return input_envelope, output_envelope


def test_successful_isolated_execution_commits_all_fields_in_place():
    node = EnvelopeMutationNode()
    envelope = create_envelope(
        intent=Intent.CUSTOM,
        input_data={"nested": {"values": [1]}},
        state={"original": True},
        next_capability=node.capability,
    )
    original_identity = id(envelope)

    result = node.execute(envelope)

    assert result.envelope is envelope
    assert id(result.envelope) == original_identity
    assert envelope.id == "committed-id"
    assert envelope.intent is Intent.BATCH
    assert envelope.input == {"nested": {"values": [1, 2]}}
    assert envelope.state == {"original": True, "nested": {"values": [3]}}
    assert envelope.metadata.source == "committed"
    assert envelope.metadata.tags == {"phase": "complete"}
    assert envelope.plan.capabilities == ["runtime.after"]
    assert envelope.plan.current_index == 1
    assert envelope.next == "runtime.after"
    assert envelope.parent_id == "committed-parent"
    assert [entry.capability for entry in envelope.history] == [node.capability]

    node.working_envelope.input["nested"]["values"].append(99)
    node.working_envelope.state["nested"]["values"].append(99)
    node.working_envelope.metadata.tags["phase"] = "mutated"
    node.working_envelope.plan.add("mutated")

    assert envelope.input == {"nested": {"values": [1, 2]}}
    assert envelope.state["nested"] == {"values": [3]}
    assert envelope.metadata.tags == {"phase": "complete"}
    assert envelope.plan.capabilities == ["runtime.after"]


@pytest.mark.asyncio
async def test_async_success_returns_and_mutates_original_envelope():
    node = AsyncDelayNode(delay=0)
    envelope = create_envelope(next_capability=node.capability)

    result = await node.execute_async(envelope)

    assert result.envelope is envelope
    assert envelope.state["completed"] is True
    assert envelope.next is None


@pytest.mark.asyncio
async def test_node_timeout_is_enforced_for_async_callable():
    node = AsyncDelayNode()
    node.timeout_ms = 10
    envelope = create_envelope(next_capability=node.capability)

    result = await node.execute_async(envelope)

    assert result.envelope is envelope
    assert result.success is False
    assert result.envelope.terminated is True
    assert "timed out" in result.error
    assert node.cancelled.is_set()


def test_sync_timeout_never_commits_late_work():
    node = SlowSyncNode()
    node.timeout_ms = 10
    envelope = create_envelope(next_capability=node.capability)

    result = node.execute(envelope)
    time.sleep(0.06)

    assert result.envelope is envelope
    assert result.success is False
    assert "late_mutation" not in envelope.state


def test_bounded_executor_limits_work_queue_and_shutdown_wait():
    executor = _BoundedDaemonExecutor(max_workers=2, max_pending=2)
    gate = threading.Event()
    both_started = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum = 0
    starts = 0

    def blocked_work():
        nonlocal active, maximum, starts
        with lock:
            active += 1
            starts += 1
            maximum = max(maximum, active)
            if starts == 2:
                both_started.set()
        gate.wait()
        with lock:
            active -= 1
        return "done"

    running = [executor.submit(blocked_work) for _ in range(2)]
    assert both_started.wait(0.5)
    queued = [executor.submit(lambda: "queued") for _ in range(2)]

    with pytest.raises(NodeExecutionCapacityError):
        executor.submit(lambda: "overflow")

    started_at = time.monotonic()
    executor.shutdown(wait=True, timeout=0.01)
    shutdown_duration = time.monotonic() - started_at

    assert maximum == 2
    assert shutdown_duration < 0.1
    assert all(thread.daemon for thread in executor._threads)
    assert all(future.cancelled() for future in queued)

    gate.set()
    assert [future.result(timeout=0.5) for future in running] == ["done", "done"]


def test_pipeline_timeout_is_enforced_without_late_mutation():
    pipeline = Pipeline(config=PipelineConfig(timeout_seconds=0.01))
    pipeline.register_node(SlowSyncNode())
    envelope = create_envelope(next_capability=SlowSyncNode.capability)

    result = pipeline.execute(envelope)
    time.sleep(0.06)

    assert result.success is False
    assert result.error == "Pipeline timed out after 0.01s"
    assert "late_mutation" not in result.envelope.state


def test_slow_before_node_hook_causes_pipeline_timeout():
    node = CountingNode()
    pipeline = Pipeline(config=PipelineConfig(timeout_seconds=0.01))
    pipeline.register_node(node)
    pipeline.hook("before_node", lambda *_: time.sleep(0.02))

    result = pipeline.execute(create_envelope(next_capability=node.capability))

    assert result.success is False
    assert result.error == "Pipeline timed out after 0.01s"
    assert node.calls == 0


def test_slow_after_node_hook_prevents_success_after_deadline():
    node = CountingNode()
    pipeline = Pipeline(config=PipelineConfig(timeout_seconds=0.01))
    pipeline.register_node(node)
    pipeline.hook("after_node", lambda *_: time.sleep(0.02))

    result = pipeline.execute(create_envelope(next_capability=node.capability))

    assert result.success is False
    assert result.error == "Pipeline timed out after 0.01s"
    assert node.calls == 1


def test_normal_pipeline_hooks_still_run():
    node = CountingNode()
    pipeline = Pipeline(config=PipelineConfig(timeout_seconds=0.5))
    pipeline.register_node(node)
    events = []
    pipeline.hook("before_node", lambda *_: events.append("before"))
    pipeline.hook("after_node", lambda *_: events.append("after"))

    result = pipeline.execute(create_envelope(next_capability=node.capability))

    assert result.success is True
    assert events == ["before", "after"]


@pytest.mark.asyncio
async def test_engine_iteration_limit_stops_looping_node():
    engine = PrismEngine(PipelineConfig(max_iterations=2))
    node = LoopNode()
    engine.register_node(node)
    envelope = create_envelope(next_capability=node.capability)

    result = await engine.execute(envelope)

    assert result is envelope
    assert result.terminated is True
    assert result.error == "Max iterations (2) exceeded"
    assert node.calls == 2


@pytest.mark.asyncio
async def test_organism_timeout_is_not_cached():
    router = CapabilityRouter()
    node = AsyncDelayNode()
    router.register(node.capability, node)
    graph = ComputationGraph()
    organism = Organism(
        Intent.CUSTOM,
        {"request": "timeout"},
        initial_capability=node.capability,
    )
    executor = OrganismExecutor(
        router,
        PipelineConfig(timeout_seconds=0.01),
    )

    result = await executor.execute(organism, graph)

    assert result.terminated is True
    assert "timed out" in result.state["_error"]
    assert graph.get_deduplication_stats()["unique_computations"] == 0


@pytest.mark.asyncio
async def test_organism_preserves_caller_cancellation_and_does_not_cache():
    router = CapabilityRouter()
    node = AsyncDelayNode(delay=1)
    router.register(node.capability, node)
    graph = ComputationGraph()
    organism = Organism(
        Intent.CUSTOM,
        {"request": "cancel"},
        initial_capability=node.capability,
    )
    task = asyncio.create_task(OrganismExecutor(router).execute(organism, graph))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert organism._state is OrganismState.SUSPENDED
    assert node.cancelled.is_set()
    assert graph.get_deduplication_stats()["unique_computations"] == 0


@pytest.mark.asyncio
async def test_shared_computation_restores_complete_isolated_envelope():
    router = CapabilityRouter()
    first_node = FirstCacheNode()
    second_node = SecondCacheNode()
    router.register(first_node.capability, first_node)
    router.register(second_node.capability, second_node)
    graph = TrackingComputationGraph()
    executor = OrganismExecutor(router)
    first = make_cache_organism()
    second = make_cache_organism()
    third = make_cache_organism()

    await executor.execute(first, graph)
    await executor.execute(second, graph)

    assert len(graph.hit_envelopes) == 2
    assert len({id(envelope) for envelope in graph.hit_envelopes}) == 1
    hit_envelope = graph.hit_envelopes[0]
    assert hit_envelope.input == {"values": [1, 2]}
    assert hit_envelope.metadata.tags == {"cache": "restored"}
    assert hit_envelope.plan.capabilities == ["cache.plan"]
    assert hit_envelope.next is None
    graph.hit_envelopes.clear()

    second.input["values"].append(99)
    second.state["nested"]["values"].append(99)
    second.metadata.custom["nested"]["values"].append(99)
    second._plan.add("mutated")
    await executor.execute(third, graph)

    expected_state = {
        "nested": {"values": [1, 2]},
        "final": 3,
        "observed": {
            "metadata": "restored",
            "plan": ["cache.plan"],
            "parent": "cache-parent",
        },
    }
    assert first.state == expected_state
    assert third.state == expected_state
    assert third.input == {"values": [1, 2]}
    assert third.metadata.tags == {"cache": "restored"}
    assert third.metadata.custom == {"nested": {"values": [3]}}
    assert third._plan.capabilities == ["cache.plan"]
    assert third._parent_organism_id == "cache-parent"
    assert third._next_capability is None
    assert [entry.capability for entry in third.history] == [
        "cache.first",
        "cache.second",
    ]
    assert first_node.calls == 1
    assert second_node.calls == 1
    assert graph.get_deduplication_stats()["total_reuses"] == 4


@pytest.mark.asyncio
async def test_cache_key_covers_every_node_readable_envelope_context():
    router = CapabilityRouter()
    node = CountingNode()
    router.register(node.capability, node)
    graph = ComputationGraph()
    executor = OrganismExecutor(router)

    baseline = Organism(
        Intent.CUSTOM,
        {"value": 1},
        initial_capability=node.capability,
    )
    baseline.id = "context-id"

    variants = []
    metadata_variant = Organism(
        Intent.CUSTOM,
        {"value": 1},
        initial_capability=node.capability,
    )
    metadata_variant.id = "context-id"
    metadata_variant.metadata.tags["variant"] = "metadata"
    variants.append(metadata_variant)

    intent_variant = Organism(
        Intent.BATCH,
        {"value": 1},
        initial_capability=node.capability,
    )
    intent_variant.id = "context-id"
    variants.append(intent_variant)

    input_variant = Organism(
        Intent.CUSTOM,
        {"value": 2},
        initial_capability=node.capability,
    )
    input_variant.id = "context-id"
    variants.append(input_variant)

    state_variant = Organism(
        Intent.CUSTOM,
        {"value": 1},
        initial_capability=node.capability,
    )
    state_variant.id = "context-id"
    state_variant.state["variant"] = "state"
    variants.append(state_variant)

    history_variant = Organism(
        Intent.CUSTOM,
        {"value": 1},
        initial_capability=node.capability,
    )
    history_variant.id = "context-id"
    history_variant.history.append(
        HistoryEntry(
            node_id="prior",
            node_type="test",
            capability="prior",
            action="complete",
        )
    )
    variants.append(history_variant)

    plan_variant = Organism(
        Intent.CUSTOM,
        {"value": 1},
        initial_capability=node.capability,
    )
    plan_variant.id = "context-id"
    plan_variant._plan.add("planned")
    variants.append(plan_variant)

    parent_variant = Organism(
        Intent.CUSTOM,
        {"value": 1},
        initial_capability=node.capability,
        parent_organism_id="different-parent",
    )
    parent_variant.id = "context-id"
    variants.append(parent_variant)

    identity_variant = Organism(
        Intent.CUSTOM,
        {"value": 1},
        initial_capability=node.capability,
    )
    identity_variant.id = "different-id"
    variants.append(identity_variant)

    await executor.execute(baseline, graph)
    for variant in variants:
        await executor.execute(variant, graph)

    assert node.calls == 1 + len(variants)
    assert graph.get_deduplication_stats()["unique_computations"] == 1 + len(variants)


@pytest.mark.asyncio
async def test_failed_computation_is_retried_and_only_success_is_cached():
    router = CapabilityRouter()
    node = FailOnceNode()
    router.register(node.capability, node)
    graph = ComputationGraph()
    executor = OrganismExecutor(router)
    first = Organism(
        Intent.CUSTOM,
        {"request": "retry"},
        initial_capability=node.capability,
    )
    second = Organism(
        Intent.CUSTOM,
        {"request": "retry"},
        initial_capability=node.capability,
    )
    first.id = second.id = "retry-id"

    await executor.execute(first, graph)
    assert graph.get_deduplication_stats()["unique_computations"] == 0
    await executor.execute(second, graph)

    assert first.terminated is True
    assert second.terminated is False
    assert second.state["recovered"] is True
    assert node.calls == 2
    assert graph.get_deduplication_stats()["unique_computations"] == 1


@pytest.mark.asyncio
async def test_unserializable_computation_context_is_never_cached():
    router = CapabilityRouter()
    node = CountingNode()
    router.register(node.capability, node)
    graph = ComputationGraph()
    executor = OrganismExecutor(router)
    first = Organism(
        Intent.CUSTOM,
        {"value": UnserializableValue()},
        initial_capability=node.capability,
    )
    second = Organism(
        Intent.CUSTOM,
        {"value": UnserializableValue()},
        initial_capability=node.capability,
    )
    first.id = second.id = "unserializable-id"

    await executor.execute(first, graph)
    await executor.execute(second, graph)

    assert node.calls == 2
    assert graph.get_deduplication_stats()["unique_computations"] == 0


@pytest.mark.asyncio
async def test_disabling_sharing_executes_each_organism():
    engine = PrismEngine()
    node = CountingNode()
    engine.register_node(node)
    first = engine.spawn_organism(
        Intent.CUSTOM,
        {"request": "same"},
        initial_capability=node.capability,
    )
    second = engine.spawn_organism(
        Intent.CUSTOM,
        {"request": "same"},
        initial_capability=node.capability,
    )

    await engine.execute_organism(first, use_computation_sharing=False)
    await engine.execute_organism(second, use_computation_sharing=False)

    assert node.calls == 2


@pytest.mark.asyncio
async def test_concurrent_duplicate_computations_do_not_corrupt_cache():
    router = CapabilityRouter()
    node = ConcurrentCacheNode()
    router.register(node.capability, node)
    graph = ComputationGraph()
    executor = OrganismExecutor(router)
    organisms = [
        Organism(
            Intent.CUSTOM,
            {"value": 7},
            initial_capability=node.capability,
        )
        for _ in range(2)
    ]
    for organism in organisms:
        organism.id = "concurrent-id"

    await asyncio.gather(*(executor.execute(organism, graph) for organism in organisms))
    organisms[0].state["payload"]["values"].append(9)

    input_envelope = create_envelope(
        intent=Intent.CUSTOM,
        input_data={"value": 7},
        next_capability=node.capability,
    )
    input_envelope.id = "concurrent-id"
    shared = graph.find_shared_computation(
        node.capability,
        {"value": 7},
        version=node.version,
        envelope=input_envelope,
    )

    assert shared is not None
    assert shared.output_envelope is not None
    assert shared.output_envelope.state == {"payload": {"values": [7]}}
    assert organisms[1].state == {"payload": {"values": [7]}}
    assert graph.get_deduplication_stats()["unique_computations"] == 1


def test_computation_cache_enforces_lru_bound_and_ttl():
    graph = ComputationGraph(max_entries=1, ttl_seconds=0.02)
    first_input, first_output = make_direct_envelopes(1)
    second_input, second_output = make_direct_envelopes(2)
    graph.register_computation(
        "cache.direct",
        first_input.input,
        first_output.state,
        latency_ms=1,
        success=True,
        input_envelope=first_input,
        output_envelope=first_output,
    )
    graph.register_computation(
        "cache.direct",
        second_input.input,
        second_output.state,
        latency_ms=1,
        success=True,
        input_envelope=second_input,
        output_envelope=second_output,
    )

    assert (
        graph.find_shared_computation(
            "cache.direct",
            first_input.input,
            envelope=first_input,
        )
        is None
    )
    assert (
        graph.find_shared_computation(
            "cache.direct",
            second_input.input,
            envelope=second_input,
        )
        is not None
    )

    time.sleep(0.03)

    assert (
        graph.find_shared_computation(
            "cache.direct",
            second_input.input,
            envelope=second_input,
        )
        is None
    )
    assert graph.get_deduplication_stats()["unique_computations"] == 0
