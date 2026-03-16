# PrismPipe — Capability-Routed API Pipeline

<p align="center">
  <strong>Transform ordinary API requests into a mutable, capability-driven pipeline</strong>
</p>

<p align="center">
  <a href="#overview">Overview</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#examples">Examples</a> •
  <a href="#contributing">Contributing</a>
</p>

---

## Overview

PrismPipe transforms API requests into evolving **Request Envelopes** that flow through **Capability Nodes**. Instead of services calling each other, the request itself moves through the system and gets enriched along the way.

### Traditional API
```
Client → Service → Response
```

### PrismPipe
```
Client → Request Envelope → Capability Nodes → Enriched Response
```

The request becomes the unit of computation.

---

## Quick Start

```bash
# Clone the repository
git clone git@github.com:Team-Deepiri/deepiri-prismpipe.git
cd deepiri-prismpipe

# Install dependencies
pip install -e .
```

---

## Core Concepts

### Request Envelope

Every request becomes a structured packet:

```json
{
  "id": "req_123",
  "intent": "http_request",
  "input": {
    "endpoint": "/users",
    "method": "GET"
  },
  "state": {},
  "history": [],
  "next": null
}
```

### Nodes

Nodes process envelopes and can:
- Read the request
- Modify state
- Append history
- Forward to next node

---

## Architecture

```
prismpipe/
 ├ core/
 │   ├ envelope      # Request envelope model
 │   ├ router        # Capability routing
 │   ├ pipeline      # Pipeline engine
 │   └ capabilities  # Core capabilities
 │
 ├ nodes/
 │   ├ transform     # Data transformation
 │   ├ enrich        # Data enrichment
 │   └ validate      # Validation nodes
 │
 ├ examples/
 │   ├ http_api      # HTTP API example
 │   ├ ai_pipeline   # AI processing pipeline
 │   └ data_pipeline # Data processing pipeline
 │
 ├ sdk/
 │   ├ python        # Python SDK
 │   └ typescript    # TypeScript SDK
 │
 └ docs/
```

---

## Examples

### Basic Pipeline

```python
from prismpipe import Pipeline, Node, RequestEnvelope

# Define a simple node
class UserFetchNode(Node):
    capability = "data.fetch_users"
    
    def process(self, envelope: RequestEnvelope) -> RequestEnvelope:
        envelope.state["users"] = db.get_users()
        envelope.next = "response.format"
        return envelope

# Build and run pipeline
pipeline = Pipeline()
pipeline.register(UserFetchNode())
result = pipeline.execute(create_envelope())
```

---

## Documentation

- [Architecture](./docs/architecture.md)
- [Protocol Specification](./docs/protocol.md)
- [Roadmap](./docs/roadmap.md)

---

## Contributing

1. Fork the repository
2. Create a feature branch
3. Implement changes
4. Add tests
5. Submit PR

---

## License

MIT License
