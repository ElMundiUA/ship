"""End-to-end tests for ``/v1/integrations/linear/*`` (Day 2 tracker WOW).

Mirrors the GitHub App test layout. We don't hit Linear's real OAuth
server — ``exchange_code_for_token`` is monkey-patched per test so we
control the access-token + scope returned to the callback handler.
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select


@pytest.fixture
def linear_env(monkeypatch: pytest.MonkeyPatch):
    """Wire OAuth client creds + canonical public/console URLs.

    Bust ``get_settings`` so the routes pick up the fresh env. The tear-
    down clears the cache again to avoid bleeding state into other
    test files.
    """
    monkeypatch.setenv("LINEAR_CLIENT_ID", "lin_client_test")
    monkeypatch.setenv("LINEAR_CLIENT_SECRET", "lin_secret_test")
    monkeypatch.setenv(
        "LINEAR_OAUTH_SCOPES", "read,write,issues:create,comments:create"
    )
    monkeypatch.setenv("SHIP_PUBLIC_URL", "https://api.ship.test")
    monkeypatch.setenv("SHIP_CONSOLE_URL", "https://ship.test")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_linear_install_start_requires_admin(
    v1_client, seed_workspace, linear_env
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.post(
        "/v1/integrations/linear/install/start",
        headers=headers,
        params={"workspace_id": str(workspace.id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["install_url"].startswith("https://linear.app/oauth/authorize?")
    qs = parse_qs(urlparse(body["install_url"]).query)
    assert qs["client_id"] == ["lin_client_test"]
    assert qs["redirect_uri"] == [
        "https://api.ship.test/v1/integrations/linear/install/callback"
    ]
    assert qs["state"] == [body["state"]]
    assert qs["actor"] == ["user"]
    assert "read,write" in qs["scope"][0]


@pytest.mark.asyncio
async def test_linear_install_start_503_when_unconfigured(
    v1_client, seed_workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No client id/secret on the deployment → 503, not a 500."""
    monkeypatch.delenv("LINEAR_CLIENT_ID", raising=False)
    monkeypatch.delenv("LINEAR_CLIENT_SECRET", raising=False)
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    try:
        _, raw, workspace = seed_workspace
        response = await v1_client.post(
            "/v1/integrations/linear/install/start",
            headers={"Authorization": f"Bearer {raw}"},
            params={"workspace_id": str(workspace.id)},
        )
        assert response.status_code == 503
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_linear_install_start_404_for_non_member(
    v1_client, seed_user_with_token, linear_env
) -> None:
    _, raw = seed_user_with_token
    response = await v1_client.post(
        "/v1/integrations/linear/install/start",
        headers={"Authorization": f"Bearer {raw}"},
        params={"workspace_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_linear_install_callback_persists_token(
    v1_client, db_session, seed_workspace, linear_env, monkeypatch
) -> None:
    """Round-trip the callback and ensure an Integration row is written."""
    from backend.app.core.config import get_settings
    from backend.app.db.models.integrations import (
        NativeIntegrationAuditEvent,
        NativeIntegrationCredential,
        NativeIntegrationInstallation,
    )
    from backend.app.db.models.tenancy import AuditLog, Integration
    from backend.app.integrations.linear import oauth as linear_oauth_mod
    from backend.app.integrations.linear.oauth import (
        LinearTokenBundle,
        build_oauth_state,
    )
    from backend.app.security.encryption import decrypt

    _, _, workspace = seed_workspace
    workspace_id = workspace.id

    state = build_oauth_state(workspace_id, settings=get_settings())

    async def _fake_exchange(code, *, settings, redirect_uri, client=None):
        assert code == "auth-code-from-linear"
        assert redirect_uri.endswith("/v1/integrations/linear/install/callback")
        return LinearTokenBundle(
            access_token="lin_access_token_xyz",
            token_type="Bearer",
            scope="read,write",
            expires_in=None,
        )

    # Patch where the route imports the symbol — the route does
    # `from ... import exchange_code_for_token`, so we patch the
    # rebound name in that module.
    monkeypatch.setattr(
        "backend.app.api.v1.routes.linear_oauth.exchange_code_for_token",
        _fake_exchange,
    )
    # Belt-and-braces: also patch the source to keep tests resilient
    # if a future refactor switches to module-qualified usage.
    monkeypatch.setattr(linear_oauth_mod, "exchange_code_for_token", _fake_exchange)

    response = await v1_client.get(
        "/v1/integrations/linear/install/callback",
        params={"state": state, "code": "auth-code-from-linear"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303, 307)
    location = response.headers["location"]
    assert location.startswith("https://ship.test/onboarding?step=tracker")
    assert "linear=connected" in location
    assert f"ws={workspace_id}" in location

    row = (
        await db_session.execute(
            select(Integration).where(
                Integration.workspace_id == workspace_id,
                Integration.kind == "linear",
            )
        )
    ).scalar_one()
    assert row.status == "ok"
    assert row.secret_ciphertext is not None
    assert decrypt(row.secret_ciphertext) == "lin_access_token_xyz"
    assert row.config.get("scope") == "read,write"
    assert row.last_health_at is not None

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "integration.create",
            )
        )
    ).scalar_one()
    assert audit.payload["kind"] == "linear"
    assert audit.payload["via"] == "oauth"

    native = (
        await db_session.execute(
            select(NativeIntegrationInstallation).where(
                NativeIntegrationInstallation.workspace_id == workspace_id,
                NativeIntegrationInstallation.provider == "linear",
            )
        )
    ).scalar_one()
    assert native.auth_mode == "oauth"
    assert native.external_account_id == "default"
    assert native.capabilities == ["tracker"]
    assert native.scopes == ["read", "write"]
    assert native.status == "ready"

    native_credential = (
        await db_session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == native.id,
                NativeIntegrationCredential.kind == "access_token",
            )
        )
    ).scalar_one()
    assert decrypt(native_credential.secret_ciphertext) == "lin_access_token_xyz"

    native_audit = (
        await db_session.execute(
            select(NativeIntegrationAuditEvent).where(
                NativeIntegrationAuditEvent.workspace_id == workspace_id,
                NativeIntegrationAuditEvent.provider == "linear",
            )
        )
    ).scalar_one()
    assert native_audit.action == "native_integration.create"
    assert native_audit.payload["scope"] == "read,write"


