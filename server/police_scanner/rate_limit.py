"""Per-IP token-bucket rate limiting (in-process, fits a single Pi).

For login we apply a tight 10/min/IP cap. For general API we apply a looser
60/sec/IP cap. The buckets are intentionally trivial — no Redis required.
"""

from __future__ import annotations

import time
from collections import deque

from fastapi import HTTPException, Request, status

from .security import client_ip


class _SlidingWindowBucket:
    __slots__ = ("events", "max_events", "window_seconds")

    def __init__(self, max_events: int, window_seconds: float) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self.events: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        dq = self.events.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self.max_events:
            return False
        dq.append(now)
        return True


_login_bucket = _SlidingWindowBucket(max_events=10, window_seconds=60)
_api_bucket = _SlidingWindowBucket(max_events=120, window_seconds=60)


async def login_limiter(request: Request) -> None:
    if not _login_bucket.allow(client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in a minute.",
        )


async def api_limiter(request: Request) -> None:
    if not _api_bucket.allow(client_ip(request)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded.",
        )
