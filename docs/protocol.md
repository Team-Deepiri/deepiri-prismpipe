# PrismPipe Protocol Specification

## Request Envelope Schema

### JSON Schema

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["id", "intent", "input", "state", "history"],
  "properties": {
    "id": {
      "type": "string",
      "description": "Unique request identifier"
    },
    "intent": {
      "type": "string",
      "description": "Request intent/capability to execute"
    },
    "input": {
      "type": "object",
      "description": "Input data for the request"
    },
    "state": {
      "type": "object",
      "description": "Mutable state accumulated during execution"
    },
    "history": {
      "type": "array",
      "items": {
        "$ref": "#/definitions/HistoryEntry"
      },
      "description": "Execution history"
    },
    "metadata": {
      "type": "object",
      "description": "Request metadata"
    },
    "plan": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "Execution plan (capability chain)"
    },
    "parent_id": {
      "type": "string",
      "description": "Parent request ID for lineage"
    },
    "next": {
      "type": ["string", "null"],
      "description": "Next capability to execute"
    },
    "terminated": {
      "type": "boolean",
      "description": "Whether request has terminated"
    },
    "termination_reason": {
      "type": ["string", "null"],
      "description": "Reason for termination"
    }
  }
}
```

### History Entry Schema

```json
{
  "definitions": {
    "HistoryEntry": {
      "type": "object",
      "required": ["node", "action", "timestamp"],
      "properties": {
        "node": {
          "type": "string",
          "description": "Capability/node name"
        },
        "action": {
          "type": "string",
          "description": "Action performed"
        },
        "timestamp": {
          "type": "string",
          "format": "date-time"
        },
        "duration_ms": {
          "type": "number"
        },
        "input": {
          "type": "object"
        },
        "output": {
          "type": "object"
        },
        "error": {
          "type": ["string", "null"]
        }
      }
    }
  }
}
```

## Node Contract

### Interface

```python
class Node(ABC):
    capability: str = ""
    version: str = "1.0.0"
    
    @abstractmethod
    def process(self, envelope: RequestEnvelope) -> NodeResult:
        """Process the envelope and return result."""
        pass
```

### NodeResult

```python
@dataclass
class NodeResult:
    envelope: RequestEnvelope
    success: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

## Capability Registry Protocol

### Registration

```http
POST /engine/capabilities
Content-Type: application/json

{
  "capability": "auth.validate",
  "node": {
    "class": "my.module:AuthNode",
    "config": {}
  },
  "metadata": {
    "version": "1.0.0",
    "description": "Validates authentication tokens"
  }
}
```

### Discovery

```http
GET /engine/capabilities?intent=user.fetch
```

Response:
```json
{
  "capabilities": [
    {
      "name": "auth.validate",
      "description": "Validates authentication tokens"
    },
    {
      "name": "data.fetch_users",
      "description": "Fetches user data"
    }
  ]
}
```

## Wire Format for Remote Execution

### Envelope Transfer

```http
POST /execute
Content-Type: application/json

{
  "envelope": { ... },
  "capability": "data.fetch_users",
  "timeout": 30000
}
```

Response:
```json
{
  "envelope": { ... },
  "node_result": {
    "success": true,
    "metadata": {
      "duration_ms": 12
    }
  }
}
```

## Error Response Format

```json
{
  "error": "NODE_NOT_FOUND",
  "message": "Capability 'auth.validate' not found",
  "details": {
    "capability": "auth.validate",
    "request_id": "req_123"
  }
}
```

### Error Codes

| Code | Description |
|------|-------------|
| `NODE_NOT_FOUND` | Capability not registered |
| `NODE_EXECUTION_ERROR` | Node failed during execution |
| `ACCESS_DENIED` | Tenant lacks permission |
| `REQUEST_TIMEOUT` | Request exceeded timeout |
| `CIRCUIT_OPEN` | Circuit breaker open |
| `RATE_LIMIT_EXCEEDED` | Rate limit hit |
| `VALIDATION_ERROR` | Request validation failed |
| `STORAGE_ERROR` | Storage operation failed |

## Event Protocol

### Event Types

- `request.started` - Request received
- `request.completed` - Request finished successfully
- `request.failed` - Request failed
- `node.executed` - Node executed successfully
- `node.failed` - Node execution failed
- `cache.hit` - Cache hit
- `cache.miss` - Cache miss
- `pipeline.started` - Pipeline started
- `pipeline.completed` - Pipeline completed

### Event Payload

```json
{
  "type": "node.executed",
  "timestamp": "2024-01-15T10:30:00Z",
  "data": {
    "capability": "auth.validate",
    "latency_ms": 12,
    "success": true,
    "request_id": "req_123"
  }
}
```
