"""
PrismPipe Server - FastAPI server with organism protocol.

Run with: uvicorn server:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from prismpipe import OrganismPersistence, PrismEngine, create_envelope
from prismpipe.bench_nodes import BenchComputeNode, BenchPartitionNode
from prismpipe.core import Intent, Node, NodeResult
from prismpipe.deepiri_nodes import register_deepiri_nodes
from prismpipe.events import get_event_bus
from prismpipe.storage import MemoryStorage

START_TIME = time.monotonic()

app = FastAPI(
    title="PrismPipe",
    description="Capability-Routed API Pipeline - Requests become the carrier of computation",
    version="0.2.0",
)

engine = PrismEngine(event_bus=get_event_bus())


def _configure_persistence() -> None:
    redis_url = os.getenv("REDIS_URL")
    database_url = os.getenv("DATABASE_URL")
    backend: Any = MemoryStorage()
    if redis_url:
        from prismpipe.storage_redis import RedisStorage

        backend = RedisStorage(url=redis_url)
    elif database_url:
        from prismpipe.storage_postgres import PostgresStorage

        backend = PostgresStorage(dsn=database_url)
    engine.organism_persistence = OrganismPersistence(storage_backend=backend)


_configure_persistence()


# =============================================================================
# DETERMINISTIC DEMO / BENCH NODES
# =============================================================================


class AuthNode(Node):
    capability = "auth.validate"

    def process(self, envelope):
        token = envelope.input.get("headers", {}).get("Authorization", "")
        if token:
            envelope.state["user"] = {
                "id": "user_123",
                "permissions": ["read", "write"],
                "tier": "premium",
            }
            envelope.state["authenticated"] = True
            envelope.set_next("route.request")
        else:
            envelope.state["authenticated"] = False
            envelope.set_next("response.unauthorized")
        return NodeResult(envelope=envelope)


class RouteNode(Node):
    capability = "route.request"

    def process(self, envelope):
        path = envelope.input.get("path", "/")
        if path.startswith("/users"):
            envelope.set_next("users.list")
        elif path.startswith("/models"):
            envelope.set_next("models.list")
        elif path.startswith("/analytics"):
            envelope.set_next("analytics.compute")
        else:
            envelope.set_next("response.not_found")
        return NodeResult(envelope=envelope)


class UsersListNode(Node):
    capability = "users.list"

    def process(self, envelope):
        envelope.state["users"] = [
            {"id": "1", "name": "Alice", "email": "alice@deepiri.ai"},
            {"id": "2", "name": "Bob", "email": "bob@deepiri.ai"},
        ]
        envelope.set_next("response.success")
        return NodeResult(envelope=envelope)


class ModelsListNode(Node):
    capability = "models.list"

    def process(self, envelope):
        envelope.state["models"] = [
            {"id": "llama-3-70b", "name": "Llama 3 70B", "provider": "meta"},
        ]
        envelope.set_next("response.success")
        return NodeResult(envelope=envelope)


class AnalyticsNode(Node):
    capability = "analytics.compute"

    def process(self, envelope):
        n = int(envelope.input.get("body", {}).get("n", envelope.input.get("n", 1)))
        envelope.state["analytics"] = {"n": n, "score": n * 2}
        envelope.set_next("response.success")
        return NodeResult(envelope=envelope)


class ResponseSuccessNode(Node):
    capability = "response.success"

    def process(self, envelope):
        data = {
            k: envelope.state[k]
            for k in ("users", "models", "analytics", "result")
            if k in envelope.state
        }
        envelope.state["http_response"] = {
            "status_code": 200,
            "body": {"success": True, "data": data},
            "headers": {"X-Content-Type-Options": "nosniff"},
        }
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class ResponseUnauthorizedNode(Node):
    capability = "response.unauthorized"

    def process(self, envelope):
        envelope.state["http_response"] = {
            "status_code": 401,
            "body": {"error": "Unauthorized", "code": "AUTH_REQUIRED"},
            "headers": {},
        }
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


class ResponseNotFoundNode(Node):
    capability = "response.not_found"

    def process(self, envelope):
        envelope.state["http_response"] = {
            "status_code": 404,
            "body": {"error": "Not Found", "code": "ROUTE_NOT_FOUND"},
            "headers": {},
        }
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


for node in [
    AuthNode(),
    RouteNode(),
    UsersListNode(),
    ModelsListNode(),
    AnalyticsNode(),
    ResponseSuccessNode(),
    ResponseUnauthorizedNode(),
    ResponseNotFoundNode(),
    BenchComputeNode(),
    BenchPartitionNode(),
]:
    engine.register_node(node)

register_deepiri_nodes(engine)


# =============================================================================
# MIDDLEWARE
# =============================================================================


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    incoming_id = request.headers.get("X-Request-ID")
    request_id = incoming_id if incoming_id and len(incoming_id) < 100 else str(uuid4())
    request.state.request_id = request_id

    start = time.monotonic()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers.setdefault("X-Request-ID", request_id)
        return response
    finally:
        try:
            duration = time.monotonic() - start
            print(f"[{request_id}] {request.method} {request.url.path} {status_code} {duration:.3f}s")
        except Exception:
            pass


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )

# =============================================================================
# HEALTH / ROOT / METRICS  (registered before catch-all)
# =============================================================================


@app.get("/")
async def root():
    return {
        "name": "PrismPipe",
        "version": "0.2.0",
        "description": "Capability-Routed API Pipeline",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "0.2.0",
        "timestamp": time.time(),
        "uptime_seconds": round(time.monotonic() - START_TIME, 2),
    }


@app.get("/ready")
async def ready():
    try:
        node_count = len(engine.router.list_capabilities())
        if node_count == 0:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "reason": "No nodes registered",
                    "timestamp": time.time(),
                },
            )
        return {
            "status": "ready",
            "engine_nodes": node_count,
            "timestamp": time.time(),
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": str(e), "timestamp": time.time()},
        )


@app.get("/metrics")
async def metrics():
    return engine.get_metrics()


# =============================================================================
# ORGANISM API
# =============================================================================


class SpawnRequest(BaseModel):
    intent: str = "custom"
    input: dict[str, Any] = Field(default_factory=dict)
    initial_capability: str | None = "bench.compute"
    parent_organism_id: str | None = None


class WakeRequest(BaseModel):
    hibernation_id: str


def _serialize_organism(organism) -> dict[str, Any]:
    return {
        "id": organism.id,
        "intent": organism.intent.value
        if hasattr(organism.intent, "value")
        else str(organism.intent),
        "input": organism.input,
        "state": organism.state,
        "next": organism._next_capability,
        "terminated": organism.terminated,
        "parent_id": organism._parent_organism_id,
        "knowledge": [
            {"key": k.key, "value": k.value, "confidence": k.confidence}
            for k in organism.knowledge
        ],
        "history": [
            h.model_dump() if hasattr(h, "model_dump") else h for h in organism.history
        ],
    }


@app.post("/organisms")
async def spawn_organism(body: SpawnRequest):
    organism = engine.spawn_organism(
        intent=body.intent,
        input_data=body.input,
        initial_capability=body.initial_capability,
        parent_organism_id=body.parent_organism_id,
    )
    return _serialize_organism(organism)


@app.post("/organisms/{organism_id}/execute")
async def execute_organism(organism_id: str):
    organism = engine.organism_registry.get(organism_id)
    if organism is None:
        return JSONResponse({"error": "Organism not found"}, status_code=404)
    result = await engine.execute_organism(organism)
    mutation = engine.organism_executor.get_mutation(organism_id)
    return {
        "organism": _serialize_organism(result),
        "metrics": engine.get_metrics(),
        "mutations": mutation.get_timeline() if mutation else [],
    }


@app.get("/organisms/{organism_id}")
async def inspect_organism(organism_id: str):
    organism = engine.organism_registry.get(organism_id)
    if organism is None:
        return JSONResponse({"error": "Organism not found"}, status_code=404)
    return _serialize_organism(organism)


@app.get("/organisms/{organism_id}/lineage")
async def organism_lineage(organism_id: str):
    lineage = engine.organism_registry.get_lineage(organism_id)
    if not lineage:
        return JSONResponse({"error": "Organism not found"}, status_code=404)
    return {"lineage": [_serialize_organism(o) for o in lineage]}


@app.post("/organisms/{organism_id}/hibernate")
async def hibernate_organism(organism_id: str):
    organism = engine.organism_registry.get(organism_id)
    if organism is None:
        return JSONResponse({"error": "Organism not found"}, status_code=404)
    hib_id = await engine.organism_persistence.hibernate(organism)
    return {"hibernation_id": hib_id, "organism_id": organism_id}


@app.post("/organisms/wake")
async def wake_organism(body: WakeRequest):
    organism = await engine.organism_persistence.wake(body.hibernation_id)
    if organism is None:
        return JSONResponse({"error": "Hibernation not found"}, status_code=404)
    engine.organism_registry.register(organism)
    return _serialize_organism(organism)


class DeepiriPipelineRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    use_computation_sharing: bool = True


@app.post("/pipelines/deepiri/health")
async def deepiri_health_pipeline(body: DeepiriPipelineRequest | None = None):
    """Multi-hop Deepiri probe: auth∥LIS → cyrex → aggregate.

    Identical requests share computation via ComputationGraph — this is the
    primary usefulness signal for platform wiring.
    """
    payload = body or DeepiriPipelineRequest()
    organism = engine.spawn_organism(
        intent="deepiri.health",
        input_data=payload.input or {"probe": "health"},
        initial_capability="deepiri.health.parallel",
    )
    result = await engine.execute_organism(
        organism,
        use_computation_sharing=payload.use_computation_sharing,
    )
    report = result.state.get("deepiri_report", {})
    return {
        "organism": _serialize_organism(result),
        "report": report,
        "useful": bool(report.get("useful")),
        "metrics": engine.get_metrics(),
    }


@app.get("/pipelines/deepiri/health")
async def deepiri_health_pipeline_get():
    return await deepiri_health_pipeline(DeepiriPipelineRequest())


class DeepiriSessionRequest(BaseModel):
    authorization: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    use_computation_sharing: bool = True


@app.post("/pipelines/deepiri/session")
async def deepiri_session_pipeline(body: DeepiriSessionRequest | None = None):
    """Productivity pipeline: auth verify ∥ LIS health in one call.

    Pass `authorization: Bearer <jwt>` (or put it under input.authorization).
    Warm identical tokens are served from the shared computation cache (Redis
    when configured) so repeated session checks add near-zero auth load.
    """
    payload = body or DeepiriSessionRequest()
    input_data = dict(payload.input or {})
    if payload.authorization:
        input_data["authorization"] = payload.authorization
    organism = engine.spawn_organism(
        intent="deepiri.session",
        input_data=input_data,
        initial_capability="deepiri.session.bootstrap",
    )
    result = await engine.execute_organism(
        organism,
        use_computation_sharing=payload.use_computation_sharing,
    )
    session = result.state.get("session") or result.state.get("result") or {}
    report = result.state.get("deepiri_report", {})
    return {
        "organism": _serialize_organism(result),
        "session": session,
        "report": report,
        "useful": bool(session.get("useful") or report.get("useful")),
        "metrics": engine.get_metrics(),
    }


# =============================================================================
# ENGINE API
# =============================================================================


@app.get("/engine/capabilities")
async def list_capabilities():
    return {"capabilities": engine.router.list_capabilities()}


@app.get("/engine/snapshots")
async def list_snapshots():
    return {"snapshots": list(engine.replay_engine._snapshots.keys())}


@app.get("/engine/memory")
async def request_memory():
    return {
        "count": len(engine.request_memory._requests),
        "requests": [
            {"id": r.id, "intent": str(r.intent), "terminated": r.terminated}
            for r in list(engine.request_memory._requests.values())[-10:]
        ],
    }


@app.get("/engine/cache")
async def cache_info():
    return {
        "entries": len(engine.semantic_cache._cache),
        "intents": list(engine.semantic_cache._intent_index.keys()),
    }


@app.get("/engine/ancestry")
async def ancestry_tree():
    return {"tree": dict(engine.ancestry_tree._children)}


@app.get("/engine/diff/{request_id}")
async def get_diff(request_id: str):
    timeline = engine.diff_engine.get_timeline(request_id)
    return {
        "request_id": request_id,
        "changes": [
            {
                "capability": d.capability,
                "added": d.added,
                "modified": {k: str(v) for k, v in d.modified.items()},
                "removed": d.removed,
                "latency_ms": d.latency_ms,
            }
            for d in timeline
        ],
    }


@app.post("/engine/replay/{snapshot_id}")
async def replay_snapshot(snapshot_id: str):
    envelope = engine.replay_engine.restore(snapshot_id)
    if not envelope:
        return JSONResponse({"error": "Snapshot not found"}, status_code=404)
    result = await engine.execute(envelope)
    return {"success": not result.terminated, "result": result.state}


@app.post("/engine/fork")
async def fork_request():
    requests = list(engine.request_memory._requests.values())
    if not requests:
        return JSONResponse({"error": "No requests to fork"}, status_code=404)
    last_req = requests[-1]
    fork = engine.replay_engine.fork(last_req, {"_forked": True})
    return {"original_id": last_req.id, "fork_id": fork.id}


@app.get("/demo/streaming")
async def demo_streaming():
    async def generate():
        for i in range(5):
            await asyncio.sleep(0.05)
            yield f'data: {{"chunk": {i}, "message": "Processing..."}}\n\n'
        yield 'data: {"done": true}\n\n'

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/demo/intent/{intent}")
async def demo_intent_routing(intent: str):
    envelope = create_envelope(
        intent=intent,
        input_data={"query": "show me analytics"},
        next_capability="auth.validate",
    )
    result = await engine.execute(envelope)
    return {
        "intent": intent,
        "executed": [h.capability for h in result.history],
        "success": not result.terminated,
    }


# =============================================================================
# CATCH-ALL HTTP → envelope (MUST be last)
# =============================================================================


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def handle_request(request: Request, path: str):
    try:
        body = await request.body()
        if body:
            import orjson

            body = orjson.loads(body)
        else:
            body = None
    except Exception:
        body = None

    envelope = create_envelope(
        intent=Intent.HTTP_REQUEST,
        input_data={
            "method": request.method,
            "path": f"/{path}",
            "query_params": dict(request.query_params),
            "headers": dict(request.headers),
            "body": body,
        },
        next_capability="auth.validate",
    )

    result = await engine.execute(envelope)
    response_data = result.state.get("http_response", {})
    return JSONResponse(
        status_code=response_data.get("status_code", 200),
        content=response_data.get("body", {"ok": True}),
        headers=response_data.get("headers", {}),
    )
