"""Core Node - Base class for all capability nodes."""

from __future__ import annotations

import atexit
import asyncio
import copy
import inspect
import queue
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from prismpipe.core.envelope import RequestEnvelope

T = TypeVar("T")


class NodeExecutionCapacityError(RuntimeError):
    """Raised when bounded synchronous node execution capacity is exhausted."""


class _BoundedDaemonExecutor:
    """Small bounded executor whose abandoned workers cannot block process exit.

    Python cannot safely preempt arbitrary synchronous code. Workers are therefore
    daemon threads, work and queue sizes are fixed, and shutdown abandons blocked
    calls without waiting for them.
    """

    def __init__(self, max_workers: int = 32, max_pending: int = 32) -> None:
        if max_workers <= 0 or max_pending <= 0:
            raise ValueError("max_workers and max_pending must be positive")
        self.max_workers = max_workers
        self.max_pending = max_pending
        self._work: queue.Queue[
            tuple[Future[Any], Callable[..., Any], tuple[Any, ...]]
        ] = queue.Queue(maxsize=max_pending)
        self._shutdown = threading.Event()
        self._submit_lock = threading.Lock()
        self._threads = [
            threading.Thread(
                target=self._worker,
                name=f"prismpipe-node-{index}",
                daemon=True,
            )
            for index in range(max_workers)
        ]
        for thread in self._threads:
            thread.start()

    def submit(
        self,
        function: Callable[..., Any],
        *args: Any,
    ) -> Future[Any]:
        future: Future[Any] = Future()
        with self._submit_lock:
            if self._shutdown.is_set():
                raise RuntimeError("Node execution resources are shut down")
            try:
                self._work.put_nowait((future, function, args))
            except queue.Full as error:
                raise NodeExecutionCapacityError(
                    "Synchronous node execution capacity exhausted"
                ) from error
        return future

    def shutdown(self, *, wait: bool = False, timeout: float = 0.0) -> None:
        with self._submit_lock:
            self._shutdown.set()
            while True:
                try:
                    future, _, _ = self._work.get_nowait()
                except queue.Empty:
                    break
                future.cancel()
                self._work.task_done()

        if wait:
            deadline = time.monotonic() + max(timeout, 0.0)
            for thread in self._threads:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                thread.join(remaining)

    def _worker(self) -> None:
        while not self._shutdown.is_set():
            try:
                future, function, args = self._work.get(timeout=0.1)
            except queue.Empty:
                continue

            try:
                if self._shutdown.is_set():
                    future.cancel()
                elif future.set_running_or_notify_cancel():
                    try:
                        future.set_result(function(*args))
                    except BaseException as error:
                        future.set_exception(error)
            finally:
                self._work.task_done()


_NODE_EXECUTOR = _BoundedDaemonExecutor(max_workers=32, max_pending=32)


def shutdown_node_execution_resources() -> None:
    """Stop accepting work and abandon blocked daemon workers without waiting."""
    _NODE_EXECUTOR.shutdown(wait=False)


atexit.register(shutdown_node_execution_resources)


def commit_envelope(
    target: RequestEnvelope[T],
    source: RequestEnvelope[T],
) -> RequestEnvelope[T]:
    """Deep-copy every declared envelope field into an existing envelope."""
    if target is source:
        return target
    committed_fields = {
        field_name: copy.deepcopy(getattr(source, field_name))
        for field_name in RequestEnvelope.model_fields
    }
    for field_name, value in committed_fields.items():
        setattr(target, field_name, value)
    return target


