"""Deepiri platform capability nodes — real HTTP hops into platform services.

These nodes make PrismPipe useful as a capability-routed façade over auth, LIS,
and Cyrex rather than demo stubs.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from prismpipe.core.node import Node, NodeResult
from prismpipe.engine import PrismEngine


def _env_url(name: str, default: str) -> str:
    return os.getenv(name, default).rstrip("/")


def _http_get_json(url: str, timeout_s: float = 0.8) -> dict[str, Any]:
    try:
        timeout = httpx.Timeout(timeout_s, connect=min(0.4, timeout_s))
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
            body: Any
            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text[:500]}
            return {
                "ok": 200 <= resp.status_code < 300,
                "status_code": resp.status_code,
                "body": body,
                "url": url,
            }
    except Exception as exc:
        return {
            "ok": False,
            "status_code": 0,
            "body": {"error": str(exc)},
            "url": url,
        }


class DeepiriAuthHealthNode(Node):
    """Probe auth-service /health."""

    capability = "deepiri.auth.health"

    def process(self, envelope):
        base = _env_url("AUTH_SERVICE_URL", "http://auth-service:5001")
        result = _http_get_json(f"{base}/health")
        envelope.state["auth_health"] = result
        envelope.set_next("deepiri.lis.health")
        return NodeResult(envelope=envelope, success=True)


class DeepiriLisHealthNode(Node):
    """Probe language-intelligence-service /health."""

    capability = "deepiri.lis.health"

    def process(self, envelope):
        base = _env_url(
            "LANGUAGE_INTELLIGENCE_SERVICE_URL",
            "http://language-intelligence-service:5003",
        )
        result = _http_get_json(f"{base}/health")
        envelope.state["lis_health"] = result
        envelope.set_next("deepiri.cyrex.health")
        return NodeResult(envelope=envelope, success=True)


class DeepiriCyrexHealthNode(Node):
    """Probe cyrex /health (optional — soft-fail / skip if unset or down)."""

    capability = "deepiri.cyrex.health"

    def process(self, envelope):
        # Empty CYREX_URL skips the hop (avoids ~10s DNS waits when cyrex isn't deployed).
        raw = os.getenv("CYREX_URL", "http://cyrex:8000").strip()
        if not raw or raw.lower() in {"-", "none", "off", "disabled"}:
            envelope.state["cyrex_health"] = {
                "ok": False,
                "status_code": 0,
                "body": {"skipped": True, "reason": "CYREX_URL unset"},
                "url": "",
            }
            envelope.set_next("deepiri.aggregate")
            return NodeResult(envelope=envelope, success=True)
        result = _http_get_json(f"{raw.rstrip('/')}/health", timeout_s=0.5)
        envelope.state["cyrex_health"] = result
        envelope.set_next("deepiri.aggregate")
        return NodeResult(envelope=envelope, success=True)


class DeepiriAggregateNode(Node):
    """Aggregate downstream probe results into a usefulness report."""

    capability = "deepiri.aggregate"

    def process(self, envelope):
        probes = {
            "auth": envelope.state.get("auth_health", {}),
            "lis": envelope.state.get("lis_health", {}),
            "cyrex": envelope.state.get("cyrex_health", {}),
        }
        reachable = [name for name, p in probes.items() if p.get("ok")]
        # Auth + LIS are the hard usefulness bar; Cyrex may be offline in many team envs
        useful = bool(probes.get("auth", {}).get("ok")) and bool(
            probes.get("lis", {}).get("ok")
        )
        envelope.state["deepiri_report"] = {
            "probes": probes,
            "reachable": reachable,
            "useful": useful,
            "required_ok": useful,
        }
        envelope.state["result"] = envelope.state["deepiri_report"]
        envelope.set_next(None)
        return NodeResult(envelope=envelope, success=True)


def register_deepiri_nodes(engine: PrismEngine) -> PrismEngine:
    """Register Deepiri platform nodes on a PrismEngine."""
    for node in (
        DeepiriAuthHealthNode(),
        DeepiriLisHealthNode(),
        DeepiriCyrexHealthNode(),
        DeepiriAggregateNode(),
    ):
        engine.register_node(node)
        engine.intent_planner.register_capability(
            node.capability,
            node.description or node.capability,
            keywords=["deepiri", "health", "auth", "lis", "cyrex"],
        )
    return engine
