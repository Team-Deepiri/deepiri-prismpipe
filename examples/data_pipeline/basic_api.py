"""Example: Basic API Pipeline with authentication and user fetching."""

from prismpipe import Pipeline, create_envelope
from prismpipe.core.envelope import Intent
from prismpipe.core.node import Node, NodeResult


class AuthNode(Node):
    """Validates authentication token."""

    capability = "auth.validate"

    def process(self, envelope) -> NodeResult:
        token = envelope.input.get("headers", {}).get("Authorization")

        if token and token.startswith("Bearer "):
            envelope.state["authenticated"] = True
            envelope.state["user_id"] = "user_123"
            envelope.set_next("data.fetch_users")
        else:
            envelope.state["authenticated"] = False
            envelope.set_next("response.build")

        return NodeResult(envelope=envelope)


class UserFetchNode(Node):
    """Fetches users from database."""

    capability = "data.fetch_users"

    def process(self, envelope) -> NodeResult:
        users = [
            {"id": "1", "name": "Alice", "email": "alice@example.com"},
            {"id": "2", "name": "Bob", "email": "bob@example.com"},
            {"id": "3", "name": "Charlie", "email": "charlie@example.com"},
        ]
        envelope.state["users"] = users
        envelope.state["count"] = len(users)
        envelope.set_next("data.enrich-users")
        return NodeResult(envelope=envelope)


class EnrichUsersNode(Node):
    """Enriches user data with additional information."""

    capability = "data.enrich-users"

    def process(self, envelope) -> NodeResult:
        users = envelope.state.get("users", [])
        enriched = []

        for user in users:
            user["display_name"] = user["name"].upper()
            user["avatar_url"] = f"https://avatars.example.com/{user['id']}"
            enriched.append(user)

        envelope.state["users"] = enriched
        envelope.set_next("response.build")
        return NodeResult(envelope=envelope)


class ResponseBuildNode(Node):
    """Builds the final HTTP response."""

    capability = "response.build"

    def process(self, envelope) -> NodeResult:
        if not envelope.state.get("authenticated", False):
            envelope.state["response_body"] = {
                "error": "Unauthorized",
                "code": "AUTH_REQUIRED",
            }
            envelope.state["status_code"] = 401
        else:
            envelope.state["response_body"] = {
                "users": envelope.state.get("users", []),
                "count": envelope.state.get("count", 0),
            }
            envelope.state["status_code"] = 200

        envelope.set_next(None)
        return NodeResult(envelope=envelope)


def create_api_pipeline() -> Pipeline:
    """Create and configure the API pipeline."""
    pipeline = Pipeline()
    pipeline.register_nodes([
        AuthNode(),
        UserFetchNode(),
        EnrichUsersNode(),
        ResponseBuildNode(),
    ])
    return pipeline


if __name__ == "__main__":
    pipeline = create_api_pipeline()

    envelope = create_envelope(
        intent=Intent.HTTP_REQUEST,
        input={
            "method": "GET",
            "path": "/api/users",
            "headers": {"Authorization": "Bearer test_token_123"},
        },
        next="auth.validate",
    )

    result = pipeline.execute(envelope)

    print(f"Success: {result.success}")
    print(f"Iterations: {result.iterations}")
    print(f"Response: {result.envelope.state.get('response_body')}")
    print(f"History: {[h.capability for h in result.envelope.history]}")
