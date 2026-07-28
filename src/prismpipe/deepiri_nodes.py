"""Deepiri platform capability nodes — real HTTP hops into platform services.

These nodes make PrismPipe useful as a capability-routed façade over auth, LIS,
and Cyrex rather than demo stubs.

Key productivity pipelines:
  - deepiri.health.parallel — auth+LIS health in parallel (cold ≈ max hop, not sum)
  - deepiri.session.bootstrap — auth /auth/verify + LIS /health in one call
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from prismpipe.core.node import Node, NodeResult
from prismpipe.engine import PrismEngine

_client_lock = threading.Lock()
_shared_client: httpx.Client | None = None


def _env_url(name: str, default: str) -> str:
    return os.getenv(name, default).rstrip("/")


def _shared_http_client() -> httpx.Client:
    """Process-wide keep-alive client — cuts cold-path TCP/TLS setup to auth/LIS."""
    global _shared_client
    if _shared_client is not None:
        return _shared_client
    with _client_lock:
        if _shared_client is None:
            max_conn = int(os.getenv("DEEPIRI_HTTP_MAX_CONNECTIONS", "32"))
            max_keep = int(os.getenv("DEEPIRI_HTTP_MAX_KEEPALIVE", "16"))
            _shared_client = httpx.Client(
                limits=httpx.Limits(
                    max_connections=max_conn,
                    max_keepalive_connections=max_keep,
                ),
                http2=False,
            )
        return _shared_client


def _http_request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    if timeout_s is None:
        timeout_s = float(os.getenv("DEEPIRI_PROBE_TIMEOUT_S", "0.8"))
    try:
        timeout = httpx.Timeout(timeout_s, connect=min(0.4, timeout_s))
        client = _shared_http_client()
        resp = client.request(method, url, headers=headers or {}, timeout=timeout)
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


def _http_get_json(url: str, timeout_s: float | None = None) -> dict[str, Any]:
    return _http_request_json("GET", url, timeout_s=timeout_s)


class DeepiriAuthHealthNode(Node):
    """Probe auth-service /health (sequential chain compatibility)."""

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


class DeepiriParallelHealthNode(Node):
    """Auth + LIS health in parallel — cold path ≈ slowest hop, not sum."""

    capability = "deepiri.health.parallel"

    def process(self, envelope):
        auth_base = _env_url("AUTH_SERVICE_URL", "http://auth-service:5001")
        lis_base = _env_url(
            "LANGUAGE_INTELLIGENCE_SERVICE_URL",
            "http://language-intelligence-service:5003",
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            auth_f = pool.submit(_http_get_json, f"{auth_base}/health")
            lis_f = pool.submit(_http_get_json, f"{lis_base}/health")
            envelope.state["auth_health"] = auth_f.result()
            envelope.state["lis_health"] = lis_f.result()
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
        result = _http_get_json(
            f"{raw.rstrip('/')}/health",
            timeout_s=float(os.getenv("DEEPIRI_CYREX_PROBE_TIMEOUT_S", "0.5")),
        )
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


class DeepiriSessionBootstrapNode(Node):
    """One-call session bootstrap: auth verify + LIS readiness in parallel.

    Replaces two client round-trips (gateway→auth verify, gateway→LIS health)
    with a single PrismPipe call. Identical Authorization inputs share computation
    (short Redis TTL) so bursts add near-zero auth load.
    """

    capability = "deepiri.session.bootstrap"

    def process(self, envelope):
        auth_base = _env_url("AUTH_SERVICE_URL", "http://auth-service:5001")
        lis_base = _env_url(
            "LANGUAGE_INTELLIGENCE_SERVICE_URL",
            "http://language-intelligence-service:5003",
        )
        authorization = (
            envelope.input.get("authorization")
            or envelope.input.get("Authorization")
            or ""
        )
        if authorization and not str(authorization).lower().startswith("bearer "):
            authorization = f"Bearer {authorization}"

        def _verify() -> dict[str, Any]:
            if not authorization:
                return {
                    "ok": False,
                    "status_code": 401,
                    "body": {"error": "authorization required"},
                    "url": f"{auth_base}/auth/verify",
                }
            return _http_request_json(
                "GET",
                f"{auth_base}/auth/verify",
                headers={"Authorization": str(authorization)},
                timeout_s=float(os.getenv("DEEPIRI_AUTH_VERIFY_TIMEOUT_S", "1.5")),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            verify_f = pool.submit(_verify)
            lis_f = pool.submit(_http_get_json, f"{lis_base}/health")
            auth_verify = verify_f.result()
            lis_health = lis_f.result()

        user = None
        if auth_verify.get("ok") and isinstance(auth_verify.get("body"), dict):
            user = auth_verify["body"].get("user") or auth_verify["body"]

        useful = bool(auth_verify.get("ok")) and bool(lis_health.get("ok"))
        session = {
            "authenticated": bool(auth_verify.get("ok")),
            "user": user,
            "lis_ready": bool(lis_health.get("ok")),
            "auth_verify": auth_verify,
            "lis_health": lis_health,
            "useful": useful,
            # Client saved one RTT vs calling auth+LIS separately.
            "productivity": {
                "client_round_trips_saved": 1,
                "parallel_hops": ["auth.verify", "lis.health"],
                "downstream_http_calls": 2,
            },
        }
        envelope.state["session"] = session
        envelope.state["result"] = session
        envelope.state["deepiri_report"] = {
            "useful": useful,
            "required_ok": useful,
            "probes": {"auth_verify": auth_verify, "lis": lis_health},
        }
        envelope.set_next(None)
        return NodeResult(envelope=envelope, success=True)


def register_deepiri_nodes(engine: PrismEngine) -> PrismEngine:
    """Register Deepiri platform nodes on a PrismEngine."""
    for node in (
        DeepiriAuthHealthNode(),
        DeepiriLisHealthNode(),
        DeepiriParallelHealthNode(),
        DeepiriCyrexHealthNode(),
        DeepiriAggregateNode(),
        DeepiriSessionBootstrapNode(),
    ):
        engine.register_node(node)
        engine.intent_planner.register_capability(
            node.capability,
            node.description or node.capability,
            keywords=["deepiri", "health", "auth", "lis", "cyrex", "session"],
        )
    return engine
