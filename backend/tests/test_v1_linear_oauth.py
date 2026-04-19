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
