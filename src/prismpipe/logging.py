"""PrismPipe structured logging."""

import logging
import sys
from typing import Any
from contextvars import ContextVar

import structlog

# Context variable for request ID
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)


class RedactSensitiveKeys:
    """Redact sensitive keys from log output."""

    def __init__(self, keys: list[str] | None = None):
        self.keys = keys or ["password", "token", "secret", "api_key", "authorization"]

    def __call__(self, logger, method_name, event_dict):
        def redact(obj):
            if isinstance(obj, dict):
                return {
                    k: "***REDACTED***" if k.lower() in self.keys else redact(v)
                    for k, v in obj.items()
                }
            elif isinstance(obj, list):
                return [redact(i) for i in obj]
            return obj

        for key in list(event_dict.keys()):
            if key.lower() in self.keys:
                event_dict[key] = "***REDACTED***"
            elif isinstance(event_dict[key], (dict, list)):
                event_dict[key] = redact(event_dict[key])

        return event_dict


class AddRequestContext:
    """Add request context to log output."""

    def __call__(self, logger, method_name, event_dict):
        request_id = request_id_var.get()
        tenant_id = tenant_id_var.get()

        if request_id:
            event_dict["request_id"] = request_id
        if tenant_id:
            event_dict["tenant_id"] = tenant_id

        return event_dict


def configure_logging(level: str = "INFO", format: str = "json", redact_keys: list[str] | None = None):
    """Configure structured logging."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper()),
    )

    processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        AddRequestContext(),
        RedactSensitiveKeys(keys=redact_keys),
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    if format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.BoundLogger:
    """Get a logger instance."""
    return structlog.get_logger(name)


def set_request_context(request_id: str | None = None, tenant_id: str | None = None):
    """Set request context for logging."""
    if request_id:
        request_id_var.set(request_id)
    if tenant_id:
        tenant_id_var.set(tenant_id)


def clear_request_context():
    """Clear request context."""
    request_id_var.set(None)
    tenant_id_var.set(None)
