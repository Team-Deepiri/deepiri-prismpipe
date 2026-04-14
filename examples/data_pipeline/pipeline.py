"""
Example: Data pipeline using PrismPipe capability routing.

Demonstrates a three-stage ETL flow:
  1. extract  — fetch raw records from a source
  2. transform — normalise and enrich each record
  3. load — persist the batch to a destination

Run:
    python -m examples.data_pipeline.pipeline
"""

import asyncio
from prismpipe import PrismEngine, create_envelope
from prismpipe.core import Intent, Node, NodeResult


class ExtractNode(Node):
    """Simulate pulling raw records from an external source."""

    capability = "etl.extract"

    def process(self, envelope):
        envelope.state["raw_records"] = [
            {"id": 1, "name": " Alice ", "score": "95"},
            {"id": 2, "name": "Bob", "score": "87"},
            {"id": 3, "name": " Charlie", "score": "invalid"},
        ]
        envelope.set_next("etl.transform")
        return NodeResult(envelope=envelope)


class TransformNode(Node):
    """Clean and normalise extracted records."""

    capability = "etl.transform"

    def process(self, envelope):
        cleaned = []
        errors = []
        for rec in envelope.state.get("raw_records", []):
            try:
                cleaned.append({
                    "id": rec["id"],
                    "name": rec["name"].strip(),
                    "score": int(rec["score"]),
                })
            except (ValueError, KeyError):
                errors.append(rec)

        envelope.state["cleaned_records"] = cleaned
        envelope.state["transform_errors"] = errors
        envelope.set_next("etl.load")
        return NodeResult(envelope=envelope)


class LoadNode(Node):
    """Persist cleaned records (simulated)."""

    capability = "etl.load"

    def process(self, envelope):
        records = envelope.state.get("cleaned_records", [])
        envelope.state["load_result"] = {
            "inserted": len(records),
            "errors": len(envelope.state.get("transform_errors", [])),
        }
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


async def main():
    engine = PrismEngine()
    for node in [ExtractNode(), TransformNode(), LoadNode()]:
        engine.register_node(node)

    envelope = create_envelope(
        intent=Intent.HTTP_REQUEST,
        input_data={"pipeline": "etl"},
        next_capability="etl.extract",
    )

    result = await engine.execute(envelope)

    print("Pipeline complete")
    print(f"  Cleaned records: {result.state.get('cleaned_records')}")
    print(f"  Transform errors: {result.state.get('transform_errors')}")
    print(f"  Load result: {result.state.get('load_result')}")


if __name__ == "__main__":
    asyncio.run(main())
