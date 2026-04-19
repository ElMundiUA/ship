"""End-to-end tests for the secret-probe worker + on-demand `/probe` route.

Pulls the live Postgres test fixture set so we exercise the same selection
SQL the worker would run in production. The actual third-party network call
is mocked at ``probe_one`` — the point of these tests is the *plumbing*
(which rows get picked, what gets written back, idempotency) rather than the
per-kind validators (covered in ``test_secret_probe.py``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_worker_picks_pending_row_and_writes_verdict(
    monkeypatch: pytest.MonkeyPatch, db_session, seed_workspace, v1_client
) -> None:
    from backend.app.db.models.tenancy import Integration
    from backend.app.db.session import get_sessionmaker
    from backend.app.workers import secret_probe as worker

    user, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    # The PUT route now probes inline; stub it across both the API path and
    # the worker import so this test stays hermetic and offline.
    calls: list[tuple[str, str]] = []

    async def _stub_probe(kind: str, secret: str, _config):
        calls.append((kind, secret))
        return "ok", None

    from backend.app.api.v1.routes import integrations as routes

    monkeypatch.setattr(routes, "probe_one", _stub_probe)
    monkeypatch.setattr(worker, "probe_one", _stub_probe)
    monkeypatch.setattr(worker, "get_sessionmaker", lambda: _MakerWrapper(db_session))

    # Provision via the API so we exercise the same encrypt path the
    # workspace owner sees. After the inline probe the row is already 'ok';
    # the worker should still re-pick stale rows on its cadence.
    response = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/linear",
        headers=headers,
        json={"kind": "linear", "config": {"team_id": "ENG"}, "secret": "lin_api_x"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    # Force the row back to 'pending' so the worker selection picks it up
    # again — the original cron path still has a job to do for rotated rows
    # in self-hosted topologies that opt into the worker.
    from backend.app.db.models.tenancy import Integration as IntegrationModel

    row = (
        await db_session.execute(
            select(IntegrationModel).where(IntegrationModel.workspace_id == workspace.id)
        )
    ).scalar_one()
    row.status = "pending"
    row.last_health_at = None
    await db_session.flush()

    summary = await worker.probe_pending_secrets()
    # The shared test database may contain leftover integrations from prior
    # smoke runs; we only assert that *our* row was processed.
    assert summary["checked"] >= 1
    assert ("linear", "lin_api_x") in calls

    row = (
        await db_session.execute(
            select(Integration).where(Integration.workspace_id == workspace.id)
        )
    ).scalar_one()
    assert row.status == "ok"
    assert row.last_health_at is not None
    assert row.last_health_error is None


@pytest.mark.asyncio
async def test_worker_skips_rows_without_secret(
    monkeypatch: pytest.MonkeyPatch, db_session, seed_workspace
) -> None:
    from backend.app.db.models.tenancy import Integration
    from backend.app.workers import secret_probe as worker

    _, _, workspace = seed_workspace
    db_session.add(
        Integration(
            workspace_id=workspace.id, kind="webhook", config={"url": "https://x.test/h"}
        )
    )
    await db_session.flush()

    monkeypatch.setattr(worker, "get_sessionmaker", lambda: _MakerWrapper(db_session))

    # Our brand-new webhook row has no secret, so it's not eligible. We can't
    # assert checked==0 because the shared DB may have leftover rows; the
    # important thing is our row was *not* picked.
    before = (
        await db_session.execute(select(Integration).where(Integration.workspace_id == workspace.id))
    ).scalar_one()
    assert before.status == "pending"
    await worker.probe_pending_secrets()
    after = (
        await db_session.execute(select(Integration).where(Integration.workspace_id == workspace.id))
    ).scalar_one()
    assert after.status == "pending"
    assert after.last_health_at is None


@pytest.mark.asyncio
async def test_worker_reprobes_stale_rows(
    monkeypatch: pytest.MonkeyPatch, db_session, seed_workspace
) -> None:
    from backend.app.db.models.tenancy import Integration
    from backend.app.security.encryption import encrypt
    from backend.app.workers import secret_probe as worker

    _, _, workspace = seed_workspace
    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    row = Integration(
        workspace_id=workspace.id,
        kind="github",
        config={"org": "acme"},
        secret_ciphertext=encrypt("ghp_old"),
        status="ok",
        last_health_at=long_ago,
    )
    db_session.add(row)
    await db_session.flush()

    async def _stub(kind: str, secret: str, _config):
        return "error", "revoked upstream"

    monkeypatch.setattr(worker, "probe_one", _stub)
    monkeypatch.setattr(worker, "get_sessionmaker", lambda: _MakerWrapper(db_session))

    summary = await worker.probe_pending_secrets()
    assert summary["checked"] >= 1
    refreshed = (
        await db_session.execute(select(Integration).where(Integration.id == row.id))
    ).scalar_one()
    assert refreshed.status == "error"
    assert refreshed.last_health_error == "revoked upstream"


@pytest.mark.asyncio
async def test_probe_endpoint_runs_inline_and_persists(
    monkeypatch: pytest.MonkeyPatch, db_session, seed_workspace, v1_client
) -> None:
    from backend.app.api.v1.routes import integrations as routes
    from backend.app.db.models.tenancy import Integration

    user, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    # Stub before the PUT so the inline probe doesn't hit the network.
    async def _ok(_kind, _secret, _config):
        return "ok", None

    monkeypatch.setattr(routes, "probe_one", _ok)

    await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/linear",
        headers=headers,
        json={"kind": "linear", "config": {}, "secret": "lin_api_x"},
    )

    async def _stub(kind: str, secret: str, _config):
        return "error", "rejected"

    monkeypatch.setattr(routes, "probe_one", _stub)

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/integrations/linear/probe",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "error"
    assert body["last_health_error"] == "rejected"
    assert body["last_health_at"] is not None

    row = (
        await db_session.execute(
            select(Integration).where(Integration.workspace_id == workspace.id)
        )
    ).scalar_one()
    assert row.status == "error"


@pytest.mark.asyncio
async def test_probe_endpoint_409_when_no_secret(
    db_session, seed_workspace, v1_client
) -> None:
    from backend.app.db.models.tenancy import Integration

    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    db_session.add(
        Integration(workspace_id=workspace.id, kind="webhook", config={"url": "x"})
    )
    await db_session.flush()
    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/integrations/webhook/probe", headers=headers
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_probe_endpoint_404_for_unknown_kind(seed_workspace, v1_client) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/integrations/linear/probe", headers=headers
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MakerWrapper:
    """Adapt a single test session to the ``async with sessionmaker() as s`` shape.

    The worker job opens its own short-lived session via ``get_sessionmaker()``;
    in tests we want that session to be the same transactional one our
    fixture set up so reads/writes are visible to the assertions.
    """

    def __init__(self, session) -> None:
        self._session = session

    def __call__(self) -> "_NoCloseSession":
        return _NoCloseSession(self._session)


class _NoCloseSession:
    def __init__(self, inner) -> None:
        self._inner = inner

    async def __aenter__(self):
        return self._inner

    async def __aexit__(self, *_):
        # The fixture manages the session lifecycle (rollback on test exit),
        # so we don't actually close here. The job calls .commit() which is a
        # no-op inside the SAVEPOINT-wrapped fixture.
        return None
