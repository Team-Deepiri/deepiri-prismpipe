"""Tests for OrganismPersistence with memory and fake backends."""

import pytest

from prismpipe.engine import Organism, OrganismPersistence, OrganismState
from prismpipe.storage import MemoryStorage


@pytest.mark.asyncio
async def test_hibernate_wake_roundtrip_memory():
    storage = MemoryStorage()
    persistence = OrganismPersistence(storage_backend=storage)

    org = Organism(intent="persist", input_data={"x": 1}, initial_capability="a")
    org.state["result"] = 99
    org.ingest_knowledge("k", "v", 0.8)

    hib_id = await persistence.hibernate(org)
    assert org._state == OrganismState.SUSPENDED
    assert await storage.exists(hib_id)

    # Simulate process restart: new persistence with same storage, empty memory cache
    persistence2 = OrganismPersistence(storage_backend=storage)
    restored = await persistence2.wake(hib_id)
    assert restored is not None
    assert restored.id == org.id
    assert restored.state["result"] == 99
    assert restored.get_knowledge("k") is not None
    assert restored.get_knowledge("k").value == "v"


@pytest.mark.asyncio
async def test_wake_missing_returns_none():
    persistence = OrganismPersistence()
    assert await persistence.wake("hib_missing") is None
