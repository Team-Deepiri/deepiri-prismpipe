"""Tests for Mutation/Watcher wiring into OrganismExecutor."""

import pytest

from prismpipe.core.node import Node, NodeResult
from prismpipe.engine import PrismEngine
from prismpipe.events import EventBus, EventType


class StepNode(Node):
    capability = "obs.step"

    def process(self, envelope):
        envelope.state["value"] = envelope.input.get("seed", 0) + 1
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


@pytest.mark.asyncio
async def test_mutation_and_watcher_fire_during_execute():
    engine = PrismEngine()
    engine.register_node(StepNode())

    events: list[str] = []
    org = engine.spawn_organism(
        intent="obs",
        input_data={"seed": 10},
        initial_capability="obs.step",
    )
    engine.organism_watcher.watch(org.id, lambda o, e: events.append(e))

    await engine.execute_organism(org)

    mutation = engine.organism_executor.get_mutation(org.id)
    assert mutation is not None
    assert len(mutation.get_timeline()) >= 1
    assert "value" in mutation.get_changed_keys()
    assert "node_executed" in events
    assert "completed" in events
    assert org.state["value"] == 11


@pytest.mark.asyncio
async def test_event_bus_receives_lifecycle_events():
    bus = EventBus()
    seen: list[EventType] = []

    async def capture(event):
        seen.append(event.type)

    bus.subscribe_all(capture)
    engine = PrismEngine(event_bus=bus)
    engine.register_node(StepNode())

    org = engine.spawn_organism(
        intent="obs",
        input_data={"seed": 1},
        initial_capability="obs.step",
    )
    await engine.execute_organism(org)

    assert EventType.REQUEST_STARTED in seen
    assert EventType.NODE_EXECUTED in seen
    assert EventType.REQUEST_COMPLETED in seen
