from prismpipe.deepiri_bus import (
    DeepiriStreamTopics,
    ENVELOPE_TO_STREAM,
    resolve_stream_for_envelope_kind,
)


def test_agi_pipeline_topics_present():
    assert DeepiriStreamTopics.PIPELINE_PRESSURE_EVENTS.value == "pipeline.pressure.events"
    assert (
        DeepiriStreamTopics.PIPELINE_ARTIFACT_INVALIDATION.value
        == "pipeline.artifact.invalidation"
    )
    assert DeepiriStreamTopics.HELOX_TRAINING_STRUCTURED.value == (
        "pipeline.helox-training.structured"
    )


def test_envelope_routing_bridge():
    assert resolve_stream_for_envelope_kind("pressure") == "pipeline.pressure.events"
    assert resolve_stream_for_envelope_kind("model_ready") == "model-events"
    assert resolve_stream_for_envelope_kind("unknown_xyz") == "platform-events"
    assert "train" in ENVELOPE_TO_STREAM
    assert len(DeepiriStreamTopics.sugar_glider_allowlist()) >= 15
