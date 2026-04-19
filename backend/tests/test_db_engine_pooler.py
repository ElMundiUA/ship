"""Pooler-aware engine config (Neon PgBouncer compat).

Covers the small detection helper and the engine-kwarg derivation. We
deliberately don't open a real connection — the goal is to lock the
behaviour for the two operationally-distinct DSN flavours so a future
refactor can't silently re-enable prepared statements on the pooled URL.
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import NullPool

from backend.app.db import session as db_session


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql+asyncpg://u:p@ep-foo-pooler.eu-central-1.aws.neon.tech/db", True),
        ("postgresql+asyncpg://u:p@ep-foo.eu-central-1.aws.neon.tech/db", False),
        ("postgresql+asyncpg://ship:ship@localhost:5433/ship", False),
        ("postgresql+asyncpg://u:p@db-pooler-1.internal/db", True),  # generic pooler host
        ("not-a-url", False),
    ],
)
def test_pgbouncer_pooled_detection(url: str, expected: bool) -> None:
    assert db_session._is_pgbouncer_pooled(url) is expected


def test_engine_kwargs_for_direct_dsn_keeps_default_pool() -> None:
    local = "postgresql+asyncpg://ship:ship@localhost:5433/ship"
    kwargs = db_session._engine_kwargs(local, local)
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["future"] is True
    # Direct DSNs run with SQLAlchemy's default QueuePool — no override.
    assert "poolclass" not in kwargs
    assert "prepared_statement_cache_size" not in kwargs
    # No TLS for plain localhost dev — let asyncpg decide.
    assert "connect_args" not in kwargs


def test_engine_kwargs_for_pooled_dsn_disables_prepared_statements() -> None:
    pooled = "postgresql+asyncpg://ship:ship@ep-foo-pooler.eu-central-1.aws.neon.tech/ship"
    kwargs = db_session._engine_kwargs(pooled, pooled)
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["poolclass"] is NullPool
    # asyncpg's native kwarg, passed via connect_args -> create_engine
    # rejects the dialect-level alias when combined with NullPool.
    assert kwargs["connect_args"]["statement_cache_size"] == 0
    # Cloud (non-loopback) host -> default ssl=True even without sslmode in
    # the operator-pasted DSN; asyncpg negotiates TLS.
    assert kwargs["connect_args"]["ssl"] is True
    # And — critically — these MUST NOT leak as top-level engine kwargs,
    # otherwise SQLAlchemy raises TypeError at engine construction.
    assert "prepared_statement_cache_size" not in kwargs
    assert "ssl" not in kwargs


def test_engine_kwargs_translates_neon_sslmode_to_asyncpg_ssl_arg() -> None:
    original = (
        "postgresql://u:p@ep-foo-pooler.eu-central-1.aws.neon.tech/db"
        "?sslmode=require&channel_binding=require"
    )
    # ``async_database_url`` would normalise this to the +asyncpg URL with
    # the libpq query params stripped; mimic that here so the test is
    # self-contained.
    normalised = "postgresql+asyncpg://u:p@ep-foo-pooler.eu-central-1.aws.neon.tech/db"
    kwargs = db_session._engine_kwargs(normalised, original)
    assert kwargs["connect_args"]["ssl"] == "require"
    assert kwargs["connect_args"]["statement_cache_size"] == 0
    assert kwargs["poolclass"] is NullPool


def test_engine_can_actually_be_constructed_for_neon_pooled_dsn() -> None:
    """Regression: ``prepared_statement_cache_size`` as a top-level engine
    kwarg crashed SQLAlchemy 2.0 with ``TypeError`` against NullPool, so the
    runtime async engine never came up on Neon. This test calls
    create_async_engine for real (no connection — engine construction only)
    so a future refactor that re-introduces the bug fails fast.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    normalised = "postgresql+asyncpg://u:p@ep-foo-pooler.eu-central-1.aws.neon.tech/db"
    kwargs = db_session._engine_kwargs(
        normalised,
        normalised + "?sslmode=require",
    )
    engine = create_async_engine(normalised, **kwargs)
    assert str(engine.url).startswith("postgresql+asyncpg://")
