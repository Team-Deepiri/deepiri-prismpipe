"""PrismPipe metrics."""

from functools import wraps
from typing import Callable

from prometheus_client import Counter, Histogram, Gauge, CollectorRegistry

# Default registry
default_registry = CollectorRegistry()

# Request metrics
requests_total = Counter(
    'prismpipe_requests_total',
    'Total number of requests',
    ['status', 'intent'],
    registry=default_registry
)

requests_in_flight = Gauge(
    'prismpipe_requests_in_flight',
    'Number of requests currently being processed',
    registry=default_registry
)

# Node metrics
node_execution_total = Counter(
    'prismpipe_node_execution_total',
    'Total node executions',
    ['capability', 'status'],
    registry=default_registry
)

node_execution_duration = Histogram(
    'prismpipe_node_execution_duration_seconds',
    'Node execution duration',
    ['capability'],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=default_registry
)

# Cache metrics
cache_hits = Counter(
    'prismpipe_cache_hits_total',
    'Total cache hits',
    ['cache_name'],
    registry=default_registry
)

cache_misses = Counter(
    'prismpipe_cache_misses_total',
    'Total cache misses',
    ['cache_name'],
    registry=default_registry
)

# Pipeline metrics
pipeline_execution_total = Counter(
    'prismpipe_pipeline_execution_total',
    'Total pipeline executions',
    ['status'],
    registry=default_registry
)

pipeline_execution_duration = Histogram(
    'prismpipe_pipeline_execution_duration_seconds',
    'Pipeline execution duration',
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
    registry=default_registry
)

# Error metrics
errors_total = Counter(
    'prismpipe_errors_total',
    'Total errors',
    ['error_type', 'capability'],
    registry=default_registry
)


def track_node_execution(capability: str):
    """Decorator to track node execution metrics."""
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            status = "success"
            try:
                result = func(*args, **kwargs)
                return result
            except Exception:
                status = "error"
                raise
            finally:
                node_execution_total.labels(capability=capability, status=status).inc()
        return wrapper
    return decorator


def track_request(status: str, intent: str):
    """Track request metrics."""
    requests_total.labels(status=status, intent=intent).inc()


def track_cache_hit(cache_name: str):
    """Track cache hit."""
    cache_hits.labels(cache_name=cache_name).inc()


def track_cache_miss(cache_name: str):
    """Track cache miss."""
    cache_misses.labels(cache_name=cache_name).inc()


def track_error(error_type: str, capability: str | None = None):
    """Track error."""
    errors_total.labels(error_type=error_type, capability=capability or "unknown").inc()
