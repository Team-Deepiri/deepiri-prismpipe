"""Tests for Deepiri platform capability nodes (mocked HTTP)."""

from unittest.mock import patch

import pytest

from prismpipe.deepiri_nodes import register_deepiri_nodes
from prismpipe.engine import PrismEngine


def _ok(url: str) -> dict:
    return {"ok": True, "status_code": 200, "body": {"status": "ok"}, "url": url}


def _fail(url: str) -> dict:
    return {"ok": False, "status_code": 0, "body": {"error": "down"}, "url": url}


@pytest.mark.asyncio
async def test_deepiri_health_pipeline_marks_useful_when_auth_and_lis_ok():
    engine = PrismEngine()
    register_deepiri_nodes(engine)

    def fake_get(url: str, timeout_s: float = 5.0):
        if "auth" in url:
            return _ok(url)
        if "language-intelligence" in url or ":5003" in url:
            return _ok(url)
        return _fail(url)

    with patch("prismpipe.deepiri_nodes._http_get_json", side_effect=fake_get):
        org = engine.spawn_organism(
            intent="deepiri.health",
            input_data={"probe": "health"},
            initial_capability="deepiri.auth.health",
        )
        await engine.execute_organism(org)

    report = org.state["deepiri_report"]
    assert report["useful"] is True
    assert "auth" in report["reachable"]
    assert "lis" in report["reachable"]


@pytest.mark.asyncio
async def test_deepiri_pipeline_dedups_identical_probes():
    engine = PrismEngine()
    register_deepiri_nodes(engine)
    calls = {"n": 0}

    def fake_get(url: str, timeout_s: float = 5.0):
        calls["n"] += 1
        return _ok(url)

    with patch("prismpipe.deepiri_nodes._http_get_json", side_effect=fake_get):
        for _ in range(3):
            org = engine.spawn_organism(
                intent="deepiri.health",
                input_data={"probe": "health"},
                initial_capability="deepiri.auth.health",
            )
            await engine.execute_organism(org)

    # First run hits 3 capabilities; subsequent runs should reuse cached outputs
    assert calls["n"] == 3
    stats = engine.computation_graph.get_deduplication_stats()
    assert stats["hits"] >= 2
    assert stats["hit_ratio"] > 0
