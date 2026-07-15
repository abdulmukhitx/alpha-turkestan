"""Thread-safe, in-process sliding-window rate limiting primitives.

The current deployment runs as a single FastAPI process, so a small in-memory
limiter is sufficient. A multi-worker or multi-host deployment should replace
this with a shared Redis-backed implementation at the reverse proxy or app
layer while keeping the same HTTP contract.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class SlidingWindowRateLimiter:
    """Track request timestamps per key and return quota information."""

    def __init__(self, clock: Callable[[], float] | None = None):
        self._clock = clock or time.monotonic
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def consume(self, key: str, *, limit: int, window_seconds: int) -> tuple[bool, int, int]:
        """Consume one request.

        Returns ``(allowed, remaining, retry_after_seconds)``. Rejected
        requests are not appended, so a client can recover as soon as the
        oldest accepted request leaves the window.
        """
        if limit < 1 or window_seconds < 1:
            raise ValueError("limit and window_seconds must be positive")

        now = self._clock()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()

            if len(events) >= limit:
                retry_after = max(1, math.ceil(events[0] + window_seconds - now))
                return False, 0, retry_after

            events.append(now)
            return True, max(0, limit - len(events)), 0

    def clear(self) -> None:
        """Clear all buckets. Intended for tests and controlled maintenance."""
        with self._lock:
            self._events.clear()
