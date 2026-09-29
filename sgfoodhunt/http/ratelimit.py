"""Per domain polite delay and per API requests-per-minute limiting."""

from __future__ import annotations

import asyncio
import random
import threading
import time
from collections import deque


class DomainRateLimiter:
    """Enforces a random polite delay between consecutive requests to the same host.

    Thread safe; the polite delay is chosen uniformly in [min_delay, max_delay].
    """

    def __init__(self, min_delay: float, max_delay: float, sleep=time.sleep) -> None:  # type: ignore[no-untyped-def]
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock_for(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def wait(self, host: str) -> float:
        """Block until the host may be contacted again. Returns the seconds slept."""
        with self._lock_for(host):
            delay = random.uniform(self.min_delay, self.max_delay)
            last = self._last.get(host)
            slept = 0.0
            if last is not None:
                remaining = last + delay - time.monotonic()
                if remaining > 0:
                    self._sleep(remaining)
                    slept = remaining
            self._last[host] = time.monotonic()
            return slept


class AsyncRpmLimiter:
    """Sliding window limiter: at most ``rpm`` calls per 60 seconds."""

    def __init__(self, rpm: int) -> None:
        self.rpm = max(1, rpm)
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            while self._calls and now - self._calls[0] > 60:
                self._calls.popleft()
            if len(self._calls) >= self.rpm:
                await asyncio.sleep(60 - (now - self._calls[0]) + 0.01)
            self._calls.append(time.monotonic())
