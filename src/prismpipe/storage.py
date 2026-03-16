"""PrismPipe storage backends."""

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Generic, TypeVar

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
        path = self._get_path(key)
        try:
            with open(path, "w") as f:
                json.dump(value, f, default=str)
        except Exception as e:
            raise StorageError("save", str(e))

    async def load(self, key: str) -> T | None:
        path = self._get_path(key)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            raise StorageError("load", str(e))

    async def delete(self, key: str) -> None:
        path = self._get_path(key)
        if path.exists():
            path.unlink()

    async def exists(self, key: str) -> bool:
        return self._get_path(key).exists()

    async def list_keys(self, prefix: str = "") -> list[str]:
        prefix_path = self.base_path / f"{prefix}*" if prefix else self.base_path / "*.json"
        return [p.stem for p in self.base_path.glob(f"{prefix}*.json")]


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
