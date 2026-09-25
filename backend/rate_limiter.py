"""
In-memory token bucket / sliding window rate limiter for WebLens backend.
Protects compute resources and prevents abuse by client or IP.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import NamedTuple


class RateLimitResult(NamedTuple):
    allowed: bool
    remaining: int
    retry_after_seconds: int


class SlidingWindowRateLimiter:
    """
    Sliding window rate limiter based on timestamps.
    Thread/asyncio safe for cooperative multitasking within a single worker process.
    """

    def __init__(self, max_requests: int = 10, window_seconds: int = 600):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # client_id -> list of request timestamps
        self._history: dict[str, list[float]] = defaultdict(list)

    def check(self, client_id: str) -> RateLimitResult:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        
        # Clean older requests
        timestamps = [ts for ts in self._history[client_id] if ts > cutoff]
        self._history[client_id] = timestamps

        if len(timestamps) >= self.max_requests:
            earliest_in_window = timestamps[0]
            retry_after = int(max(1.0, (earliest_in_window + self.window_seconds) - now))
            return RateLimitResult(
                allowed=False,
                remaining=0,
                retry_after_seconds=retry_after,
            )

        # Allow and record current request
        timestamps.append(now)
        remaining = self.max_requests - len(timestamps)
        return RateLimitResult(
            allowed=True,
            remaining=remaining,
            retry_after_seconds=0,
        )

    def prune(self) -> None:
        """Evict stale client entries to prevent memory growth."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        stale_clients = [
            cid for cid, ts_list in self._history.items()
            if not ts_list or ts_list[-1] <= cutoff
        ]
        for cid in stale_clients:
            self._history.pop(cid, None)
