"""Content-addressed stage memoization against the Cyrex AGI plane.

The AGI schema records `pipeline_stage_inputs.input_hash` alongside the
`pipeline_stage_outputs.artifact_id` a stage produced, but nothing reads that
pairing back. This store does, which is what turns it from provenance into
memoization: before running a stage, ask whether *any previous run* — this
document or another — already executed that stage against the same input hash,
and reuse its artifacts instead of re-paying for the work.

Why this is not the Redis ComputationGraph:

- Keys are content, not identity, so entries do not expire. The same input hash
  always yields the same artifacts; invalidation is by producer/schema version,
  not by TTL. See docs/PRISMPIPE_REPURPOSING_PLAN.md.
- Hits survive process restarts and cross workers, because the durable record is
  the pipeline table the orchestrator already writes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from prismpipe.exceptions import StorageError

# Lookup is by (stage_name, input_hash) across every run. The shipped PK on
# pipeline_stage_inputs is (run_id, stage_name, input_ref), which does not serve
# that predicate — see migration 031_pipeline_memo_index.sql.
_LOOKUP_SQL = """
SELECT o.artifact_id, o.output_type, i.run_id
FROM cyrex.pipeline_stage_inputs i
JOIN cyrex.pipeline_stage_outputs o
  ON o.run_id = i.run_id AND o.stage_name = i.stage_name
JOIN cyrex.artifacts a
  ON a.artifact_id = o.artifact_id AND a.is_deleted = FALSE
JOIN cyrex.pipeline_run_stages s
  ON s.run_id = i.run_id AND s.stage_name = i.stage_name
WHERE i.stage_name = $1
  AND i.input_hash = $2
  AND s.status = 'completed'
ORDER BY s.completed_at DESC NULLS LAST
LIMIT $3
"""

_RECORD_INPUT_SQL = """
INSERT INTO cyrex.pipeline_stage_inputs (run_id, stage_name, input_hash, input_ref)
VALUES ($1, $2, $3, $4)
ON CONFLICT (run_id, stage_name, input_ref) DO UPDATE SET input_hash = EXCLUDED.input_hash
"""

_RECORD_OUTPUT_SQL = """
INSERT INTO cyrex.pipeline_stage_outputs (run_id, stage_name, artifact_id, output_type)
VALUES ($1, $2, $3, $4)
ON CONFLICT (run_id, stage_name, artifact_id) DO NOTHING
"""


def stage_input_hash(payload: Any, *, producer_version: str = "v1") -> str:
    """Stable content hash for a stage's inputs.

    `producer_version` is part of the key on purpose: when a producer's
    behaviour changes, old artifacts must stop matching rather than being
    served for inputs a new version would extract differently. That is the
    invalidation story replacing TTL.
    """
    content = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(f"{producer_version}:{content}".encode()).hexdigest()


@dataclass
class StageMemoHit:
    stage_name: str
    input_hash: str
    artifact_ids: list[str] = field(default_factory=list)
    output_types: list[str | None] = field(default_factory=list)
    source_run_id: str | None = None


class CyrexStageStore:
    """Async reader/writer for stage-level memoization on the AGI plane."""

    def __init__(self, dsn: str, pool: Any | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None
        self.hits = 0
        self.misses = 0

    async def _get_pool(self) -> Any:
        if self._pool is None:
            try:
                import asyncpg
            except ImportError as exc:
                raise StorageError(
                    "asyncpg package required for CyrexStageStore. pip install asyncpg"
                ) from exc
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        return self._pool

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
            self._pool = None

    async def lookup(
        self,
        stage_name: str,
        input_hash: str,
        *,
        limit: int = 64,
    ) -> StageMemoHit | None:
        """Return artifacts a completed stage already produced for this input.

        None means "never computed" — the caller must run the stage.
        """
        pool = await self._get_pool()
        rows = await pool.fetch(_LOOKUP_SQL, stage_name, input_hash, limit)
        if not rows:
            self.misses += 1
            return None

        self.hits += 1
        return StageMemoHit(
            stage_name=stage_name,
            input_hash=input_hash,
            artifact_ids=[str(r["artifact_id"]) for r in rows],
            output_types=[r["output_type"] for r in rows],
            source_run_id=str(rows[0]["run_id"]),
        )

    async def record(
        self,
        run_id: str,
        stage_name: str,
        input_hash: str,
        artifact_ids: list[str],
        *,
        input_ref: str = "",
        output_type: str | None = None,
    ) -> None:
        """Record what a stage consumed and produced, so future runs can skip it.

        Written in one transaction: an input row with no matching outputs would
        advertise a memo hit that resolves to nothing.
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    _RECORD_INPUT_SQL, run_id, stage_name, input_hash, input_ref
                )
                for artifact_id in artifact_ids:
                    await conn.execute(
                        _RECORD_OUTPUT_SQL, run_id, stage_name, artifact_id, output_type
                    )

    def stats(self) -> dict[str, Any]:
        lookups = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_ratio": self.hits / max(lookups, 1),
            "lookups": lookups,
        }
