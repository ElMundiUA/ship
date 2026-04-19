"""End-to-end tests for ``/v1/integrations/notion/*`` (Day 2 tracker WOW)."""

from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select


@pytest.fixture
def notion_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOTION_CLIENT_ID", "notion_client_test")
    monkeypatch.setenv("NOTION_CLIENT_SECRET", "notion_secret_test")
    monkeypatch.setenv("SHIP_PUBLIC_URL", "https://api.ship.test")
    monkeypatch.setenv("SHIP_CONSOLE_URL", "https://ship.test")
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_notion_install_start_returns_authorize_url(
    v1_client, seed_workspace, notion_env
) -> None:
    _, raw, workspace = seed_workspace
    response = await v1_client.post(
        "/v1/integrations/notion/install/start",
        headers={"Authorization": f"Bearer {raw}"},
        params={"workspace_id": str(workspace.id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["install_url"].startswith(
        "https://api.notion.com/v1/oauth/authorize?"
    )
    qs = parse_qs(urlparse(body["install_url"]).query)
    assert qs["client_id"] == ["notion_client_test"]
    assert qs["owner"] == ["user"]
    assert qs["response_type"] == ["code"]
    assert qs["state"] == [body["state"]]


@pytest.mark.asyncio
async def test_notion_install_start_503_when_unconfigured(
    v1_client, seed_workspace, monkeypatch
) -> None:
    monkeypatch.delenv("NOTION_CLIENT_ID", raising=False)
    monkeypatch.delenv("NOTION_CLIENT_SECRET", raising=False)
    from backend.app.core.config import get_settings

    get_settings.cache_clear()
    try:
        _, raw, workspace = seed_workspace
        response = await v1_client.post(
            "/v1/integrations/notion/install/start",
            headers={"Authorization": f"Bearer {raw}"},
            params={"workspace_id": str(workspace.id)},
        )
        assert response.status_code == 503
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_notion_install_start_404_for_non_member(
    v1_client, seed_user_with_token, notion_env
) -> None:
    _, raw = seed_user_with_token
    response = await v1_client.post(
        "/v1/integrations/notion/install/start",
        headers={"Authorization": f"Bearer {raw}"},
        params={"workspace_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_notion_install_callback_persists_token(
    v1_client, db_session, seed_workspace, notion_env, monkeypatch
) -> None:
    from backend.app.core.config import get_settings
    from backend.app.db.models.tenancy import AuditLog, Integration
    from backend.app.integrations.notion.oauth import (
        NotionTokenBundle,
        build_oauth_state,
    )
    from backend.app.security.encryption import decrypt

    _, _, workspace = seed_workspace
    workspace_id = workspace.id
    state = build_oauth_state(workspace_id, settings=get_settings())

    async def _fake_exchange(code, *, settings, redirect_uri, client=None):
        assert redirect_uri.endswith(
            "/v1/integrations/notion/install/callback"
        )
        return NotionTokenBundle(
            access_token="ntn_secret_token",
            bot_id="bot-1",
            workspace_id="notion-ws-1",
            workspace_name="Acme",
            workspace_icon="https://acme.example/icon.png",
            owner={"type": "user"},
        )

    monkeypatch.setattr(
        "backend.app.api.v1.routes.notion_oauth.exchange_code_for_token",
        _fake_exchange,
    )

    response = await v1_client.get(
        "/v1/integrations/notion/install/callback",
        params={"state": state, "code": "code-from-notion"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303, 307)
    location = response.headers["location"]
    assert location.startswith("https://ship.test/onboarding?step=tracker")
    assert "notion=connected" in location

    row = (
        await db_session.execute(
            select(Integration).where(
                Integration.workspace_id == workspace_id,
                Integration.kind == "notion",
            )
        )
    ).scalar_one()
    assert row.status == "ok"
    assert decrypt(row.secret_ciphertext) == "ntn_secret_token"
    assert row.config["bot_id"] == "bot-1"
    assert row.config["notion_workspace_id"] == "notion-ws-1"
    assert row.config["notion_workspace_name"] == "Acme"

    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "integration.create",
            )
        )
    ).scalar_one()
    assert audit.payload["kind"] == "notion"
    assert audit.payload["notion_workspace_id"] == "notion-ws-1"


@pytest.mark.asyncio
async def test_notion_install_callback_rejects_bad_state(
    v1_client, notion_env
) -> None:
    response = await v1_client.get(
        "/v1/integrations/notion/install/callback",
        params={"state": "garbage", "code": "x"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303, 307)
    assert "error=bad_state" in response.headers["location"]
