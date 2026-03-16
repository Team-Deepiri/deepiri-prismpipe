"""Tests for PrismPipe core functionality."""

import pytest

from prismpipe import Pipeline, PipelineConfig, create_envelope
from prismpipe.core.envelope import HistoryEntry, Intent, Metadata, RequestEnvelope
from prismpipe.core.node import Node, NodeResult
from prismpipe.core.router import CapabilityRouter, NodeNotFoundError


class TestRequestEnvelope:
    """Tests for RequestEnvelope."""

    def test_create_envelope(self):
        envelope = create_envelope(
            intent=Intent.HTTP_REQUEST,
            input_data={"path": "/test"},
            next_capability="test.capability",
        )

        assert envelope.id.startswith("req_")
        assert envelope.intent == Intent.HTTP_REQUEST
        assert envelope.input.get("path") == "/test"
        assert envelope.next == "test.capability"
        assert envelope.terminated is False

    def test_record_history(self):
        envelope = create_envelope()
        
        envelope.record(
            node_id="test_node",
            node_type="TestNode",
            capability="test.capability",
            action="test action",
            duration_ms=10.5,
        )

        assert len(envelope.history) == 1
        assert envelope.history[0].node_id == "test_node"
        assert envelope.history[0].capability == "test.capability"

    def test_terminate(self):
        envelope = create_envelope()
        envelope.terminate("Test error")

        assert envelope.terminated is True
        assert envelope.error == "Test error"

    def test_get_capability(self):
        envelope = create_envelope(next_capability="capability:test")
        assert envelope.get_capability() == "test"

        envelope2 = create_envelope(next_capability="node_id_123")
        assert envelope2.get_capability() == "node_id_123"


class TestCapabilityRouter:
    """Tests for CapabilityRouter."""

    def test_register_and_resolve(self):
        router = CapabilityRouter()

        class TestNode(Node):
            capability = "test.capability"

            def process(self, envelope):
                return NodeResult(envelope=envelope)

        node = TestNode()
        router.register("test.capability", node)

        resolved = router.resolve("test.capability")
        assert resolved is node

    def test_unregister(self):
        router = CapabilityRouter()

        class TestNode(Node):
            capability = "test.capability"

            def process(self, envelope):
                return NodeResult(envelope=envelope)

        router.register("test.capability", TestNode())
        assert router.has_capability("test.capability")

        router.unregister("test.capability")
        assert not router.has_capability("test.capability")

    def test_alias(self):
        router = CapabilityRouter()

        class TestNode(Node):
            capability = "test.capability"

            def process(self, envelope):
                return NodeResult(envelope=envelope)

        router.register("test.capability", TestNode())
        router.alias("alias.capability", "test.capability")

        resolved = router.resolve("alias.capability")
        assert resolved is not None

    def test_not_found_error(self):
        router = CapabilityRouter()

        with pytest.raises(NodeNotFoundError):
            router.resolve("nonexistent.capability")


class TestNode:
    """Tests for Node base class."""

    def test_node_result(self):
        envelope = create_envelope()
        result = NodeResult(envelope=envelope, success=True)

        assert result.success is True
        assert result.error is None
        assert result.envelope is envelope


class TestPipeline:
    """Tests for Pipeline."""

    def test_execute_simple(self):
        class TestNode(Node):
            capability = "test.node"

            def process(self, envelope):
                envelope.state["processed"] = True
                envelope.set_next(None)
                return NodeResult(envelope=envelope)

        pipeline = Pipeline()
        pipeline.register_node(TestNode())

        envelope = create_envelope(next_capability="test.node")
        result = pipeline.execute(envelope)

        assert result.success is True
        assert result.envelope.state["processed"] is True

    def test_execute_chain(self):
        class NodeA(Node):
            capability = "node.a"

            def process(self, envelope):
                envelope.state["a"] = True
                envelope.set_next("node.b")
                return NodeResult(envelope=envelope)

        class NodeB(Node):
            capability = "node.b"

            def process(self, envelope):
                envelope.state["b"] = True
                envelope.set_next(None)
                return NodeResult(envelope=envelope)

        pipeline = Pipeline()
        pipeline.register_nodes([NodeA(), NodeB()])

        envelope = create_envelope(next_capability="node.a")
        result = pipeline.execute(envelope)

        assert result.success is True
        assert result.envelope.state["a"] is True
        assert result.envelope.state["b"] is True
        assert result.iterations == 2

    def test_execute_not_found(self):
        pipeline = Pipeline()
        envelope = create_envelope(next_capability="nonexistent.node")

        result = pipeline.execute(envelope)

        assert result.success is False
        assert "nonexistent.node" in result.error

    def test_max_iterations(self):
        class LoopNode(Node):
            capability = "loop.node"

            def process(self, envelope):
                envelope.set_next("loop.node")
                return NodeResult(envelope=envelope)

        pipeline = Pipeline(config=PipelineConfig(max_iterations=5))
        pipeline.register_node(LoopNode())

        envelope = create_envelope(next_capability="loop.node")
        result = pipeline.execute(envelope)

        assert result.iterations == 6
        assert "Max iterations" in result.error


class TestPrismPipeSDK:
    """Tests for the SDK."""

    def test_node_decorator(self):
        from prismpipe.sdk import node, PrismPipe

        @node(capability="test.decorated")
        def test_func(envelope):
            envelope.state["decorated"] = True
            return envelope

        assert test_func.capability == "test.decorated"
        assert isinstance(test_func, Node)

    def test_prism_pipe_fluent(self):
        from prismpipe.sdk import PrismPipe

        pp = PrismPipe()

        @pp.node("test.capability")
        def test_node(envelope):
            envelope.state["executed"] = True
            envelope.set_next(None)
            return envelope

        assert "test.capability" in pp.router

        result = pp.execute({"test": True}, start_capability="test.capability")
        assert result.success is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