@dataclass
class NodeResult(Generic[T]):
    envelope: RequestEnvelope[T]
    success: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Node(ABC, Generic[T]):
    """Base class for all capability nodes."""

    capability: str = ""
    version: str = "1.0.0"
    description: str = ""
    timeout_ms: int | None = None

    def __init__(self) -> None:
        self._id = f"{self.__class__.__name__}_{id(self)}"

    @property
    def id(self) -> str:
        return self._id

    @property
    def node_type(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    def process(self, envelope: RequestEnvelope[T]) -> NodeResult[T]:
        ...

    def execute(self, envelope: RequestEnvelope[T]) -> NodeResult[T]:
        """Execute from synchronous callers while enforcing the node timeout."""
        timeout_seconds = self.timeout_ms / 1000 if self.timeout_ms is not None else None
        return self.execute_with_timeout(envelope, timeout_seconds)

    def execute_with_timeout(
        self,
        envelope: RequestEnvelope[T],
        timeout_seconds: float | None,
    ) -> NodeResult[T]:
        """Execute synchronously with an optional caller-provided deadline."""
        effective_timeout = self._effective_timeout(timeout_seconds)
        if effective_timeout is None:
            return self._commit_completed_result(
                envelope,
                self._execute_sync(envelope),
            )
        if effective_timeout <= 0:
            return self._timeout_result(envelope, effective_timeout)

        working_envelope = envelope.model_copy(deep=True)
        try:
            future = _NODE_EXECUTOR.submit(self._execute_sync, working_envelope)
        except NodeExecutionCapacityError as error:
            return self._capacity_result(envelope, error)

        try:
            result = future.result(timeout=effective_timeout)
        except FutureTimeoutError:
            future.cancel()
            return self._timeout_result(envelope, effective_timeout)
        return self._commit_completed_result(envelope, result)

    async def execute_async(
        self,
        envelope: RequestEnvelope[T],
        timeout_seconds: float | None = None,
    ) -> NodeResult[T]:
        """Execute without blocking the event loop and preserve caller cancellation."""
        effective_timeout = self._effective_timeout(timeout_seconds)
        if effective_timeout is not None and effective_timeout <= 0:
            return self._timeout_result(envelope, effective_timeout)

        working_envelope = envelope.model_copy(deep=True)
        is_async_operation = inspect.iscoroutinefunction(self.process)
        if is_async_operation:
            operation: asyncio.Future[NodeResult[T]] = asyncio.create_task(
                self._execute_async_process(working_envelope)
            )
        else:
            try:
                concurrent_future = _NODE_EXECUTOR.submit(
                    self._execute_sync,
                    working_envelope,
                )
            except NodeExecutionCapacityError as error:
                return self._capacity_result(envelope, error)
            operation = asyncio.wrap_future(concurrent_future)

        try:
            protected = asyncio.shield(operation)
            if effective_timeout is None:
                result = await protected
            else:
                result = await asyncio.wait_for(
                    protected,
                    timeout=effective_timeout,
                )
            return self._commit_completed_result(envelope, result)
        except asyncio.TimeoutError:
            if is_async_operation:
                await self._cancel_async_operation(operation)
            else:
                self._abandon_operation(operation)
            return self._timeout_result(envelope, effective_timeout)
        except asyncio.CancelledError:
            if is_async_operation:
                await self._cancel_async_operation(operation)
            else:
                self._abandon_operation(operation)
            raise

    async def _execute_async_process(
        self,
        envelope: RequestEnvelope[T],
    ) -> NodeResult[T]:
        start_time = time.perf_counter()
        try:
            result = await self.process(envelope)
            return self._finalize_result(result, start_time)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return self._failure_result(envelope, error, start_time)

    def _execute_sync(self, envelope: RequestEnvelope[T]) -> NodeResult[T]:
        start_time = time.perf_counter()
        try:
            result = self.process(envelope)
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            return self._finalize_result(result, start_time)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return self._failure_result(envelope, error, start_time)

    def _finalize_result(
        self,
        result: NodeResult[T],
        start_time: float,
    ) -> NodeResult[T]:
        duration_ms = (time.perf_counter() - start_time) * 1000
        result.envelope.record(
            node_id=self.id,
            node_type=self.node_type,
            capability=self.capability,
            action=self._get_action_description(result),
            duration_ms=duration_ms,
        )
        if not result.success:
            result.envelope.error = result.error
        return result

    def _commit_completed_result(
        self,
        envelope: RequestEnvelope[T],
        result: NodeResult[T],
    ) -> NodeResult[T]:
        result.envelope = commit_envelope(envelope, result.envelope)
        return result

    @staticmethod
    async def _cancel_async_operation(operation: asyncio.Future[Any]) -> None:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)

    @staticmethod
    def _abandon_operation(operation: asyncio.Future[Any]) -> None:
        operation.cancel()

        def consume_result(future: asyncio.Future[Any]) -> None:
            try:
                future.exception()
            except (asyncio.CancelledError, Exception):
                pass

        operation.add_done_callback(consume_result)

    def _capacity_result(
        self,
        envelope: RequestEnvelope[T],
        error: NodeExecutionCapacityError,
    ) -> NodeResult[T]:
        envelope.record(
            node_id=self.id,
            node_type=self.node_type,
            capability=self.capability,
            action="capacity_exhausted",
            duration_ms=0.0,
        )
        envelope.terminate(str(error))
        return NodeResult(envelope=envelope, success=False, error=str(error))

    def _failure_result(
        self,
        envelope: RequestEnvelope[T],
        error: Exception,
        start_time: float,
    ) -> NodeResult[T]:
        duration_ms = (time.perf_counter() - start_time) * 1000
        envelope.record(
            node_id=self.id,
            node_type=self.node_type,
            capability=self.capability,
            action=f"error: {type(error).__name__}",
            duration_ms=duration_ms,
        )
        envelope.terminate(str(error))
        return NodeResult(envelope=envelope, success=False, error=str(error))

    def _timeout_result(
        self,
        envelope: RequestEnvelope[T],
        timeout_seconds: float,
    ) -> NodeResult[T]:
        message = f"Node '{self.capability}' timed out after {timeout_seconds:g}s"
        envelope.record(
            node_id=self.id,
            node_type=self.node_type,
            capability=self.capability,
            action="timeout",
            duration_ms=max(timeout_seconds, 0) * 1000,
        )
        envelope.terminate(message)
        return NodeResult(envelope=envelope, success=False, error=message)

    def _effective_timeout(self, timeout_seconds: float | None) -> float | None:
        node_timeout = self.timeout_ms / 1000 if self.timeout_ms is not None else None
        candidates = [value for value in (timeout_seconds, node_timeout) if value is not None]
        return min(candidates) if candidates else None

    def _get_action_description(self, result: NodeResult) -> str:
        if not result.success:
            return f"failed: {result.error}"
        return result.metadata.get("action", "completed")

    def can_handle(self, capability: str) -> bool:
        return self.capability == capability


