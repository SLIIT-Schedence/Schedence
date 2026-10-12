"""In-process fixed-window rate limiter for administrative write endpoints.

It protects a single process. When the service runs with several workers or
replicas, enforce limits at the gateway (or move this to Redis) as well.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0, *, clock: Callable[[], float] = time.monotonic,
                 max_keys: int = 10_000) -> None:
        self.limit = limit
        self.window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._windows: dict[Hashable, tuple[float, int]] = {}

    def hit(self, key: Hashable) -> bool:
        """Record one request for ``key``; return False if it exceeds the limit."""
        now = self._clock()
        started, count = self._windows.get(key, (now, 0))
        if now - started >= self.window:
            started, count = now, 0
        if count >= self.limit:
            return False
        if len(self._windows) >= self._max_keys and key not in self._windows:
            self._evict(now)
        self._windows[key] = (started, count + 1)
        return True

    def _evict(self, now: float) -> None:
        expired = [k for k, (started, _) in self._windows.items() if now - started >= self.window]
        for k in expired or list(self._windows)[: max(1, len(self._windows) // 10)]:
            self._windows.pop(k, None)
