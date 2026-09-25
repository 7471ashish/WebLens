"""
Unit tests for sliding window rate limiter.
"""

import sys
import os
import time

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from rate_limiter import SlidingWindowRateLimiter


def test_rate_limiter_allows_under_limit():
    limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
    client_id = "test-client-1"

    r1 = limiter.check(client_id)
    assert r1.allowed is True
    assert r1.remaining == 2

    r2 = limiter.check(client_id)
    assert r2.allowed is True
    assert r2.remaining == 1

    r3 = limiter.check(client_id)
    assert r3.allowed is True
    assert r3.remaining == 0


def test_rate_limiter_blocks_over_limit():
    limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
    client_id = "test-client-2"

    limiter.check(client_id)
    limiter.check(client_id)
    blocked = limiter.check(client_id)

    assert blocked.allowed is False
    assert blocked.remaining == 0
    assert blocked.retry_after_seconds > 0
