"""Example: Connecting PrismPipe to Deepiri Services."""

import asyncio

from prismpipe import (
    PrismPipe,
    Pipeline,
    create_envelope,
    ServiceRegistry,
    node,
    PermissionNode,
    RateLimitNode,
    ValidationSchemaNode,
    Schema,
    FieldSchema,
)


# Example: Connect to your existing Deepiri services
def create_deepiri_pipeline():
    """Create a pipeline connected to Deepiri services."""
    
    pp = PrismPipe()

    # Connect to auth service
    auth_service = pp.router  # Just using local for now
    
    # Auth node - validates tokens against your auth service
    @node(capability="auth.validate", description="Validate JWT token")
    def validate_auth(envelope):
        token = envelope.input.get("headers", {}).get("Authorization", "").replace("Bearer ", "")
        
        if not token:
            envelope.terminate("No token provided")
            return envelope
        
        # In production, call your auth service
        # For now, decode/validate the token
        envelope.state["user"] = {
            "id": "user_123",
            "permissions": ["read", "write"],
            "tier": "premium",
        }
        envelope.state["authenticated"] = True
        envelope.set_next("validate.permissions")
        
        return envelope

    # Permission validation
    @node(capability="validate.permissions")
    def check_permissions(envelope):
        required = envelope.input.get("required_permissions", ["read"])
        user_perms = envelope.state.get("user", {}).get("permissions", [])
        
        if not all(p in user_perms for p in required):
            envelope.terminate("Insufficient permissions")
            return envelope
        
        envelope.set_next("process.request")
        return envelope

    # Main processing node
    @node(capability="process.request")
    def process_request(envelope):
        path = envelope.input.get("path", "")
        method = envelope.input.get("method", "GET")
        
        # Route based on path
        if path.startswith("/users"):
            envelope.set_next("users.fetch")
        elif path.startswith("/models"):
            envelope.set_next("models.list")
        else:
            envelope.set_next("response.build")
        
        return envelope

    # Users service
    @node(capability="users.fetch")
    def fetch_users(envelope):
        # In production, call your user service
        envelope.state["users"] = [
            {"id": "1", "name": "Alice", "email": "alice@deepiri.ai"},
            {"id": "2", "name": "Bob", "email": "bob@deepiri.ai"},
        ]
        envelope.set_next("response.build")
        return envelope

    # Models service (connect to modelkit)
    @node(capability="models.list")
    def list_models(envelope):
        # In production, call your modelkit service
        envelope.state["models"] = [
            {"id": "llama-3", "name": "Llama 3", "provider": "meta"},
            {"id": "gpt-4", "name": "GPT-4", "provider": "openai"},
        ]
        envelope.set_next("response.build")
        return envelope

    # Response builder
    @node(capability="response.build")
    def build_response(envelope):
        body = envelope.state.get("users") or envelope.state.get("models") or {}
        
        envelope.state["http_response"] = {
            "status_code": 200,
            "body": {"data": body},
            "headers": {
                "X-Content-Type-Options": "nosniff",
                "X-Request-ID": envelope.id,
            },
        }
        envelope.set_next(None)
        return envelope

    # Register all nodes
    pp.register_node(validate_auth())
    pp.register_node(check_permissions())
    pp.register_node(process_request())
    pp.register_node(fetch_users())
    pp.register_node(list_models())
    pp.register_node(build_response())

    return pp


# Example: Using the service registry to connect to external services
async def example_with_service_connector():
    """Example showing how to use the service connector."""
    
    registry = ServiceRegistry()
    
    # Register your Deepiri services
    registry.register(
        "auth",
        base_url="http://localhost:8001",
        timeout=10.0,
    )
    
    registry.register(
        "modelkit",
        base_url="http://localhost:8003",
        api_key="your-api-key",
    )
    
    # Create nodes that call those services
    auth_node = registry.node("auth", capability="service.auth")
    model_node = registry.node("modelkit", capability="service.models")
    
    # Build a pipeline with them
    pipeline = Pipeline()
    pipeline.register_node(auth_node)
    pipeline.register_node(model_node)
    
    # Use the connector
    connector = registry.get_connector("auth")
    if connector:
        envelope = create_envelope(
            input={"method": "GET", "path": "/verify"},
            next="service.auth"
        )
        # Note: This would need async execution
    
    await registry.close_all()


# Example: Using schema validation
def example_with_validation():
    """Example showing schema validation."""
    
    schema = Schema(
        input_schema={
            "path": FieldSchema(type=str, required=True),
            "method": FieldSchema(
                type=str, 
                required=True,
                validator=lambda m: m in ["GET", "POST", "PUT", "DELETE"]
            ),
        },
        state_schema={
            "user": FieldSchema(type=dict, required=False),
        },
    )
    
    validator = ValidationSchemaNode(schema)
    
    pp = PrismPipe()
    pp.pipeline.register_node(validator)
    
    return pp


# Example: Using rate limiting
def example_with_rate_limit():
    """Example showing rate limiting."""
    
    rate_limiter = RateLimitNode(
        max_requests=100,
        window_seconds=60,
    )
    
    pp = PrismPipe()
    pp.pipeline.register_node(rate_limiter)
    
    return pp


if __name__ == "__main__":
    # Create and run the pipeline
    pp = create_deepiri_pipeline()
    
    # Simulate a request
    result = pp.execute(
        {
            "path": "/users",
            "method": "GET",
            "headers": {"Authorization": "Bearer test_token"},
            "required_permissions": ["read"],
        },
        start_capability="auth.validate"
    )
    
    print(f"Success: {result.success}")
    print(f"Response: {result.envelope.state.get('http_response')}")
    print(f"History: {[h.capability for h in result.envelope.history]}")
