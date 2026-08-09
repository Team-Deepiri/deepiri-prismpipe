"""Redis hot storage backend for PrismPipe organisms and computation cache."""

from __future__ import annotations

import json
from typing import Any, TypeVar

from prismpipe.exceptions import StorageError
from prismpipe.storage import StorageBackend

T = TypeVar("T")


class RedisStorage(StorageBackend[T]):
    """Async Redis storage with optional TTL."""

    def __init__(
        self,
        url: str = "redis://localhost:6379/0",
        prefix: str = "prismpipe:",
        ttl_seconds: int | None = 86400,
        client: Any | None = None,
    ) -> None:
        self._url = url
        self._prefix = prefix
        self._ttl_seconds = ttl_seconds
        self._client = client
        self._owns_client = client is None

    async def _get_client(self) -> Any:
        if self._client is None:
            try:
                import redis.asyncio as redis
            except ImportError as exc:
                raise StorageError(
                    "redis package required for RedisStorage. pip install redis"
                ) from exc
            self._client = redis.from_url(self._url, decode_responses=True)
        return self._client

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def _serialize(self, value: T) -> str:
        return json.dumps(value, default=str)

    def _deserialize(self, raw: str) -> T:
        return json.loads(raw)  # type: ignore[return-value]

    async def save(self, key: str, value: T) -> None:
        try:
            client = await self._get_client()
            payload = self._serialize(value)
            redis_key = self._key(key)
            if self._ttl_seconds is not None:
                await client.set(redis_key, payload, ex=self._ttl_seconds)
            else:
                await client.set(redis_key, payload)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Redis save failed: {exc}") from exc

    async def load(self, key: str) -> T | None:
        try:
            client = await self._get_client()
            raw = await client.get(self._key(key))
            if raw is None:
                return None
            return self._deserialize(raw)
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(f"Redis load failed: {exc}") from exc

    async def delete(self, key: str) -> None:
        try:
            client = await self._get_client()
            await client.delete(self._key(key))
        except Exception as exc:
            raise StorageError(f"Redis delete failed: {exc}") from exc

    async def exists(self, key: str) -> bool:
        try:
            client = await self._get_client()
            return bool(await client.exists(self._key(key)))
        except Exception as exc:
            raise StorageError(f"Redis exists failed: {exc}") from exc

    async def list_keys(self, prefix: str = "") -> list[str]:
        try:
            client = await self._get_client()
            pattern = self._key(f"{prefix}*")
            keys: list[str] = []
            async for key in client.scan_iter(match=pattern):
                keys.append(str(key)[len(self._prefix) :])
            return keys
        except Exception as exc:
            raise StorageError(f"Redis list_keys failed: {exc}") from exc

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None
