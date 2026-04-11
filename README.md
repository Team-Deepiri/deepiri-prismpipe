# Deepiri PrismPipe — Capability-Routed API Pipeline

**Transform ordinary API requests into persistent computational organisms.**

---

## The Problem with Modern API Systems

Every system today treats requests as temporary packets:

```
HTTP    → request exists for milliseconds, then disappears
gRPC    → same
REST    → same
GraphQL → same
```

Even "advanced" systems like Kafka, Temporal, or Beam treat requests as temporary executions. They're born, run, die.

**This is the boundary no one has broken.**

---

## Our Idea: Organic Pipe

What if **requests were not packets**?

What if they were **persistent computational organisms**?

### Traditional API
```
Client → Request → Pipeline → Response → Dead
```

### Organic Pipe
```
Client → Spawn Organism → Evolves → Stores Knowledge → Future Requests Inherit
```

Requests become living computational artifacts that:
- Live forever in a registry
- Remember what they've computed
- Pass knowledge to future requests
- Form ancestry trees
- Self-optimize their execution
- Collaborate in swarms

---

## The 16 Features That Make It Revolutionary

### 1. Organisms (Requests That Live Forever)

Every request becomes an `Organism` - a persistent entity with:
- Unique ID
- Intent (what it wants to accomplish)
- Input (initial parameters)
- State (computed values)
- History (execution trace)
- Knowledge (derived insights with confidence scores)

```python
organism = engine.spawn_organism(
    intent="get_user_analytics",
    input_data={"user_id": 123},
    initial_capability="fetch_user"
)
```

### 2. Computation Deduplication

The killer feature. When 1000 requests run identical pipelines:

```
Normal system:   1000 × compute()
Organic Pipe:    1 compute() → shared by 1000 requests
```

Our demo shows **2.25x deduplication** - identical computations are computed once and reused.

```python
# First request computes and caches
org1 = spawn_organism("get_users", {...})
await execute(org1)  # Computes: fetch_users → filter → analytics

# Next 999 requests reuse the cached computation
org2 = spawn_organism("get_users", {...})
await execute(org2)  # Instant - reused from cache!
```

### 3. Knowledge Inheritance

Child organisms inherit computed knowledge from parents:

```python
parent = spawn_organism("analytics", {...})
await execute(parent)
parent.ingest_knowledge("total_users", 1500, confidence=1.0)

# Child automatically inherits
child = parent.spawn_child(patch_input={"filter": "active"})
# child now has knowledge about total_users with 0.9 confidence
```

### 4. Intent-Based APIs (Kill The Endpoints)

No more hardcoded endpoints. Send intent, system figures out the pipeline:

```python
# Register capabilities with keywords
planner.register_capability("fetch_users", "Get users from DB", ["user", "users", "get"])
planner.register_capability("compute_analytics", "Calculate metrics", ["analytics", "metrics", "count"])

# Natural language → pipeline
planner.plan("show me active user count")
# → ["fetch_users", "filter_active", "compute_analytics"]
```

### 5. Pipeline Evolution (Infrastructure That Learns)

The system tracks pipeline performance and automatically chooses optimal paths:

```python
# Run pipelines and track performance
pipeline_evolver.record_execution(pipeline_a, latency_ms=500, success=True)
pipeline_evolver.record_execution(pipeline_b, latency_ms=200, success=True)

# System learns B is faster
optimal = pipeline_evolver.evolve("analytics", [pipeline_a, pipeline_b])
# → pipeline_b
```

### 6. Time Splitting (Speculative Execution)

Fork execution into multiple branches, first successful wins:

```python
# Try slow, fast, and experimental pipelines simultaneously
splitter = TimeSplitter(router)
result = await splitter.execute_time_split(
    organism,
    branch_capabilities=["slow_analytics", "fast_analytics", "experimental_analytics"]
)
# Fastest successful branch wins
```

### 7. Swarm Computing

100 organisms analyzing a dataset, sharing intermediate state:

```python
swarm = swarm_coordinator.create_swarm(template, count=100)
# Workers partition the data, compute locally, then reduce
```

### 8. Computation Gravity

Instead of moving data to compute, move compute to where data lives:

```python
gravity.register_data_location("user_db_eu", "eu-cluster")
gravity.register_data_location("analytics_store", "us-cluster")

optimal = gravity.get_optimal_cluster(
    required_data=["user_db_eu", "analytics_store"],
    preferred_capabilities=["compute_analytics"]
)
# → us-cluster (where most data lives)
```

### 9. Request Genealogy

Debugging by tracing ancestry:

```python
grandparent = spawn_organism("fetch_data", ...)
parent = grandparent.spawn_child("analyze")
child = parent.spawn_child("visualize")

lineage = registry.get_lineage(child.id)
# [child, parent, grandparent]
```

### 10. Streaming Organisms

Real-time partial results:

```python
streamer = StreamingOrganism(organism)
streamer.subscribe(lambda data: print(f"Progress: {data}"))
streamer.emit_partial({"step": 1, "data": "users fetched"})
```

