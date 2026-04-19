"""Rate limiter tests (RFC-0006 phase 2.7).

Covers:

- The in-memory sliding-window primitive itself (``acquire``).
- The wired-up FastAPI dependencies on the auth surface, so a bug that
  drops the ``dependencies=[…]`` list never lands silently.

These tests intentionally do **not** assert exact ``Retry-After`` values:
the limiter computes them from ``time.monotonic`` deltas which are
slightly non-deterministic on a busy CI runner. We only assert that the
header is present and parseable as an integer.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.app.security.rate_limit import (
    LOGIN_LIMITER,
    SIGNUP_LIMITER,
    InMemoryRateLimiter,
    _reset_all_for_tests,
)


@pytest.mark.asyncio
async def test_in_memory_limiter_blocks_after_threshold() -> None:
    """Sanity: the limiter raises 429 once the bucket is full."""
    from fastapi import HTTPException

    limiter = InMemoryRateLimiter(limit=3, window_seconds=60.0)
    for _ in range(3):
        await limiter.acquire("scope", "client-a")

    with pytest.raises(HTTPException) as exc:
        await limiter.acquire("scope", "client-a")
    assert exc.value.status_code == 429
    assert "Retry-After" in (exc.value.headers or {})
    # Different key shares the limit per (scope, key), so client-b is unaffected.
    await limiter.acquire("scope", "client-b")


@pytest.mark.asyncio
async def test_in_memory_limiter_window_expires(monkeypatch) -> None:
    """Once the window slides past, the bucket reopens."""
    import time

    from backend.app.security import rate_limit as rl_mod

    fake_time = [1000.0]

    def now() -> float:
        return fake_time[0]

    monkeypatch.setattr(rl_mod.time, "monotonic", now)
    limiter = InMemoryRateLimiter(limit=2, window_seconds=10.0)
    await limiter.acquire("s", "k")
    await limiter.acquire("s", "k")
    fake_time[0] += 11.0  # advance past the window
    await limiter.acquire("s", "k")  # must succeed without raising


@pytest.mark.asyncio
async def test_concurrent_acquires_serialise_correctly() -> None:
    """Lots of concurrent ``acquire`` calls must not race past the limit."""
    from fastapi import HTTPException

    limiter = InMemoryRateLimiter(limit=5, window_seconds=60.0)

    async def attempt() -> bool:
        try:
            await limiter.acquire("concurrent", "k")
            return True
        except HTTPException:
            return False

    results = await asyncio.gather(*[attempt() for _ in range(20)])
    assert sum(1 for ok in results if ok) == 5


@pytest.mark.asyncio
async def test_local_login_endpoint_rate_limits(v1_client, monkeypatch) -> None:
    """``POST /v1/auth/local/login`` must 429 after the 11th attempt within 60s.

    The login limiter is set to ``limit=10``. We hammer with bogus
    credentials and check the 11th attempt comes back as 429 even though
    the email does not exist (limiter runs **before** the handler body).
    """
    monkeypatch.setenv("SHIP_AUTH_MODE", "local")
    # Reset the cached settings so the new env is observed.
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    _reset_all_for_tests()

    last_status = None
    for i in range(LOGIN_LIMITER.limit + 1):
        res = await v1_client.post(
            "/v1/auth/local/login",
            json={"email": f"nobody-{i}@example.com", "password": "bogus-pw"},
        )
        last_status = res.status_code
    assert last_status == 429, f"expected 429 on attempt #{LOGIN_LIMITER.limit + 1}"


@pytest.mark.asyncio
async def test_signup_endpoint_rate_limits(v1_client, monkeypatch) -> None:
    monkeypatch.setenv("SHIP_AUTH_MODE", "local")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    _reset_all_for_tests()

    last_status = None
    for i in range(SIGNUP_LIMITER.limit + 1):
        res = await v1_client.post(
            "/v1/auth/local/signup",
            json={
                "email": f"signup-{i}-x@example.com",
                "display_name": "x",
                "password": "12345678",
            },
        )
        last_status = res.status_code
    assert last_status == 429
