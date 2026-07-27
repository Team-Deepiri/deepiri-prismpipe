"""Pipeline Engine - Executes envelopes through capability nodes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from prismpipe.core.envelope import Intent, RequestEnvelope
from prismpipe.core.node import Node, NodeResult
from prismpipe.core.router import CapabilityRouter, NodeNotFoundError

logger = logging.getLogger(__name__)


@dataclass
class PipelineConfig:
    max_iterations: int = 100
    timeout_seconds: float | None = None
    continue_on_error: bool = False
    enable_history: bool = True


@dataclass
class PipelineMetrics:
    started_at: datetime
    ended_at: datetime | None = None
    iterations: int = 0
    nodes_executed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_duration_ms: float | None = None


@dataclass
class PipelineResult:
    envelope: RequestEnvelope
    success: bool
    error: str | None = None
    metrics: PipelineMetrics | None = None
    iterations: int = 0


class Pipeline:
    """The core pipeline engine that executes envelopes through capability nodes."""

    def __init__(
        self,
        router: CapabilityRouter | None = None,
        config: PipelineConfig | None = None,
    ) -> None:
        self._router = router or CapabilityRouter()
        self._config = config or PipelineConfig()
        self._hooks: dict[str, list[Callable]] = {
            "before_node": [],
            "after_node": [],
            "before_execute": [],
            "after_execute": [],
            "on_error": [],
        }

    @property
    def router(self) -> CapabilityRouter:
        return self._router

    def register_node(self, node: Node) -> "Pipeline":
        if not node.capability:
            raise ValueError(f"Node {node.__class__.__name__} has no capability defined")
        self._router.register(node.capability, node)
        return self

    def register_nodes(self, nodes: list[Node]) -> "Pipeline":
        for node in nodes:
            self.register_node(node)
        return self

    def hook(self, event: str, callback: Callable) -> "Pipeline":
        if event not in self._hooks:
            raise ValueError(f"Unknown hook event: {event}")
        self._hooks[event].append(callback)
        return self

    def execute(self, envelope: RequestEnvelope) -> PipelineResult:
        metrics = PipelineMetrics(started_at=datetime.now(timezone.utc))
        deadline = self._deadline()

        if envelope.next is None and self._config.enable_history:
            if envelope.history:
                last_cap = envelope.history[-1].capability
                envelope.next = last_cap

        self._run_hook("before_execute", envelope)

        try:
            while envelope.next and not envelope.terminated:
                if self._enforce_deadline(envelope, metrics, deadline):
                    break

                metrics.iterations += 1
                if metrics.iterations > self._config.max_iterations:
                    envelope.terminate(
                        f"Max iterations ({self._config.max_iterations}) exceeded"
                    )
                    break

                capability = envelope.get_capability()
                if not capability:
                    break

                try:
                    node = self._router.resolve(capability)
                except NodeNotFoundError as error:
                    envelope.terminate(str(error))
                    metrics.errors.append(str(error))
                    break

                if self._enforce_deadline(envelope, metrics, deadline):
                    break
                self._run_hook("before_node", envelope, node)
                if self._enforce_deadline(envelope, metrics, deadline):
                    break

                remaining_timeout = self._remaining_timeout(deadline)
                if remaining_timeout is not None and remaining_timeout <= 0:
                    self._enforce_deadline(envelope, metrics, deadline)
                    break

                result = node.execute_with_timeout(envelope, remaining_timeout)
                envelope = result.envelope
                metrics.nodes_executed.append(node.id)

                if self._enforce_deadline(envelope, metrics, deadline):
                    break
                self._run_hook("after_node", envelope, node, result)
                if self._enforce_deadline(envelope, metrics, deadline):
                    break

                if not result.success:
                    if self._config.continue_on_error:
                        metrics.errors.append(result.error or "Unknown error")
                    else:
                        envelope.terminate(result.error)
                        metrics.errors.append(result.error or "Unknown error")
                        break

        except Exception as error:
            logger.exception("Pipeline execution failed")
            envelope.terminate(str(error))
            metrics.errors.append(str(error))
            self._run_hook("on_error", envelope, error)

        finally:
            metrics.ended_at = datetime.now(timezone.utc)
            metrics.total_duration_ms = (
                metrics.ended_at - metrics.started_at
            ).total_seconds() * 1000
            self._run_hook("after_execute", envelope, metrics)
            self._enforce_deadline(envelope, metrics, deadline)
            metrics.ended_at = datetime.now(timezone.utc)
            metrics.total_duration_ms = (
                metrics.ended_at - metrics.started_at
            ).total_seconds() * 1000

        self._enforce_deadline(envelope, metrics, deadline)
        return PipelineResult(
            envelope=envelope,
            success=not envelope.terminated or envelope.error is None,
            error=envelope.error,
            metrics=metrics,
            iterations=metrics.iterations,
        )

    def _deadline(self) -> float | None:
        if self._config.timeout_seconds is None:
            return None
        return time.monotonic() + max(self._config.timeout_seconds, 0.0)

    @staticmethod
    def _remaining_timeout(deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return deadline - time.monotonic()

    def _enforce_deadline(
        self,
        envelope: RequestEnvelope,
        metrics: PipelineMetrics,
        deadline: float | None,
    ) -> bool:
        if deadline is None or time.monotonic() < deadline:
            return False
        message = f"Pipeline timed out after {self._config.timeout_seconds:g}s"
        envelope.terminate(message)
        if message not in metrics.errors:
            metrics.errors.append(message)
        return True

    def _run_hook(
        self,
        event: str,
        *args: Any,
    ) -> None:
        for callback in self._hooks.get(event, []):
            try:
                callback(*args)
            except Exception:
                logger.exception(f"Hook {event} failed")

    def __repr__(self) -> str:
        capabilities = self._router.list_capabilities()
        return f"Pipeline(capabilities={capabilities})"
