"""Postgres cold storage backend for PrismPipe organism lineage and knowledge."""

from __future__ import annotations

import json
from typing import Any, TypeVar

from prismpipe.exceptions import StorageError
from prismpipe.storage import StorageBackend

T = TypeVar("T")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prismpipe_kv (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS prismpipe_kv_key_prefix_idx
    ON prismpipe_kv (key text_pattern_ops);
"""


class PostgresStorage(StorageBackend[T]):
    """Async Postgres JSONB key-value storage."""

    def __init__(
        self,
        dsn: str = "postgresql://postgres:postgres@localhost:5432/prismpipe",
        pool: Any | None = None,
    ) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None
        self._initialized = False

    async def _get_pool(self) -> Any:
        if self._pool is None:
            try:
                import asyncpg
            except ImportError as exc:
                raise StorageError(
                    "asyncpg package required for PostgresStorage. pip install asyncpg"
                ) from exc
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=5)
        if not self._initialized:
            async with self._pool.acquire() as conn:
                await conn.execute(SCHEMA_SQL)
            self._initialized = True
        return self._pool

    async def save(self, key: str, value: T) -> None:
        try:
            pool = await self._get_pool()
            payload = json.dumps(value, default=str)
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO prismpipe_kv(key, value, updated_at)
                    VALUES ($1, $2::jsonb, NOW())
                    ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = NOW()
                    """,
                    key,
                    payload,
                )
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Postgres save failed: {exc}") from exc

    async def load(self, key: str) -> T | None:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT value FROM prismpipe_kv WHERE key = $1", key
                )
            if row is None:
                return None
            value = row["value"]
            if isinstance(value, str):
                return json.loads(value)  # type: ignore[return-value]
            return value  # type: ignore[return-value]
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Postgres load failed: {exc}") from exc

    async def delete(self, key: str) -> None:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM prismpipe_kv WHERE key = $1", key)
        except Exception as exc:
            raise StorageError(f"Postgres delete failed: {exc}") from exc

    async def exists(self, key: str) -> bool:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT 1 FROM prismpipe_kv WHERE key = $1", key
                )
            return row is not None
        except Exception as exc:
            raise StorageError(f"Postgres exists failed: {exc}") from exc

    async def list_keys(self, prefix: str = "") -> list[str]:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT key FROM prismpipe_kv WHERE key LIKE $1 ORDER BY key",
                    f"{prefix}%",
                )
            return [r["key"] for r in rows]
        except Exception as exc:
            raise StorageError(f"Postgres list_keys failed: {exc}") from exc

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
            self._pool = None
            self._initialized = False
