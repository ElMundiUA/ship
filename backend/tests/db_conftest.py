"""Postgres-backed fixtures for the v1 API tests (RFC-0006).

Imported by ``backend/tests/conftest.py`` so the fixtures live alongside the
existing ones without forcing every test in this folder to depend on
Postgres. Tests that need the database opt in by accepting the
``db_session`` or ``v1_client`` fixtures.

Behaviour:

- ``TEST_DATABASE_URL`` (env) overrides the connection string. By default we
  point at the local docker-compose Postgres on port 5433.
- If the database is unreachable, every fixture in this module emits
  ``pytest.skip(...)``; the rest of the suite continues unaffected.
- The full migration set runs once per test session.
- Each test runs inside an outer transaction with a SAVEPOINT, and the outer
  transaction is rolled back at teardown — so tests are isolated and fast
  without TRUNCATE storms.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://ship:ship@localhost:5433/ship"
)


def _resolve_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)


def _sync_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg://" + url[len("postgresql+asyncpg://") :]
    return url


@pytest_asyncio.fixture
async def _engine() -> AsyncIterator[AsyncEngine]:
    """Per-test engine with ``NullPool`` so every ``connect()`` opens a fresh
    asyncpg connection in the *current* event loop. This sidesteps the
    pytest-asyncio + asyncpg cross-loop trap without forcing the whole suite
    to share one session-scoped event loop.
    """
    url = _resolve_url()
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover — environment dependent
        await engine.dispose()
        pytest.skip(f"Postgres unreachable for v1 tests ({exc!s})")
    yield engine
    await engine.dispose()


_MIGRATION_DONE = False


@pytest.fixture(scope="session")
def _migrated() -> None:
    """Run Alembic migrations against the test database once per session.

    Runs synchronously via psycopg, so it has no event-loop affinity and
    composes safely with the per-test async engine above.
    """
    global _MIGRATION_DONE
    if _MIGRATION_DONE:
        return
    from alembic import command
    from alembic.config import Config

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    alembic_ini = os.path.join(repo_root, "backend", "alembic.ini")
    cfg = Config(alembic_ini)
    cfg.set_main_option("sqlalchemy.url", _sync_url(_resolve_url()))
    cfg.set_main_option("script_location", "backend/migrations")
    command.upgrade(cfg, "head")
    _MIGRATION_DONE = True


@pytest_asyncio.fixture
async def db_session(
    _engine: AsyncEngine, _migrated: None
) -> AsyncIterator[AsyncSession]:
    """Per-test session that rolls back any writes when the test finishes."""
    connection = await _engine.connect()
    transaction = await connection.begin()
    sessionmaker = async_sessionmaker(
        bind=connection, expire_on_commit=False, class_=AsyncSession
    )
    session = sessionmaker()
    try:
        yield session
    finally:
        await session.close()
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def v1_client(db_session: AsyncSession):
    """Async HTTP client that shares the per-test transactional session.

    Uses ``httpx.AsyncClient`` with FastAPI's ASGI transport so both the test
    and the request handler run in the same event loop — a hard requirement
    for asyncpg sessions, which are bound to the loop they were opened on.
    """
    from httpx import ASGITransport, AsyncClient

    from backend.app.db.session import get_session
    from backend.app.main import app

    async def _override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def seed_user(db_session: AsyncSession):
    """Insert a user + matching personal org and return both."""
    from backend.app.db.models.tenancy import Org, OrgMember, User

    user = User(email=f"test-{uuid.uuid4().hex[:8]}@example.com", display_name="Test")
    db_session.add(user)
    await db_session.flush()
    org = Org(
        slug=f"personal-{str(user.id).replace('-', '')[:12]}",
        name="Test personal org",
        plan="free",
    )
    db_session.add(org)
    await db_session.flush()
    db_session.add(OrgMember(org_id=org.id, user_id=user.id, role="org_owner"))
    await db_session.flush()
    return user, org


@pytest_asyncio.fixture
async def seed_user_with_token(db_session: AsyncSession, seed_user):
    """Mint a real PAT for ``seed_user`` and return the (user, raw_token) pair."""
    import secrets

    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import ApiToken

    user, _ = seed_user
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    token = ApiToken(
        user_id=user.id,
        name="test-token",
        hashed_secret=_hash_token(raw),
        prefix=PAT_PREFIX,
        scopes=["workspace:read", "workspace:write"],
    )
    db_session.add(token)
    await db_session.flush()
    return user, raw


@pytest_asyncio.fixture
async def seed_workspace(db_session: AsyncSession, seed_user_with_token, seed_user):
    """Provision ``seed_user_with_token`` as the owner of a fresh workspace.

    Returns ``(user, raw_pat, workspace)`` so tests can exercise workspace-
    scoped routes without first creating the workspace over HTTP.
    """
    from backend.app.db.models.tenancy import Workspace, WorkspaceMember

    user, raw = seed_user_with_token
    _, org = seed_user
    workspace = Workspace(
        org_id=org.id, slug=f"ws-{uuid.uuid4().hex[:6]}", name="Test workspace"
    )
    db_session.add(workspace)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner")
    )
    await db_session.flush()
    return user, raw, workspace
