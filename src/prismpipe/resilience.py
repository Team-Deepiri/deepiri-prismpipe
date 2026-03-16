"""PrismPipe resilience patterns."""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from prismpipe.exceptions import CircuitOpenError, RateLimitError

T = TypeVar("T")


@dataclass
class CircuitState:
    """State for circuit breaker."""
    failures: int = 0
    last_failure_time: float = 0.0
    is_open: bool = False


class CircuitBreaker:
    """Circuit breaker for failing nodes."""

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout: float = 60.0,
        half_open_attempts: int = 1,
    ):
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.half_open_attempts = half_open_attempts
        self._states: dict[str, CircuitState] = defaultdict(CircuitState)
        self._half_open_successes: dict[str, int] = defaultdict(int)

    def _get_state(self, key: str) -> CircuitState:
        return self._states[key]

    def _is_open(self, key: str) -> bool:
        state = self._get_state(key)
        if not state.is_open:
            return False

        if time.time() - state.last_failure_time >= self.timeout:
            state.is_open = False
            state.failures = 0
            return False
        return True

    def record_success(self, key: str) -> None:
        """Record a successful call."""
        state = self._get_state(key)
        if state.is_open and self._half_open_successes.get(key, 0) >= self.half_open_attempts:
            state.is_open = False
            state.failures = 0
            self._half_open_successes[key] = 0
        else:
            state.failures = max(0, state.failures - 1)

    def record_failure(self, key: str) -> None:
        """Record a failed call."""
        state = self._get_state(key)
        state.failures += 1
        state.last_failure_time = time.time()

        if state.failures >= self.failure_threshold:
            state.is_open = True

    async def call(self, key: str, fn: Callable[..., T], *args, **kwargs) -> T:
        """Execute a function with circuit breaker protection."""
        if self._is_open(key):
            raise CircuitOpenError(key)

        try:
            if asyncio.iscoroutinefunction(fn):
                result = await fn(*args, **kwargs)
            else:
                result = fn(*args, **kwargs)
            self.record_success(key)
            return result
        except Exception as e:
            self.record_failure(key)
            raise


class RateLimiter:
    """Token bucket rate limiter."""

    def __init__(self, rate: float = 100.0, burst: int = 10):
        self.rate = rate
        self.burst = burst
        self._buckets: dict[str, tuple[float, float]] = {}

    def _get_tokens(self, key: str) -> tuple[float, float]:
        now = time.time()
        if key not in self._buckets:
            self._buckets[key] = (now, float(self.burst))
            return self._buckets[key]

        last_time, tokens = self._buckets[key]
        elapsed = now - last_time
        tokens = min(self.burst, tokens + elapsed * self.rate)
        self._buckets[key] = (now, tokens)
        return now, tokens

    async def acquire(self, key: str, tokens: float = 1.0) -> None:
        """Acquire tokens, waiting if necessary."""
        while True:
            now, tokens = self._get_tokens(key)
            if tokens >= tokens:
                self._buckets[key] = (now, tokens - tokens)
                return
            await asyncio.sleep(0.01)


async def with_retry(
    fn: Callable[..., T],
    max_attempts: int = 3,
    backoff: float = 1.0,
    exponential: bool = True,
    retry_on: tuple[type, ...] = (Exception,),
) -> T:
    """Execute a function with retry logic."""
    last_exception: Exception | None = None

    for attempt in range(max_attempts):
        try:
            if asyncio.iscoroutinefunction(fn):
                return await fn()
            else:
                return fn()
        except retry_on as e:
            last_exception = e
            if attempt < max_attempts - 1:
                delay = backoff * (2 ** attempt if exponential else 1)
                await asyncio.sleep(delay)
            continue

    raise last_exception  # type: ignore


class TimeoutManager:
    """Manage timeouts for requests."""

    def __init__(self, default_timeout: float = 30.0):
        self.default_timeout = default_timeout
        self._timeouts: dict[str, float] = {}

    def set_timeout(self, key: str, timeout: float) -> None:
        self._timeouts[key] = timeout

    def get_timeout(self, key: str) -> float:
        return self._timeouts.get(key, self.default_timeout)

    async def run_with_timeout(
        self,
        key: str,
        fn: Callable[..., T],
        *args,
        **kwargs
    ) -> T:
        """Run function with timeout."""
        timeout = self.get_timeout(key)
        try:
            if asyncio.iscoroutinefunction(fn):
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=timeout)
            else:
                return await asyncio.wait_for(
                    asyncio.to_thread(fn, *args, **kwargs),
                    timeout=timeout
                )
        except asyncio.TimeoutError:
            from prismpipe.exceptions import RequestTimeoutError
            raise RequestTimeoutError(key, timeout)
