# PrismPipe Architecture

## Overview

PrismPipe is a Capability-Routed API Pipeline framework that transforms ordinary API requests into mutable, capability-driven pipelines where requests become the carrier of computation.

## Core Concepts

### Request Envelope

The fundamental unit of computation. A request envelope contains:

```python
{
    "id": "req_123",           # Unique request ID
    "intent": "user.fetch",    # What the request wants
    "input": {...},            # Input data
    "state": {...},            # Mutable state accumulated during execution
    "history": [...],          # Execution history
    "metadata": {...},         # Request metadata
    "plan": [...],             # Execution plan (can be rewritten)
    "parent_id": "req_100",    # Parent request for lineage
    "next": "capability.name", # Next capability to execute
}
```

### Capability Router

Maps capability names to node implementations:

```python
router = CapabilityRouter()
router.register("auth.validate", auth_node)
router.register("data.fetch_users", user_service)
```

### Pipeline Engine

Executes envelopes through capability chains:

```python
pipeline = Pipeline(router=router)
result = await pipeline.execute(envelope)
```

## Component Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Client                               │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                      Gateway                                │
│  (HTTP Request → Envelope)                                 │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   Capability Router                          │
│  (Resolve capability → Node)                                │
└─────────────────────────┬───────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │  Node A  │    │  Node B  │    │  Node C  │
    │ (auth)   │    │ (data)   │    │ (format) │
    └────┬─────┘    └────┬─────┘    └────┬─────┘
         │               │               │
         └───────────────┼───────────────┘
                         ▼
              ┌──────────────────┐
              │   Response       │
              │ (Envelope State) │
              └──────────────────┘
```

## Advanced Features

### Engine Components

1. **ReplayEngine** - Time-travel debugging, fork requests
2. **DiffEngine** - Track state mutations per node
3. **CapabilityGraph** - Auto-discover execution paths
4. **RemoteExecutor** - Distributed node execution
5. **StreamManager** - Progressive/streaming responses
6. **ParallelExecutor** - Branch execution with merge
7. **CostOptimizer** - Fastest/cheapest/balanced routing
8. **SemanticCache** - Intent-based caching
9. **AncestryTree** - Request lineage tracking
10. **RequestMemory** - Persistent requests with knowledge inheritance

### Extension Points

1. **Custom Nodes** - Implement the Node interface
2. **Storage Backends** - Plug in different persistence layers
3. **Event Handlers** - Subscribe to system events
4. **Feature Flags** - A/B testing and gradual rollouts

## Data Flow

1. **Request Entry**: HTTP request → Gateway → Envelope
2. **Capability Resolution**: Router finds node for capability
3. **Node Execution**: Node processes envelope, modifies state
4. **Routing**: Envelope.next determines next capability
5. **Response**: Final envelope state → HTTP response

## Threading Model

- Async/await throughout
- Non-blocking node execution
- Connection pooling for remote nodes
- Backpressure for streams

## Extension Patterns

### Custom Node

```python
class MyNode(Node):
    capability = "my.custom"
    
    def process(self, envelope: RequestEnvelope) -> NodeResult:
        envelope.state["processed"] = True
        return NodeResult(envelope=envelope)
```

### Custom Storage

```python
class MyStorage(StorageBackend[T]):
    async def save(self, key: str, value: T): ...
    async def load(self, key: str) -> T | None: ...
```

### Custom Event Handler

```python
async def on_node_executed(event: Event):
    print(f"Node {event.data['capability']} executed")
    
event_bus.subscribe(EventType.NODE_EXECUTED, on_node_executed)
```