class TransformNode(Node[T]):
    """Base class for nodes that transform input data."""

    def process(self, envelope: RequestEnvelope[T]) -> NodeResult[T]:
        transformed = self.transform(envelope.input, envelope.state)
        envelope.state["transformed"] = transformed
        return NodeResult(envelope=envelope)

    @abstractmethod
    def transform(self, input_data: T, state: dict[str, Any]) -> Any:
        ...


class EnrichmentNode(Node[T]):
    """Base class for nodes that enrich state."""

    def process(self, envelope: RequestEnvelope[T]) -> NodeResult[T]:
        enrichment = self.enrich(envelope.input, envelope.state)
        envelope.state.update(enrichment)
        return NodeResult(envelope=envelope)

    @abstractmethod
    def enrich(self, input_data: T, state: dict[str, Any]) -> dict[str, Any]:
        ...


class ValidationNode(Node[T]):
    """Base class for nodes that validate requests."""

    def process(self, envelope: RequestEnvelope[T]) -> NodeResult[T]:
        errors = self.validate(envelope.input, envelope.state)
        if errors:
            envelope.terminate(f"Validation failed: {', '.join(errors)}")
            return NodeResult(envelope=envelope, success=False, error=", ".join(errors))
        envelope.state["validation_passed"] = True
        return NodeResult(envelope=envelope)

    @abstractmethod
    def validate(self, input_data: T, state: dict[str, Any]) -> list[str]:
        ...
