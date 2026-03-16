"""Example: Using the Python SDK with decorators."""

from prismpipe.sdk import PrismPipe, node, enrich, transform, validate
from prismpipe.core.node import NodeResult
from prismpipe.core.envelope import Intent, create_envelope


pp = PrismPipe()


@node(capability="auth.validate", description="Validate API key")
def validate_api_key(envelope):
    api_key = envelope.input.get("headers", {}).get("X-API-Key")

    if api_key == "secret_key_123":
        envelope.state["user"] = {"id": "user_1", "tier": "premium"}
        envelope.set_next("process.request")
    else:
        envelope.terminate("Invalid API key")

    return envelope


@transform(capability="transform.normalize")
def normalize_request(input_data, state):
    return {
        "normalized": True,
        "original_path": input_data.get("path"),
        "method": input_data.get("method", "GET").upper(),
    }


@enrich(capability="enrich.metadata")
def add_metadata(input_data, state):
    return {
        "request_id": f"req_{id(input_data)}",
        "processed_at": "2024-01-15T10:30:00Z",
        "version": "1.0.0",
    }


@validate(capability="validate.request")
def validate_request(input_data, state):
    errors = []
    if not input_data.get("path"):
        errors.append("path is required")
    if input_data.get("method") not in ["GET", "POST", "PUT", "DELETE"]:
        errors.append("invalid method")
    return errors


@node(capability="process.request")
def process_request(envelope):
    envelope.state["response_body"] = {
        "message": "Hello, World!",
        "user": envelope.state.get("user"),
        "metadata": envelope.state.get("metadata"),
    }
    envelope.set_next("response.build")
    return envelope


@node(capability="response.build")
def build_response(envelope):
    envelope.state["http_response"] = {
        "status_code": 200,
        "body": envelope.state.get("response_body"),
        "headers": {"X-Content-Type-Options": "nosniff"},
    }
    envelope.set_next(None)
    return envelope


pp.register_node(validate_api_key())
pp.register_node(normalize_request())
pp.register_node(add_metadata())
pp.register_node(validate_request())
pp.register_node(process_request())
pp.register_node(build_response())


if __name__ == "__main__":
    result = pp.execute(
        {
            "path": "/api/hello",
            "method": "GET",
            "headers": {"X-API-Key": "secret_key_123"},
        },
        start_capability="auth.validate",
    )

    print(f"Success: {result.success}")
    print(f"Response: {result.envelope.state.get('http_response')}")
    print(f"History: {[h.capability for h in result.envelope.history]}")
