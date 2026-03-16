"""Example: AI Processing Pipeline with retrieval, analysis, and generation."""

from typing import Any
from prismpipe import Pipeline, create_envelope
from prismpipe.core.envelope import Intent
from prismpipe.core.node import Node, NodeResult


class RetrievalNode(Node):
    """Retrieves relevant context/documents for the AI task."""

    capability = "ai.retrieval"

    def process(self, envelope) -> NodeResult:
        query = envelope.input.get("query", "")
        envelope.state["retrieved_docs"] = [
            {"id": "doc1", "content": "Deepiri is an AI platform...", "score": 0.95},
            {"id": "doc2", "content": "Machine learning models...", "score": 0.87},
        ]
        envelope.set_next("ai.analyze")
        return NodeResult(envelope=envelope)


class AnalysisNode(Node):
    """Analyzes retrieved context."""

    capability = "ai.analyze"

    def process(self, envelope) -> NodeResult:
        docs = envelope.state.get("retrieved_docs", [])
        summary = f"Found {len(docs)} relevant documents"
        keywords = ["AI", "platform", "machine learning"]

        envelope.state["analysis"] = {
            "summary": summary,
            "keywords": keywords,
            "doc_count": len(docs),
        }
        envelope.set_next("ai.generate")
        return NodeResult(envelope=envelope)


class GenerationNode(Node):
    """Generates the final AI response."""

    capability = "ai.generate"

    def process(self, envelope) -> NodeResult:
        query = envelope.input.get("query", "What's Deepiri?")
        analysis = envelope.state.get("analysis", {})

        response = f"Based on the analysis: {analysis.get('summary', '')}. "
        response += "Deepiri is a comprehensive AI platform that provides "
        response += "machine learning capabilities and services."

        envelope.state["generated_response"] = response
        envelope.state["tokens_used"] = len(response.split())
        envelope.set_next("ai.format")
        return NodeResult(envelope=envelope)


class FormatNode(Node):
    """Formats the AI response."""

    capability = "ai.format"

    def process(self, envelope) -> NodeResult:
        response = envelope.state.get("generated_response", "")
        tokens = envelope.state.get("tokens_used", 0)

        envelope.state["response_body"] = {
            "answer": response,
            "metadata": {
                "model": "deepiri-llm-v1",
                "tokens": tokens,
                "retrieved_docs": envelope.state.get("analysis", {}).get("doc_count", 0),
            },
        }
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


def create_ai_pipeline() -> Pipeline:
    """Create the AI processing pipeline."""
    pipeline = Pipeline()
    pipeline.register_nodes([
        RetrievalNode(),
        AnalysisNode(),
        GenerationNode(),
        FormatNode(),
    ])
    return pipeline


if __name__ == "__main__":
    pipeline = create_ai_pipeline()

    envelope = create_envelope(
        intent=Intent.AI_TASK,
        input={"query": "What is Deepiri?"},
        next="ai.retrieval",
    )

    result = pipeline.execute(envelope)

    print(f"Success: {result.success}")
    print(f"Response: {result.envelope.state.get('response_body')}")
    print(f"History: {[h.capability for h in result.envelope.history]}")
    print(f"Total time: {result.metrics.total_duration_ms:.2f}ms" if result.metrics else "")
