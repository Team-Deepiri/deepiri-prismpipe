"""Example: HTTP API Gateway with FastAPI."""

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from prismpipe import Pipeline, create_envelope
from prismpipe.core.envelope import Intent
from prismpipe.core.node import Node, NodeResult


class UserIn(BaseModel):
    name: str
    email: str


class UserOut(BaseModel):
    id: str
    name: str
    email: str


class CreateUserNode(Node):
    """Creates a new user."""

    capability = "user.create"

    def process(self, envelope) -> NodeResult:
        user_data = envelope.input.get("body", {})
        user = {
            "id": "user_123",
            "name": user_data.get("name"),
            "email": user_data.get("email"),
        }
        envelope.state["user"] = user
        envelope.set_next("response.build")
        return NodeResult(envelope=envelope)


class GetUserNode(Node):
    """Gets a user by ID."""

    capability = "user.get"

    def process(self, envelope) -> NodeResult:
        user_id = envelope.input.get("path_params", {}).get("id", "unknown")
        user = {
            "id": user_id,
            "name": "John Doe",
            "email": "john@example.com",
        }
        envelope.state["user"] = user
        envelope.set_next("response.build")
        return NodeResult(envelope=envelope)


class ListUsersNode(Node):
    """Lists all users."""

    capability = "user.list"

    def process(self, envelope) -> NodeResult:
        users = [
            {"id": "1", "name": "Alice", "email": "alice@example.com"},
            {"id": "2", "name": "Bob", "email": "bob@example.com"},
        ]
        envelope.state["users"] = users
        envelope.set_next("response.build")
        return NodeResult(envelope=envelope)


class ResponseBuildNode(Node):
    """Builds HTTP response."""

    capability = "response.build"

    def process(self, envelope) -> NodeResult:
        status = 200

        if envelope.state.get("user"):
            body = envelope.state["user"]
        elif envelope.state.get("users"):
            body = {"users": envelope.state["users"]}
        else:
            body = {"ok": True}

        if envelope.error:
            status = envelope.state.get("error_status_code", 500)
            body = {"error": envelope.error}

        envelope.state["http_response"] = {
            "status_code": status,
            "body": body,
            "headers": {},
        }
        envelope.set_next(None)
        return NodeResult(envelope=envelope)


def create_api_pipeline() -> Pipeline:
    """Create the API pipeline."""
    pipeline = Pipeline()
    pipeline.register_nodes([
        CreateUserNode(),
        GetUserNode(),
        ListUsersNode(),
        ResponseBuildNode(),
    ])
    return pipeline


app = FastAPI(title="PrismPipe API")
pipeline = create_api_pipeline()


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def handle_request(request: Request, path: str):
    """Route all requests through the pipeline."""
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
        input={
            "method": request.method,
            "path": f"/{path}",
            "query_params": dict(request.query_params),
            "headers": dict(request.headers),
            "body": body,
            "path_params": {"id": path.split("/")[-1]} if path else {},
        },
        next="user.list" if request.method == "GET" and not path else None,
    )

    if request.method == "POST":
        envelope.set_next("user.create")
    elif request.method == "GET" and path:
        envelope.set_next("user.get")
    elif request.method == "GET":
        envelope.set_next("user.list")

    result = pipeline.execute(envelope)

    response_data = result.envelope.state.get("http_response", {})
    return response_data.get("body"), response_data.get("status_code", 200)


@app.get("/health")
async def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
