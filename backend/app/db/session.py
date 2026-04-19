"""Async engine + session factory.

Single global engine per process; FastAPI dependency :func:`get_session`
yields a transactional session. The engine is lazily constructed so import
order during tests (where settings get reconfigured) stays simple.

Neon pooled-DSN (PgBouncer transaction mode)
--------------------------------------------

Neon exposes two URLs per branch: a *direct* DSN (long-lived TCP, prepared
statements work fine — used by Alembic) and a *pooled* DSN (host contains
``-pooler``, fronted by PgBouncer in transaction mode — what runtime
traffic should use). PgBouncer in transaction mode multiplexes each
transaction across upstream backends, so prepared statements created on
one backend cannot be re-used on another and asyncpg's per-connection
prepared-statement cache must be disabled. We also skip SQLAlchemy's own
connection pool: the pooler already pools for us, and an extra layer just
holds idle FDs that PgBouncer thinks are live.

This compatibility shim is gated on the ``-pooler`` host substring so
local dev (plain Postgres + pgvector) keeps the default fast pool.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from backend.app.core.config import get_settings


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _is_pgbouncer_pooled(url: str) -> bool:
    """Return True for Neon-style pooler hosts (e.g. ``ep-foo-pooler.eu-central-1...``).

    The ``-pooler`` substring is Neon's documented marker; nothing else in
    our deployment chain shares the convention, so we use it as a precise
    signal without having to thread an extra setting through Settings.
    """
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return "-pooler" in host


def _engine_kwargs(database_url: str) -> dict[str, Any]:
    """Pick SQLAlchemy/asyncpg arguments based on the DSN flavor."""
    kwargs: dict[str, Any] = {
        # ``pool_pre_ping`` is critical for any pool fronted by PgBouncer —
        # it transparently re-opens connections the pooler closed during a
        # scale-to-zero idle period. Cheap on direct DSNs too.
        "pool_pre_ping": True,
        "future": True,
    }
    if _is_pgbouncer_pooled(database_url):
        # NullPool delegates pooling to PgBouncer (the Neon pooler is the
        # pool of record). Without this, SQLAlchemy holds long-lived
        # connections that PgBouncer assumes are short-lived transactions
        # and the pool quickly fills up with idle backends.
        kwargs["poolclass"] = NullPool
        # The asyncpg dialect forwards this to ``asyncpg.connect`` as
        # ``statement_cache_size=0``, which disables prepared statements.
        # PgBouncer transaction mode cannot route a re-used prepared
        # statement back to its origin backend, so the cache is a recipe
        # for "prepared statement does not exist" 26000 / 42P05 errors at
        # p99 once traffic crosses ~1 backend per request.
        kwargs["prepared_statement_cache_size"] = 0
    return kwargs


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url, **_engine_kwargs(settings.database_url)
        )
        _sessionmaker = async_sessionmaker(
            _engine, expire_on_commit=False, class_=AsyncSession
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if _sessionmaker is None:
        get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: hand out a session, commit on success, rollback on error."""
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Cleanly shut the engine down (called from FastAPI lifespan on exit)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
