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
    kwargs = db_session._engine_kwargs(
        "postgresql+asyncpg://ship:ship@localhost:5433/ship"
    )
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["future"] is True
    # Direct DSNs run with SQLAlchemy's default QueuePool — no override.
    assert "poolclass" not in kwargs
    assert "prepared_statement_cache_size" not in kwargs


def test_engine_kwargs_for_pooled_dsn_disables_prepared_statements() -> None:
    kwargs = db_session._engine_kwargs(
        "postgresql+asyncpg://ship:ship@ep-foo-pooler.eu-central-1.aws.neon.tech/ship"
    )
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["poolclass"] is NullPool
    # SQLAlchemy forwards this to asyncpg as ``statement_cache_size=0``.
    assert kwargs["prepared_statement_cache_size"] == 0
