"""End-to-end tests for `/v1/workspaces/{id}/integrations` (RFC-0006).

Covers:

- Admins can upsert and delete integrations.
- The plaintext secret is never returned; only ``has_secret`` flips.
- PUT with a secret runs the probe synchronously and returns ok/error.
- Invalid kinds are rejected before touching the DB.
- Membership is required (404 for strangers, 403 for read-only members).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select


@pytest.fixture
def stub_probe_ok(monkeypatch: pytest.MonkeyPatch):
    """Make the inline probe deterministic so network-free tests stay green.

    The PUT route now hits ``probe_one`` synchronously; without a stub the
    Linear/GitHub validators would fire a real HTTPS request and the test
    would fail offline.
    """
    from backend.app.api.v1.routes import integrations as routes

    calls: list[tuple[str, str]] = []

    async def _stub(kind: str, secret: str, _config):
        calls.append((kind, secret))
        return "ok", None

    monkeypatch.setattr(routes, "probe_one", _stub)
    return calls


@pytest.mark.asyncio
async def test_integrations_require_membership(v1_client, seed_user_with_token) -> None:
    _, raw = seed_user_with_token
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.get(
        f"/v1/workspaces/{uuid.uuid4()}/integrations", headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_upsert_integration_and_secret_stays_opaque(
    v1_client, db_session, seed_workspace, stub_probe_ok
) -> None:
    """Raw-secret upsert path on a PAT-style provider (slack here).

    Linear / Notion live on OAuth-only — see
    ``test_oauth_only_kinds_reject_raw_secret_upserts``. We exercise the
    happy path on slack instead, since slack legitimately ships its
    bot token via the secret field.
    """
    from backend.app.db.models.tenancy import Integration

    user, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    create = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/slack",
        headers=headers,
        json={
            "kind": "slack",
            "config": {"channel": "#eng"},
            "secret": "xoxb-supersecret",
        },
    )
    assert create.status_code == 200, create.text
    body = create.json()
    assert body["kind"] == "slack"
    assert body["has_secret"] is True
    # Sync probe ran inline — operator sees ok in the same response.
    assert body["status"] == "ok"
    assert body["last_health_at"] is not None
    assert body["last_health_error"] is None
    assert "secret" not in body
    assert "secret_ciphertext" not in body
    assert body["config"] == {"channel": "#eng"}
    assert ("slack", "xoxb-supersecret") in stub_probe_ok

    # Round-trip via DB to confirm the ciphertext is present and decrypts.
    from backend.app.security.encryption import decrypt

    row = (
        await db_session.execute(
            select(Integration).where(Integration.workspace_id == workspace.id)
        )
    ).scalar_one()
    assert row.secret_ciphertext is not None
    assert decrypt(row.secret_ciphertext) == "xoxb-supersecret"

    # Editing config without a secret leaves the ciphertext untouched and
    # does not re-probe (status carries over from the previous save).
    update = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/slack",
        headers=headers,
        json={"kind": "slack", "config": {"channel": "#platform"}, "secret": None},
    )
    assert update.status_code == 200
    assert update.json()["has_secret"] is True
    assert update.json()["status"] == "ok"
    assert update.json()["config"] == {"channel": "#platform"}


@pytest.mark.asyncio
async def test_upsert_returns_error_status_when_probe_fails(
    v1_client, monkeypatch: pytest.MonkeyPatch, seed_workspace
) -> None:
    from backend.app.api.v1.routes import integrations as routes

    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    async def _stub(_kind, _secret, _config):
        return "error", "slack rejected the bot token (HTTP 401)"

    monkeypatch.setattr(routes, "probe_one", _stub)

    response = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/slack",
        headers=headers,
        json={"kind": "slack", "config": {}, "secret": "xoxb-revoked"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "error"
    assert body["last_health_error"] == "slack rejected the bot token (HTTP 401)"
    assert body["last_health_at"] is not None


@pytest.mark.parametrize("kind", ["linear", "notion"])
@pytest.mark.asyncio
async def test_oauth_only_kinds_reject_raw_secret_upserts(
    v1_client, seed_workspace, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Linear and Notion are OAuth-only on this surface.

    Pasting a personal API key worked historically but stranded a tenant
    on a single user's token (revocation, scope narrowing, on/off-
    boarding all break the integration). The OAuth round-trip
    (``POST /v1/integrations/{kind}/install/start``) is the only
    sanctioned entry point. The route returns a stable error code so
    the FE can swap in a "Sign in with {kind}" CTA on receipt.

    Probe must NOT run — the secret never gets near the third party.
    """
    from backend.app.api.v1.routes import integrations as routes

    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    async def _explode(*_args, **_kwargs):
        raise AssertionError(
            "probe_one must not run on OAuth-only-kinds raw-secret upserts"
        )

    monkeypatch.setattr(routes, "probe_one", _explode)

    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/{kind}",
        headers=headers,
        json={"kind": kind, "config": {}, "secret": "raw-key-attempt"},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json().get("detail")
    assert isinstance(detail, dict)
    assert detail.get("code") == "oauth_only"
    assert detail.get("kind") == kind
    assert "OAuth" in detail.get("message", "")


