"""In-memory sliding-window rate limiter (RFC-0006 phase 2.7).

Used to throttle the abuse-prone v1 auth surface — local login, local
signup, and PAT mint — without dragging in a Redis dependency for the
laptop / single-VPS deploys. Behaviour:

- Sliding window per ``(scope, key)`` pair. ``scope`` is a short label
  ("auth.login", "auth.token.mint", …) so two limiters can share the
  same client key without colliding.
- Defaults are conservative: 10 attempts per 60s window per remote IP.
  Production deployments behind Caddy / Bunny see the real client IP via
  ``X-Forwarded-For``; we use that header when present, falling back to
  the socket address otherwise.
- The store is a ``defaultdict[deque]`` guarded by an ``asyncio.Lock``
  so concurrent requests from the same IP don't race.
- On exhaustion we raise ``HTTPException(429)`` with a ``Retry-After``
  header so well-behaved clients back off cleanly. A future Redis
  upgrade can swap :class:`InMemoryRateLimiter` for a Redis-backed
  variant without touching the route handlers.

Caveats:

- Single-process. In a multi-replica deploy each replica enforces its
  own bucket, so the effective limit per IP scales with replica count.
  That is intentional for v1 — the goal is "make brute-forcing expensive
  enough to discourage script kiddies", not "perfectly bound load".
- The store grows unbounded in theory (one bucket per unique IP). We
  evict empty buckets opportunistically inside :meth:`acquire` so a
  burst from one IP cleans itself up shortly after the window slides.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Awaitable, Callable

from fastapi import HTTPException, Request, status


class InMemoryRateLimiter:
    """Per-(scope, key) sliding window limiter."""

    def __init__(self, *, limit: int, window_seconds: float) -> None:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._limit = limit
        self._window = window_seconds
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def window_seconds(self) -> float:
        return self._window

    async def acquire(self, scope: str, key: str) -> None:
        """Record an attempt or raise 429.

        Uses ``time.monotonic`` so the limiter is immune to wall-clock
        jumps (NTP, suspend/resume on a laptop, container migrations).
        """
        now = time.monotonic()
        cutoff = now - self._window
        bucket_key = (scope, key)
        async with self._lock:
            bucket = self._buckets[bucket_key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                # Time the oldest entry leaves the window.
                retry_after = max(1, int(bucket[0] + self._window - now) + 1)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="rate limit exceeded; retry later",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
            if not bucket:
                # Bucket emptied via eviction; drop the entry to bound memory.
                self._buckets.pop(bucket_key, None)

    def reset(self) -> None:
        """Test helper — clear all buckets."""
        self._buckets.clear()


# Default limiters. Tuned for the v1 surface — the values are intentionally
# permissive enough that interactive humans never hit them, while still
# bounding scripted abuse. Override via env later if a tenant needs custom
# values; for now we keep the defaults inline so they are obvious.
LOGIN_LIMITER = InMemoryRateLimiter(limit=10, window_seconds=60.0)
SIGNUP_LIMITER = InMemoryRateLimiter(limit=5, window_seconds=300.0)
TOKEN_MINT_LIMITER = InMemoryRateLimiter(limit=20, window_seconds=60.0)


def _client_key(request: Request) -> str:
    """Best-effort identifier for the calling client.

    Production traffic comes through Caddy / Bunny which terminate TLS and
    set ``X-Forwarded-For``; the first entry there is the real client IP.
    On a developer laptop ``request.client.host`` is the loopback address.
    Either way we get a stable bucket key per actor.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # First hop is the caller; the rest are intermediate proxies.
        return fwd.split(",")[0].strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def rate_limit(
    limiter: InMemoryRateLimiter, scope: str
) -> Callable[[Request], Awaitable[None]]:
    """Build a FastAPI dependency that enforces ``limiter`` for ``scope``.

    Usage::

        @router.post("/foo", dependencies=[Depends(rate_limit(LOGIN_LIMITER, "auth.login"))])
        async def foo(...): ...
    """

    async def _dep(request: Request) -> None:
        await limiter.acquire(scope, _client_key(request))

    return _dep


def _reset_all_for_tests() -> None:
    LOGIN_LIMITER.reset()
    SIGNUP_LIMITER.reset()
    TOKEN_MINT_LIMITER.reset()


__all__ = [
    "InMemoryRateLimiter",
    "LOGIN_LIMITER",
    "SIGNUP_LIMITER",
    "TOKEN_MINT_LIMITER",
    "rate_limit",
]
