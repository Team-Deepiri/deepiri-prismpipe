"""API contract tests for organism HTTP endpoints."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402


@pytest.fixture
def client():
    return TestClient(server.app)


def test_health_and_ready(client):
    assert client.get("/health").status_code == 200
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_organism_spawn_execute_inspect(client):
    spawn = client.post(
        "/organisms",
        json={"intent": "bench", "input": {"n": 7}, "initial_capability": "bench.compute"},
    )
    assert spawn.status_code == 200
    org_id = spawn.json()["id"]

    executed = client.post(f"/organisms/{org_id}/execute")
    assert executed.status_code == 200
    body = executed.json()
    assert body["organism"]["state"]["result"] == 14

    inspected = client.get(f"/organisms/{org_id}")
    assert inspected.status_code == 200
    assert inspected.json()["state"]["result"] == 14


def test_engine_routes_not_shadowed_by_catchall(client):
    caps = client.get("/engine/capabilities")
    assert caps.status_code == 200
    assert "bench.compute" in caps.json()["capabilities"]


def test_metrics_endpoint(client):
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    data = metrics.json()
    assert "hit_ratio" in data
    assert "execute_p95" in data


def test_hibernate_wake(client):
    spawn = client.post(
        "/organisms",
        json={"intent": "bench", "input": {"n": 3}, "initial_capability": "bench.compute"},
    )
    org_id = spawn.json()["id"]
    client.post(f"/organisms/{org_id}/execute")
    hib = client.post(f"/organisms/{org_id}/hibernate")
    assert hib.status_code == 200
    hib_id = hib.json()["hibernation_id"]
    woken = client.post("/organisms/wake", json={"hibernation_id": hib_id})
    assert woken.status_code == 200
    assert woken.json()["state"]["result"] == 6


def test_deepiri_pipeline_endpoint_registered(client):
    caps = client.get("/engine/capabilities").json()["capabilities"]
    assert "deepiri.auth.health" in caps
    assert "deepiri.aggregate" in caps
    assert "deepiri.session.bootstrap" in caps
    assert "deepiri.health.parallel" in caps

    from unittest.mock import patch

    fake = {
        "ok": True,
        "status_code": 200,
        "body": {"status": "ok"},
        "url": "mock",
    }
    with patch("prismpipe.deepiri_nodes._http_get_json", return_value=fake):
        resp = client.post(
            "/pipelines/deepiri/health",
            json={"input": {"probe": "health"}, "use_computation_sharing": True},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "report" in body
    assert "metrics" in body
    assert body["useful"] is True