### 11. Organism Persistence

Hibernate and resume:

```python
hib_id = persistence.hibernate(organism)
# ... later ...
restored = persistence.wake(hib_id)
```

### 12. Event-Driven Organisms

React to events:

```python
event_org = EventDrivenOrganism(organism)
event_org.on("user_fetched", lambda e: notify_slack(e))
event_org.emit_event("user_fetched", {"user_id": 123})
```

### 13. Migratable Organisms

Move between execution nodes:

```python
migratable = MigratableOrganism(organism)
migratable.migrate_to("node-us-east-1")
migratable.migrate_to("node-eu-west-1")
```

### 14. Organism Watchers

Real-time monitoring:

```python
watcher.watch(organism_id, lambda org, event: log(event))
watcher.notify(organism, "completed")
```

### 15. Mutation Tracking

Git-like diffs for state:

```python
tracker.record_change("fetch_users", "users", None, [{...}])
tracker.record_change("filter_active", "users", [{...}], [{...}])
# Full timeline of every state change
```

### 16. Knowledge Graph

Find similar organisms:

```python
similar = registry.find_similar(organism, max_results=10)
# Returns organisms with similar intent/state
```

---

## Architecture

```
                    ┌─────────────────────────────────────┐
                    │             PrismEngine             │
                    └─────────────────────────────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        │                             │                             │
        ▼                             ▼                             ▼
┌───────────────┐          ┌─────────────────┐          ┌─────────────────┐
│  Organism     │          │ ComputationGraph│          │  IntentPlanner  │
│  Registry     │◄─────────│ (Deduplication) │─────────►│ (NLP → Pipeline)│
└───────────────┘          └─────────────────┘          └─────────────────┘
        │                             │                             │
        ▼                             ▼                             ▼
┌───────────────┐          ┌─────────────────┐          ┌─────────────────┐
│  Genealogy    │          │ PipelineEvolver │          │  TimeSplitter   │
│  Trees        │          │ (Auto-Optimize) │          │ (Speculative)   │
└───────────────┘          └─────────────────┘          └─────────────────┘
        │                             │                             │
        ▼                             ▼                             ▼
┌───────────────┐          ┌─────────────────┐          ┌─────────────────┐
│  Knowledge    │          │ SwarmCoordinator│          │  GravityEngine  │
│  Inheritance  │          │ (MapReduce)     │          │ (Data Locality) │
└───────────────┘          └─────────────────┘          └─────────────────┘
```

---

## Quick Start

```bash
git clone git@github.com:Team-Deepiri/deepiri-prismpipe.git
cd deepiri-prismpipe
uv sync --extra server

# Run the current proof-of-concept API server
uv run uvicorn server:app --reload --port 5011

# Run the test suite
uv run pytest
```

### Current Proof Of Concept

The repository currently includes a FastAPI proof of concept in `server.py` that demonstrates:

- a capability-routed request pipeline
- request memory, snapshots, diffs, and semantic cache inspection endpoints
- demo routes for users, models, analytics, streaming, and intent routing

Once the server is running, you can try:

```bash
curl http://127.0.0.1:5011/health
curl http://127.0.0.1:5011/engine/capabilities
curl http://127.0.0.1:5011/engine/memory
curl http://127.0.0.1:5011/engine/cache

# Authenticated demo routes
curl -H "Authorization: Bearer test" http://127.0.0.1:5011/users
curl -H "Authorization: Bearer test" http://127.0.0.1:5011/models
curl -H "Authorization: Bearer test" http://127.0.0.1:5011/analytics
```

---

## Core Example

```python
from prismpipe import PrismEngine, Organism, Intent
from prismpipe.core import Node, NodeResult

# Define capability nodes
class FetchUsers(Node):
    capability = "fetch_users"
    def process(self, envelope):
        envelope.state["users"] = [{"id": 1, "name": "Alice"}]
        envelope.set_next("compute_analytics")
        return NodeResult(envelope)

class ComputeAnalytics(Node):
    capability = "compute_analytics"
    def process(self, envelope):
        users = envelope.state.get("users", [])
        envelope.state["analytics"] = {"count": len(users)}
        envelope.set_next(None)
        return NodeResult(envelope)

# Create engine and register nodes
engine = PrismEngine()
engine.register_node(FetchUsers())
engine.register_node(ComputeAnalytics())

# Spawn an organism - this request LIVES forever
organism = engine.spawn_organism(
    intent=Intent.AI_TASK,
    input_data={"query": "user analytics"},
    initial_capability="fetch_users"
)

# Execute - automatically deduplicates future identical requests
result = await engine.execute_organism(organism)

# Check knowledge inheritance
child = organism.spawn_child(patch_input={"filter": "active"})
# Child inherits parent's computed knowledge
```

---

 **Our goal is basically just to optimize the lifecycle of requests.**


---

## Documentation

- [Architecture](./docs/architecture.md)
- [Protocol Specification](./docs/protocol.md)
- [Production Plan](./docs/PRODUCTION_PLAN.md)

---

## License
Apache 2.0
