"""Stage memoization against a live Cyrex AGI plane.

Skipped unless CYREX_TEST_DSN points at a database with the cyrex schema applied:

    CYREX_TEST_DSN=postgresql://deepiri_cyrex:PASS@127.0.0.1:5434/cyrex_db \
      pytest tests/integration/test_cyrex_stage_store.py -v
"""

from __future__ import annotations

import os
import uuid

import pytest

from prismpipe.cyrex_stage_store import CyrexStageStore, stage_input_hash

DSN = os.getenv("CYREX_TEST_DSN")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not DSN, reason="CYREX_TEST_DSN not set"),
]


async def _seed_document(conn, content_hash: str) -> str:
    return str(
        await conn.fetchval(
            "INSERT INTO cyrex.documents (content_hash, mime_type, status) "
            "VALUES ($1, 'application/pdf', 'uploaded') RETURNING document_id",
            content_hash,
        )
    )


async def _seed_run(conn, document_id: str) -> str:
    return str(
        await conn.fetchval(
            "INSERT INTO cyrex.pipeline_runs (document_id, status) "
            "VALUES ($1, 'running') RETURNING run_id",
            document_id,
        )
    )


async def _seed_completed_stage(conn, run_id: str, stage_name: str) -> None:
    await conn.execute(
        "INSERT INTO cyrex.pipeline_run_stages "
        "(run_id, stage_name, status, duration_ms, producer, completed_at) "
        "VALUES ($1, $2, 'completed', 1200, 'extract_stage', NOW())",
        run_id,
        stage_name,
    )


async def _seed_artifact(conn, document_id: str, artifact_type: str) -> str:
    return str(
        await conn.fetchval(
            "INSERT INTO cyrex.artifacts "
            "(document_id, version, artifact_type, confidence, payload_json) "
            "VALUES ($1, 1, $2, 0.91, '{\"v\": 1}'::jsonb) RETURNING artifact_id",
            document_id,
            artifact_type,
        )
    )


@pytest.fixture
async def store():
    s = CyrexStageStore(DSN)
    yield s
    await s.close()


async def test_miss_then_hit_across_separate_runs(store):
    """A second run with the same stage input reuses the first run's artifacts."""
    pool = await store._get_pool()
    tag = uuid.uuid4().hex
    stage = f"extract.{tag}"
    input_hash = stage_input_hash({"section": "3.1", "text": f"payload-{tag}"})

    async with pool.acquire() as conn:
        doc = await _seed_document(conn, f"sha-{tag}")
        run1 = await _seed_run(conn, doc)
        await _seed_completed_stage(conn, run1, stage)
        artifact = await _seed_artifact(conn, doc, "extraction")

    # Nothing recorded yet — the caller must run the stage.
    assert await store.lookup(stage, input_hash) is None

    await store.record(run1, stage, input_hash, [artifact], output_type="extraction")

    hit = await store.lookup(stage, input_hash)
    assert hit is not None, "recorded stage should be reusable"
    assert hit.artifact_ids == [artifact]
    assert hit.source_run_id == run1

    # A different document/run asking for the same input hash also hits: the key
    # is content, not identity. This is the cross-document reuse.
    async with pool.acquire() as conn:
        doc2 = await _seed_document(conn, f"sha2-{tag}")
        run2 = await _seed_run(conn, doc2)

    hit2 = await store.lookup(stage, input_hash)
    assert hit2 is not None
    assert hit2.source_run_id == run1, "second run should reuse run1's artifacts"


async def test_changed_input_does_not_hit(store):
    """Memoization must be keyed on content — a changed section re-runs."""
    pool = await store._get_pool()
    tag = uuid.uuid4().hex
    stage = f"extract.{tag}"

    async with pool.acquire() as conn:
        doc = await _seed_document(conn, f"sha-{tag}")
        run = await _seed_run(conn, doc)
        await _seed_completed_stage(conn, run, stage)
        artifact = await _seed_artifact(conn, doc, "extraction")

    original = stage_input_hash({"section": "3.1", "text": "original"})
    await store.record(run, stage, original, [artifact])

    assert await store.lookup(stage, original) is not None
    edited = stage_input_hash({"section": "3.1", "text": "edited"})
    assert await store.lookup(stage, edited) is None, "edited input must not hit"


async def test_producer_version_invalidates_without_ttl(store):
    """Bumping the producer version retires old artifacts — the TTL replacement."""
    pool = await store._get_pool()
    tag = uuid.uuid4().hex
    stage = f"extract.{tag}"
    payload = {"section": "5.2", "text": "clause"}

    async with pool.acquire() as conn:
        doc = await _seed_document(conn, f"sha-{tag}")
        run = await _seed_run(conn, doc)
        await _seed_completed_stage(conn, run, stage)
        artifact = await _seed_artifact(conn, doc, "extraction")

    v1 = stage_input_hash(payload, producer_version="v1")
    await store.record(run, stage, v1, [artifact])
    assert await store.lookup(stage, v1) is not None

    v2 = stage_input_hash(payload, producer_version="v2")
    assert await store.lookup(stage, v2) is None, "new producer version must recompute"


async def test_incomplete_stage_is_not_reusable(store):
    """A stage that failed or is still running must not serve a memo hit."""
    pool = await store._get_pool()
    tag = uuid.uuid4().hex
    stage = f"extract.{tag}"
    input_hash = stage_input_hash({"t": tag})

    async with pool.acquire() as conn:
        doc = await _seed_document(conn, f"sha-{tag}")
        run = await _seed_run(conn, doc)
        await conn.execute(
            "INSERT INTO cyrex.pipeline_run_stages (run_id, stage_name, status, producer) "
            "VALUES ($1, $2, 'failed', 'extract_stage')",
            run,
            stage,
        )
        artifact = await _seed_artifact(conn, doc, "extraction")

    await store.record(run, stage, input_hash, [artifact])
    assert await store.lookup(stage, input_hash) is None, "failed stage was reused"


async def test_deleted_artifact_is_not_reusable(store):
    """Soft-deleted artifacts must not be served from the memo."""
    pool = await store._get_pool()
    tag = uuid.uuid4().hex
    stage = f"extract.{tag}"
    input_hash = stage_input_hash({"t": tag})

    async with pool.acquire() as conn:
        doc = await _seed_document(conn, f"sha-{tag}")
        run = await _seed_run(conn, doc)
        await _seed_completed_stage(conn, run, stage)
        artifact = await _seed_artifact(conn, doc, "extraction")

    await store.record(run, stage, input_hash, [artifact])
    assert await store.lookup(stage, input_hash) is not None

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE cyrex.artifacts SET is_deleted = TRUE WHERE artifact_id = $1",
            uuid.UUID(artifact),
        )

    assert await store.lookup(stage, input_hash) is None, "deleted artifact was reused"


async def test_record_is_idempotent(store):
    """Re-recording the same stage must not duplicate rows or raise."""
    pool = await store._get_pool()
    tag = uuid.uuid4().hex
    stage = f"extract.{tag}"
    input_hash = stage_input_hash({"t": tag})

    async with pool.acquire() as conn:
        doc = await _seed_document(conn, f"sha-{tag}")
        run = await _seed_run(conn, doc)
        await _seed_completed_stage(conn, run, stage)
        artifact = await _seed_artifact(conn, doc, "extraction")

    await store.record(run, stage, input_hash, [artifact])
    await store.record(run, stage, input_hash, [artifact])

    hit = await store.lookup(stage, input_hash)
    assert hit is not None
    assert hit.artifact_ids == [artifact], "duplicate record produced duplicate outputs"
