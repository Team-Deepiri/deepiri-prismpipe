"""Integration-style persistence tests (in-process storage backends)."""

import pytest

from prismpipe.engine import Organism, OrganismPersistence
from prismpipe.storage import MemoryStorage
from prismpipe.storage_redis import RedisStorage


class FakeRedis:
    """Minimal async redis stand-in for integration without a live server."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def scan_iter(self, match=None):
        prefix = (match or "*").rstrip("*")
        for key in list(self.store):
            if key.startswith(prefix):
                yield key

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_redis_storage_hibernate_wake_with_fake_client():
    fake = FakeRedis()
    storage = RedisStorage(client=fake, prefix="prismpipe:", ttl_seconds=60)
    persistence = OrganismPersistence(storage_backend=storage)

    org = Organism(intent="i", input_data={"n": 5})
    org.state["result"] = 10
    hib_id = await persistence.hibernate(org)

    # New persistence layer (process restart simulation)
    storage2 = RedisStorage(client=fake, prefix="prismpipe:", ttl_seconds=60)
    persistence2 = OrganismPersistence(storage_backend=storage2)
    restored = await persistence2.wake(hib_id)
    assert restored is not None
    assert restored.state["result"] == 10


@pytest.mark.asyncio
async def test_memory_storage_list_keys():
    storage = MemoryStorage()
    await storage.save("hib_a", {"x": 1})
    await storage.save("hib_b", {"x": 2})
    keys = await storage.list_keys("hib_")
    assert set(keys) == {"hib_a", "hib_b"}
