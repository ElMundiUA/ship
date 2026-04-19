"""Async engine + session factory.

Single global engine per process; FastAPI dependency :func:`get_session`
yields a transactional session. The engine is lazily constructed so import
order during tests (where settings get reconfigured) stays simple.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.core.config import get_settings


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        settings = get_settings()
        # ``pool_pre_ping`` is critical for Neon (and any pool fronted by
        # PgBouncer) — it transparently re-opens connections that were closed
        # by the pooler during scale-to-zero idle periods.
        engine_kwargs: dict[str, Any] = {
            "pool_pre_ping": True,
            "future": True,
        }
        _engine = create_async_engine(settings.database_url, **engine_kwargs)
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
