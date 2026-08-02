"""PrismPipe storage backends."""

import asyncio
import json
import os
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Generic, TypeVar, cast

from prismpipe.exceptions import StorageError

T = TypeVar("T")


class StorageBackend(ABC, Generic[T]):
    """Abstract storage backend."""

    @abstractmethod
    async def save(self, key: str, value: T) -> None:
        """Save a value."""
        pass

    @abstractmethod
    async def load(self, key: str) -> T | None:
        """Load a value."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Delete a value."""
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if key exists."""
        pass

    @abstractmethod
    async def list_keys(self, prefix: str = "") -> list[str]:
        """List keys with optional prefix."""
        pass


class MemoryStorage(StorageBackend[T]):
    """In-memory storage backend."""

    def __init__(self):
        self._store: dict[str, T] = {}

    async def save(self, key: str, value: T) -> None:
        self._store[key] = value

    async def load(self, key: str) -> T | None:
        return self._store.get(key)

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def list_keys(self, prefix: str = "") -> list[str]:
        if prefix:
            return [k for k in self._store.keys() if k.startswith(prefix)]
        return list(self._store.keys())


class FileStorage(StorageBackend[T]):
    """File system storage backend."""

    def __init__(self, base_path: str | Path = "./data"):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _get_path(self, key: str) -> Path:
        safe_key = key.replace("..", "_").replace("/", "_")
        return self.base_path / f"{safe_key}.json"

    async def save(self, key: str, value: T) -> None:
        await asyncio.to_thread(self._save_sync, key, value)

    def _save_sync(self, key: str, value: T) -> None:
        path = self._get_path(key)
        temporary_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temporary_path, "w", encoding="utf-8") as handle:
                json.dump(value, handle, default=str, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(path)
        except Exception as error:
            temporary_path.unlink(missing_ok=True)
            raise StorageError("save", str(error)) from error

    async def load(self, key: str) -> T | None:
        return await asyncio.to_thread(self._load_sync, key)

    def _load_sync(self, key: str) -> T | None:
        path = self._get_path(key)
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                return cast(T, json.load(handle))
        except Exception as error:
            raise StorageError("load", str(error)) from error

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._get_path(key).unlink, missing_ok=True)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._get_path(key).exists)

    async def list_keys(self, prefix: str = "") -> list[str]:
        return await asyncio.to_thread(self._list_keys_sync, prefix)

    def _list_keys_sync(self, prefix: str) -> list[str]:
        return sorted(path.stem for path in self.base_path.glob(f"{prefix}*.json"))


class SnapshotStorage(FileStorage[dict[str, Any]]):
    """Storage for request snapshots."""

    def __init__(self, base_path: str | Path = "./snapshots"):
        super().__init__(base_path)


class RequestStorage(FileStorage[dict[str, Any]]):
    """Storage for persistent requests."""

    def __init__(self, base_path: str | Path = "./requests"):
        super().__init__(base_path)


# Default instances
_default_snapshot_storage: SnapshotStorage | None = None
_default_request_storage: RequestStorage | None = None
_default_memory_storage: MemoryStorage | None = None


def get_snapshot_storage() -> SnapshotStorage:
    """Get default snapshot storage."""
    global _default_snapshot_storage
    if _default_snapshot_storage is None:
        _default_snapshot_storage = SnapshotStorage()
    return _default_snapshot_storage


def get_request_storage() -> RequestStorage:
    """Get default request storage."""
    global _default_request_storage
    if _default_request_storage is None:
        _default_request_storage = RequestStorage()
    return _default_request_storage


def get_memory_storage() -> MemoryStorage:
    """Get default memory storage."""
    global _default_memory_storage
    if _default_memory_storage is None:
        _default_memory_storage = MemoryStorage()
    return _default_memory_storage


def set_snapshot_storage(storage: SnapshotStorage) -> None:
    """Set default snapshot storage."""
    global _default_snapshot_storage
    _default_snapshot_storage = storage


def set_request_storage(storage: RequestStorage) -> None:
    """Set default request storage."""
    global _default_request_storage
    _default_request_storage = storage
