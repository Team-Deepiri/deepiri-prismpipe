"""Tests for API envelope -> organism -> child capability runtime wiring."""

import pytest

from prismpipe.core import Intent, Node, NodeResult, create_envelope
from prismpipe.engine import PrismEngine


class ParentWorkflowNode(Node):
    capability = "workflow.parent"

    def process(self, envelope):
        envelope.state["parent_executed"] = True
        envelope.state["workflow_state"] = {
            "path": envelope.input["path"],
            "body": envelope.input["body"],
        }
        envelope.state["shared_value"] = "from-parent"
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class ChildWorkflowNode(Node):
    capability = "workflow.child"

    def process(self, envelope):
        envelope.state["child_executed"] = {
            "parent_state": envelope.state["shared_value"],
            "child_payload": envelope.input["child_payload"],
        }
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class FailingChildNode(Node):
    capability = "workflow.fail"

    def process(self, envelope):
        raise RuntimeError("child capability failed")


class FailOnceChildNode(Node):
    capability = "workflow.fail_once"

    def __init__(self):
        super().__init__()
        self.calls = 0

    def process(self, envelope):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("first child attempt failed")
        envelope.state["retry_succeeded"] = True
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


def test_spawn_child_accepts_initial_capability():
    engine = PrismEngine()
    parent = engine.spawn_organism(
        intent=Intent.HTTP_REQUEST,
        input_data={"path": "/workflow"},
        initial_capability="workflow.parent",
    )

    child = parent.spawn_child(initial_capability="workflow.child")

    assert child.get_capability() == "workflow.child"
    assert child.pipeline == ["workflow.child"]
    assert child.envelope.parent_id == parent.id
    assert child in parent.children


@pytest.mark.asyncio
async def test_api_envelope_executes_parent_state_and_child_capability():
    engine = PrismEngine()
    engine.register_node(ParentWorkflowNode())
    engine.register_node(ChildWorkflowNode())

    envelope = create_envelope(
        intent=Intent.HTTP_REQUEST,
        input_data={
            "method": "POST",
            "path": "/workflows/route",
            "body": {"documentId": "doc_123"},
        },
        state={"api_request_state": "received"},
        next_capability="workflow.parent",
        source="api",
    )

    parent = engine.spawn_organism_from_envelope(envelope)

    assert parent.id == envelope.id
    assert parent.intent == Intent.HTTP_REQUEST
    assert parent.input["path"] == "/workflows/route"
    assert parent.state["api_request_state"] == "received"
    assert engine.organism_registry.get(parent.id) is parent

    parent = await engine.execute_organism(parent, use_computation_sharing=False)

    assert parent.state["parent_executed"] is True
    assert parent.state["workflow_state"] == {
        "path": "/workflows/route",
        "body": {"documentId": "doc_123"},
    }
    assert [entry.capability for entry in parent.history] == ["workflow.parent"]

    child = await engine.execute_child_organism(
        parent,
        capability="workflow.child",
        patch_input={"child_payload": {"capability": "vectorize"}},
        intent=Intent.CUSTOM,
        use_computation_sharing=False,
    )

    assert child.intent == Intent.CUSTOM
    assert child.envelope.parent_id == parent.id
    assert child.input["path"] == "/workflows/route"
    assert child.input["child_payload"] == {"capability": "vectorize"}
    assert child.state["api_request_state"] == "received"
    assert child.state["parent_executed"] is True
    assert child.state["child_executed"] == {
        "parent_state": "from-parent",
        "child_payload": {"capability": "vectorize"},
    }
    assert [entry.capability for entry in child.history] == ["workflow.child"]

    descendant_ids = {
        organism.id
        for organism in engine.organism_registry.get_descendants(parent.id)
    }
    lineage_ids = {
        organism.id
        for organism in engine.organism_registry.get_lineage(child.id)
    }

    assert child.id in descendant_ids
    assert {parent.id, child.id}.issubset(lineage_ids)


@pytest.mark.asyncio
async def test_child_capability_failure_terminates_cleanly():
    engine = PrismEngine()
    engine.register_node(FailingChildNode())
    parent = engine.spawn_organism(
        intent=Intent.HTTP_REQUEST,
        input_data={"path": "/workflows/fail"},
    )
    parent.state["parent_ready"] = True

    child = await engine.execute_child_organism(
        parent,
        capability="workflow.fail",
        use_computation_sharing=False,
    )

    assert child.terminated is True
    assert "child capability failed" in child.state["_error"]
    assert child.state["parent_ready"] is True
    assert child.history[-1].capability == "workflow.fail"
    assert child.history[-1].action == "error: RuntimeError"

    descendant_ids = {
        organism.id
        for organism in engine.organism_registry.get_descendants(parent.id)
    }
    assert child.id in descendant_ids


@pytest.mark.asyncio
async def test_child_execution_default_does_not_reuse_failed_computation():
    engine = PrismEngine()
    node = FailOnceChildNode()
    engine.register_node(node)
    parent = engine.spawn_organism(
        intent=Intent.HTTP_REQUEST,
        input_data={"path": "/workflows/retry"},
    )

    first = await engine.execute_child_organism(parent, capability="workflow.fail_once")
    second = await engine.execute_child_organism(parent, capability="workflow.fail_once")

    assert first.terminated is True
    assert "first child attempt failed" in first.state["_error"]
    assert second.terminated is False
    assert second.state["retry_succeeded"] is True
    assert "_from_shared" not in second.state
    assert node.calls == 2