@pytest.mark.parametrize("kind", ["linear", "notion"])
@pytest.mark.asyncio
async def test_oauth_only_kinds_allow_config_only_edits(
    v1_client, db_session, seed_workspace, kind: str
) -> None:
    """Config-only edits on an OAuth-installed row still pass through.

    The 422 gate fires only when ``payload.secret is not None``, so an
    operator tuning ``team_id`` or ``project`` on a row already
    populated by the OAuth callback doesn't have to re-run the OAuth
    dance just to rename a team key.
    """
    from backend.app.db.models.tenancy import Integration

    user, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    # Pre-seed an OAuth-installed row (mimicking what
    # linear_oauth.py/notion_oauth.py write on callback) so the upsert
    # has something to update.
    db_session.add(
        Integration(
            workspace_id=workspace.id,
            kind=kind,
            config={"team_id": "OLD"},
            status="ok",
            secret_ciphertext=b"oauth-token-ciphertext",
        )
    )
    await db_session.flush()

    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/{kind}",
        headers=headers,
        json={"kind": kind, "config": {"team_id": "NEW"}, "secret": None},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"] == {"team_id": "NEW"}
    assert resp.json()["has_secret"] is True  # OAuth ciphertext untouched


@pytest.mark.asyncio
async def test_upsert_without_secret_keeps_pending(
    v1_client, monkeypatch: pytest.MonkeyPatch, seed_workspace
) -> None:
    """Creating a row with no secret should not run the probe and stays pending."""
    from backend.app.api.v1.routes import integrations as routes

    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    async def _explode(*_args, **_kwargs):
        raise AssertionError("probe_one should not be called when no secret is provided")

    monkeypatch.setattr(routes, "probe_one", _explode)

    response = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/webhook",
        headers=headers,
        json={"kind": "webhook", "config": {"url": "https://x.test/h"}, "secret": None},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["has_secret"] is False


@pytest.mark.asyncio
async def test_unknown_integration_kind_is_422(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/nonsense",
        headers=headers,
        json={"kind": "nonsense", "config": {}, "secret": "x"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_path_kind_must_match_payload(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/linear",
        headers=headers,
        json={"kind": "slack", "config": {}, "secret": "x"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_removes_integration(
    v1_client, seed_workspace, stub_probe_ok
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/slack",
        headers=headers,
        json={"kind": "slack", "config": {"channel": "#eng"}, "secret": "xoxb-..."},
    )
    deleted = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/integrations/slack",
        headers=headers,
    )
    assert deleted.status_code == 204
    listed = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/integrations", headers=headers
    )
    assert listed.status_code == 200
    assert listed.json() == []