@pytest.mark.asyncio
async def test_linear_install_callback_rejects_bad_state(
    v1_client, linear_env
) -> None:
    response = await v1_client.get(
        "/v1/integrations/linear/install/callback",
        params={"state": "not-a-jwt", "code": "anything"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303, 307)
    location = response.headers["location"]
    assert "/onboarding" in location
    assert "step=tracker" in location
    assert "error=bad_state" in location


@pytest.mark.asyncio
async def test_linear_install_callback_propagates_vendor_error(
    v1_client, seed_workspace, linear_env
) -> None:
    """If Linear returned ``error=access_denied`` we bounce with that code."""
    from backend.app.core.config import get_settings
    from backend.app.integrations.linear.oauth import build_oauth_state

    _, _, workspace = seed_workspace
    state = build_oauth_state(workspace.id, settings=get_settings())

    response = await v1_client.get(
        "/v1/integrations/linear/install/callback",
        params={"state": state, "error": "access_denied"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303, 307)
    location = response.headers["location"]
    assert "step=tracker" in location
    assert "error=access_denied" in location


@pytest.mark.asyncio
async def test_linear_install_callback_handles_exchange_failure(
    v1_client, db_session, seed_workspace, linear_env, monkeypatch
) -> None:
    from backend.app.core.config import get_settings
    from backend.app.db.models.tenancy import Integration
    from backend.app.integrations.linear.oauth import (
        LinearTokenExchangeFailed,
        build_oauth_state,
    )

    _, _, workspace = seed_workspace
    workspace_id = workspace.id
    state = build_oauth_state(workspace_id, settings=get_settings())

    async def _boom(code, *, settings, redirect_uri, client=None):
        raise LinearTokenExchangeFailed("400 Bad Request")

    monkeypatch.setattr(
        "backend.app.api.v1.routes.linear_oauth.exchange_code_for_token",
        _boom,
    )

    response = await v1_client.get(
        "/v1/integrations/linear/install/callback",
        params={"state": state, "code": "x"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303, 307)
    assert "error=exchange_failed" in response.headers["location"]

    # Nothing should have been persisted on a failed exchange.
    row = (
        await db_session.execute(
            select(Integration).where(
                Integration.workspace_id == workspace_id,
                Integration.kind == "linear",
            )
        )
    ).scalar_one_or_none()
    assert row is None


# ---------------------------------------------------------------------------
# Reconnect-doesn't-overwrite-team_id + team-repick endpoint.
# ---------------------------------------------------------------------------


def _patch_oauth_exchange(monkeypatch, scope: str = "admin read write"):
    """Stub the OAuth code-exchange + native side-effects so the callback
    only exercises the team-binding logic that the new tests care about."""
    from backend.app.integrations.linear import oauth as linear_oauth_mod
    from backend.app.integrations.linear.oauth import LinearTokenBundle

    async def _fake_exchange(code, *, settings, redirect_uri, client=None):
        return LinearTokenBundle(
            access_token="lin_access_repick_test",
            token_type="Bearer",
            scope=scope,
            expires_in=None,
        )

    monkeypatch.setattr(
        "backend.app.api.v1.routes.linear_oauth.exchange_code_for_token",
        _fake_exchange,
    )
    monkeypatch.setattr(linear_oauth_mod, "exchange_code_for_token", _fake_exchange)


def _patch_provisioner(
    monkeypatch,
    *,
    teams: list[dict],
    provisioned: dict | None = None,
):
    """Stub linear_provisioner.{list_teams, provision_team} so the
    callback / repick endpoint can run without a live Linear org."""
    from backend.app.services import linear_provisioner

    async def _list_teams(_tracker):
        return list(teams)

    async def _provision_team(*, tracker, team_key, settings):
        target = next((t for t in teams if t["key"] == team_key), None)
        if target is None:
            raise RuntimeError(f"team {team_key} not in stub list")
        result = provisioned or {
            "state_id_by_name": {"Todo": f"todo-{team_key}"},
            "label_id_by_stage": {"plan": f"plan-{team_key}"},
            "signal_label_ids": {},
            "canonical_to_native": {},
            "canonical_resolution_meta": {},
        }
        return linear_provisioner.ProvisionResult(
            team_id=target["id"],
            team_key=target["key"],
            state_id_by_name=result["state_id_by_name"],
            label_id_by_stage=result["label_id_by_stage"],
            signal_label_ids=result["signal_label_ids"],
            canonical_to_native=result["canonical_to_native"],
            canonical_resolution_meta=result["canonical_resolution_meta"],
        )

    monkeypatch.setattr(linear_provisioner, "list_teams", _list_teams)
    monkeypatch.setattr(linear_provisioner, "provision_team", _provision_team)


@pytest.mark.asyncio
async def test_callback_preserves_existing_team_on_reconnect(
    v1_client, db_session, seed_workspace, linear_env, monkeypatch
) -> None:
    """Reconnect must not overwrite a previously-bound ``team_id``.

    The pilot bug: an admin-scope reconnect ran from a Linear session
    where ``list_teams`` returned only one team. The auto-pick path
    silently rebound the workspace from its original team to the
    only-visible one, leaving the dashboard's project filter pointing
    at a wrong team (zero projects).
    """
    from backend.app.core.config import get_settings
    from backend.app.db.models.tenancy import Integration
    from backend.app.integrations.linear.oauth import build_oauth_state

    _, _, workspace = seed_workspace
    workspace_id = workspace.id

    # Pre-existing Integration row with the original team binding.
    original = Integration(
        workspace_id=workspace_id,
        kind="linear",
        status="ok",
        config={
            "team_id": "team-elship-uuid",
            "team_key": "ELS",
            "team_options": [
                {"id": "team-elship-uuid", "key": "ELS", "name": "elship"},
            ],
            "fsm_provisioned": True,
        },
    )
    db_session.add(original)
    await db_session.flush()

    _patch_oauth_exchange(monkeypatch)
    # Simulate the reconnect session where Linear's list_teams now also
    # exposes the ELS team alongside a sibling Buzz team — the ELS row
    # must win because it was the saved binding.
    _patch_provisioner(
        monkeypatch,
        teams=[
            {"id": "team-elship-uuid", "key": "ELS", "name": "elship"},
            {"id": "team-buzz-uuid", "key": "BUZ", "name": "Buzzz"},
        ],
    )

    state = build_oauth_state(workspace_id, settings=get_settings())
    response = await v1_client.get(
        "/v1/integrations/linear/install/callback",
        params={"state": state, "code": "auth-code"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303, 307)

    await db_session.refresh(original)
    config = original.config
    assert config["team_id"] == "team-elship-uuid"
    assert config["team_key"] == "ELS"
    # team_options should be refreshed to whatever the new session sees.
    keys = sorted(t["key"] for t in config["team_options"])
    assert keys == ["BUZ", "ELS"]


@pytest.mark.asyncio
async def test_callback_marks_needs_repick_when_saved_team_lost(
    v1_client, db_session, seed_workspace, linear_env, monkeypatch
) -> None:
    """Reconnect from an OAuth session that no longer sees the saved
    team must surface a ``needs_team_repick`` flag rather than silently
    auto-picking a different team."""
    from backend.app.core.config import get_settings
    from backend.app.db.models.tenancy import Integration
    from backend.app.integrations.linear.oauth import build_oauth_state

    _, _, workspace = seed_workspace
    workspace_id = workspace.id

    original = Integration(
        workspace_id=workspace_id,
        kind="linear",
        status="ok",
        config={
            "team_id": "team-elship-uuid",
            "team_key": "ELS",
            "team_options": [
                {"id": "team-elship-uuid", "key": "ELS", "name": "elship"},
            ],
            "fsm_provisioned": True,
        },
    )
    db_session.add(original)
    await db_session.flush()

    _patch_oauth_exchange(monkeypatch)
    # Reconnect session no longer sees ELS, only BUZ.
    _patch_provisioner(
        monkeypatch,
        teams=[{"id": "team-buzz-uuid", "key": "BUZ", "name": "Buzzz"}],
    )

    state = build_oauth_state(workspace_id, settings=get_settings())
    response = await v1_client.get(
        "/v1/integrations/linear/install/callback",
        params={"state": state, "code": "auth-code"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303, 307)

    await db_session.refresh(original)
    config = original.config
    # team_id stays as the original — we never silently rebind.
    assert config["team_id"] == "team-elship-uuid"
    assert config["team_key"] == "ELS"
    assert config["needs_team_repick"] is True
    assert config["fsm_provisioned"] is False


@pytest.mark.asyncio
async def test_team_repick_endpoint_updates_binding(
    v1_client, db_session, seed_workspace, linear_env, monkeypatch
) -> None:
    """``POST .../linear/team`` re-binds the workspace to a different
    team in the same org and re-provisions FSM state for it."""
    from backend.app.db.models.tenancy import AuditLog, Integration

    user, raw, workspace = seed_workspace
    workspace_id = workspace.id

    original = Integration(
        workspace_id=workspace_id,
        kind="linear",
        status="ok",
        secret_ciphertext=b"placeholder",  # ignored by stubs below
        config={
            "team_id": "team-buzz-uuid",
            "team_key": "BUZ",
            "team_options": [
                {"id": "team-elship-uuid", "key": "ELS", "name": "elship"},
                {"id": "team-buzz-uuid", "key": "BUZ", "name": "Buzzz"},
            ],
            "fsm_provisioned": True,
            "needs_team_repick": True,
            "scope": "admin read write",
        },
    )
    db_session.add(original)
    await db_session.flush()

    # Bypass the live token-fetch helper — the repick endpoint pulls a
    # decrypted access token via ``_fetch_live_linear_token`` which
    # touches the encrypted secret + native install table. Stub it so
    # the test focuses on the team-binding behaviour.
    async def _fake_token(_session, *, workspace_id):
        return None, "lin_access_repick_test"

    monkeypatch.setattr(
        "backend.app.api.v1.routes.linear_oauth._fetch_live_linear_token",
        _fake_token,
    )

    _patch_provisioner(
        monkeypatch,
        teams=[
            {"id": "team-elship-uuid", "key": "ELS", "name": "elship"},
            {"id": "team-buzz-uuid", "key": "BUZ", "name": "Buzzz"},
        ],
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/integrations/linear/team",
        json={"team_key": "ELS"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["team_id"] == "team-elship-uuid"
    assert body["team_key"] == "ELS"
    assert body["fsm_provisioned"] is True

    await db_session.refresh(original)
    config = original.config
    assert config["team_id"] == "team-elship-uuid"
    assert config["team_key"] == "ELS"
    assert config["fsm_provisioned"] is True
    assert "needs_team_repick" not in config

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "linear.team.repick",
            )
        )
    ).scalar_one()
    assert audit.payload["from_team_key"] == "BUZ"
    assert audit.payload["to_team_key"] == "ELS"


@pytest.mark.asyncio
async def test_team_repick_endpoint_404_for_unknown_team(
    v1_client, db_session, seed_workspace, linear_env, monkeypatch
) -> None:
    """Repick with a team key the live OAuth doesn't expose → 404."""
    from backend.app.db.models.tenancy import Integration

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id

    db_session.add(
        Integration(
            workspace_id=workspace_id,
            kind="linear",
            status="ok",
            secret_ciphertext=b"placeholder",
            config={"team_id": "team-buzz-uuid", "team_key": "BUZ"},
        )
    )
    await db_session.flush()

    async def _fake_token(_session, *, workspace_id):
        return None, "lin_access_repick_test"

    monkeypatch.setattr(
        "backend.app.api.v1.routes.linear_oauth._fetch_live_linear_token",
        _fake_token,
    )
    _patch_provisioner(
        monkeypatch,
        teams=[{"id": "team-buzz-uuid", "key": "BUZ", "name": "Buzzz"}],
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/integrations/linear/team",
        json={"team_key": "ELS"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "team_not_visible"


@pytest.mark.asyncio
async def test_team_repick_endpoint_requires_admin(
    v1_client, db_session, seed_workspace, linear_env, monkeypatch
) -> None:
    """Non-admin members cannot rebind workspace credentials."""
    import secrets as secrets_mod
    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import (
        ApiToken,
        Integration,
        User,
        WorkspaceMember,
    )

    _, _, workspace = seed_workspace
    workspace_id = workspace.id

    # Spin a second user with "member" role on the same workspace.
    member_user = User(
        email=f"member-{uuid.uuid4().hex[:6]}@example.com",
        display_name="member",
    )
    db_session.add(member_user)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=workspace_id,
            user_id=member_user.id,
            role="member",
            answer_specialist_slugs=["*"],
        )
    )
    raw_member = f"{PAT_PREFIX}{secrets_mod.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=member_user.id,
            name="member-pat",
            hashed_secret=_hash_token(raw_member),
            prefix=PAT_PREFIX,
            scopes=["workspace:read"],
        )
    )
    db_session.add(
        Integration(
            workspace_id=workspace_id,
            kind="linear",
            status="ok",
            secret_ciphertext=b"placeholder",
            config={"team_id": "team-buzz-uuid", "team_key": "BUZ"},
        )
    )
    await db_session.flush()

    response = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/integrations/linear/team",
        json={"team_key": "ELS"},
        headers={"Authorization": f"Bearer {raw_member}"},
    )
    assert response.status_code == 403
