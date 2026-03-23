"""
PrismPipe Server - FastAPI server with all features.

Run with: uvicorn server:app --reload --port 5011
"""

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from uuid import uuid4
import asyncio
import os
import time

START_TIME = time.monotonic()

from prismpipe import PrismEngine, create_envelope
from prismpipe.core import Intent, Node, NodeResult
from prismpipe.engine import ReplayEngine, DiffEngine, SemanticCache


app = FastAPI(
    title="PrismPipe",
    description="Capability-Routed API Pipeline - Requests become the carrier of computation",
    version="0.2.0",
)

# Create the engine
engine = PrismEngine()

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
        content={
            "detail": "Internal Server Error",
            "request_id": request_id,
        },
        headers={"X-Request-ID": request_id},
    )

@app.exception_handler(asyncio.CancelledError)
async def cancelled_handler(request: Request, exc: asyncio.CancelledError):
    request_id = getattr(request.state, "request_id", str(uuid4()))
    return JSONResponse(
        status_code=499,
        content={"detail": "Request cancelled", "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )             

# =============================================================================
# EXAMPLE NODES - Connect to your services
# =============================================================================

class AuthNode(Node):
    """Auth validation node - integrate with your auth service."""
    capability = "auth.validate"
    
    def process(self, envelope):
        token = envelope.input.get("headers", {}).get("Authorization", "")
        
        # In production, call your auth service here
        # auth_service.verify(token)
        
        if token:
            envelope.state["user"] = {
                "id": "user_123",
                "permissions": ["read", "write"],
                "tier": "premium"
            }
            envelope.state["authenticated"] = True
            envelope.set_next("route.request")
        else:
            envelope.state["authenticated"] = False
            envelope.set_next("response.unauthorized")
        
        return NodeResult(envelope=envelope)


class RouteNode(Node):
    """Route to appropriate handler based on path."""
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
    """List users - integrate with your user service."""
    capability = "users.list"
    
    def process(self, envelope):
        # In production, call your user service
        # users = user_service.list()
        
        envelope.state["users"] = [
            {"id": "1", "name": "Alice", "email": "alice@deepiri.ai"},
            {"id": "2", "name": "Bob", "email": "bob@deepiri.ai"},
            {"id": "3", "name": "Charlie", "email": "charlie@deepiri.ai"},
        ]
        envelope.set_next("response.success")
        return NodeResult(envelope=envelope)


class ModelsListNode(Node):
    """List ML models - integrate with modelkit."""
    capability = "models.list"
    
    def process(self, envelope):
        # In production, call modelkit service
        # models = modelkit.list_models()
        
        envelope.state["models"] = [
            {"id": "llama-3-70b", "name": "Llama 3 70B", "provider": "meta"},
            {"id": "gpt-4-turbo", "name": "GPT-4 Turbo", "provider": "openai"},
            {"id": "claude-3", "name": "Claude 3", "provider": "anthropic"},
        ]
        envelope.set_next("response.success")
        return NodeResult(envelope=envelope)


class AnalyticsNode(Node):
    """Compute analytics."""
    capability = "analytics.compute"
    
    def process(self, envelope):
        # In production, call analytics service
        envelope.state["analytics"] = {
            "daily_active_users": 12500,
            "api_calls": 450000,
            "avg_response_time_ms": 45,
        }
        envelope.set_next("response.success")
        return NodeResult(envelope=envelope)


class ResponseSuccessNode(Node):
    """Success response builder."""
    capability = "response.success"
    
    def process(self, envelope):
        data = {}
        for key in ["users", "models", "analytics"]:
            if key in envelope.state:
                data[key] = envelope.state[key]
        
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


# Register all nodes
for node in [
    AuthNode(),
    RouteNode(),
    UsersListNode(),
    ModelsListNode(),
    AnalyticsNode(),
    ResponseSuccessNode(),
    ResponseUnauthorizedNode(),
    ResponseNotFoundNode(),
]:
    engine.register_node(node)


# =============================================================================
# HTTP HANDLERS
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
                }
            )

        return {
            "status": "ready",
            "engine_nodes": node_count,
            "timestamp": time.time(),
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "reason": str(e),
                "timestamp": time.time(),
            }
        )

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def handle_request(request: Request, path: str):
    """Main request handler - converts HTTP to envelope, processes, returns."""
    
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


# =============================================================================
# ENGINE API - Inspect and control the engine
# =============================================================================

@app.get("/engine/capabilities")
async def list_capabilities():
    """List all registered capabilities."""
    return {"capabilities": engine.router.list_capabilities()}


@app.get("/engine/snapshots")
async def list_snapshots():
    """List all request snapshots."""
    return {"snapshots": list(engine.replay_engine._snapshots.keys())}


@app.get("/engine/memory")
async def request_memory():
    """List requests in memory."""
    return {
        "count": len(engine.request_memory._requests),
        "requests": [
            {"id": r.id, "intent": r.intent, "terminated": r.terminated}
            for r in list(engine.request_memory._requests.values())[-10:]
        ]
    }


@app.get("/engine/cache")
async def cache_info():
    """Show semantic cache info."""
    return {
        "entries": len(engine.semantic_cache._cache),
        "intents": list(engine.semantic_cache._intent_index.keys()),
    }


@app.get("/engine/ancestry")
async def ancestry_tree():
    """Show request ancestry."""
    return {"tree": dict(engine.ancestry_tree._children)}


@app.get("/engine/diff/{request_id}")
async def get_diff(request_id: str):
    """Get state diffs for a request."""
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
        ]
    }


@app.post("/engine/replay/{snapshot_id}")
async def replay_snapshot(snapshot_id: str):
    """Replay from a snapshot."""
    envelope = engine.replay_engine.restore(snapshot_id)
    if not envelope:
        return JSONResponse({"error": "Snapshot not found"}, status_code=404)
    
    result = await engine.execute(envelope)
    return {
        "success": not result.terminated,
        "result": result.state,
    }


@app.post("/engine/fork")
async def fork_request():
    """Fork a request with optional patches."""
    # For demo, fork the last request
    requests = list(engine.request_memory._requests.values())
    if not requests:
        return JSONResponse({"error": "No requests to fork"}, status_code=404)
    
    last_req = requests[-1]
    fork = engine.replay_engine.fork(last_req, {"_forked": True})
    
    return {
        "original_id": last_req.id,
        "fork_id": fork.id,
    }


# =============================================================================
# DEMO ENDPOINTS
# =============================================================================

@app.get("/demo/streaming")
async def demo_streaming():
    """Demo streaming response."""
    async def generate():
        for i in range(5):
            await asyncio.sleep(0.5)
            yield f'data: {{"chunk": {i}, "message": "Processing..."}}\n\n'
        yield 'data: {"done": true}\n\n'
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/demo/intent/{intent}")
async def demo_intent_routing(intent: str):
    """Demo intent-based routing."""
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

