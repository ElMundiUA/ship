"""Wizard picker endpoints for Confluence — spaces + sections.

Stubs the Confluence HTTP via monkeypatching the route's `_confluence_get`
helper (same seam pattern used elsewhere) so tests don't reach Atlassian.
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import encrypt


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def _seed_confluence_integration(
    db_session, workspace_id, *, secret: str | None = "atl_token"
) -> Integration:
    row = Integration(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        kind="confluence",
        config={
            "site_url": "https://acme.atlassian.net",
            "email": "ops@example.com",
        },
        status="ok",
        secret_ciphertext=encrypt(secret) if secret else None,
    )
    db_session.add(row)
    return row


@pytest.mark.asyncio
async def test_list_confluence_spaces_returns_picker_items(
    v1_client, seed_workspace, db_session, monkeypatch
) -> None:
    _, raw, workspace = seed_workspace
    integration = _seed_confluence_integration(db_session, workspace.id)
    await db_session.flush()

    captured = {}

    async def _fake_get(*, site_url, email, token, path, params, workspace_id):
        captured["path"] = path
        captured["site_url"] = site_url
        return {
            "results": [
                {
                    "id": "S1",
                    "key": "ENG",
                    "name": "Engineering",
                    "type": "global",
                    "homepageId": "h1",
                    "description": {"plain": {"value": "Engineering team space"}},
                },
                {"id": "S2", "key": "ONB", "name": "Onboarding"},
            ],
        }

    monkeypatch.setattr(
        "backend.app.api.v1.routes.knowledge_import_sources._confluence_get",
        _fake_get,
    )

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/confluence/spaces",
        headers=_auth(raw),
        params={"integration_id": str(integration.id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [s["key"] for s in body["items"]] == ["ENG", "ONB"]
    assert body["items"][0]["description"] == "Engineering team space"
    assert body["items"][0]["type"] == "global"
    assert captured["path"] == "/wiki/api/v2/spaces"
    assert captured["site_url"] == "https://acme.atlassian.net"


@pytest.mark.asyncio
async def test_list_confluence_sections_includes_space_metadata(
    v1_client, seed_workspace, db_session, monkeypatch
) -> None:
    _, raw, workspace = seed_workspace
    integration = _seed_confluence_integration(db_session, workspace.id)
    await db_session.flush()

    paths: list[str] = []

    async def _fake_get(*, site_url, email, token, path, params, workspace_id):
        paths.append(path)
        if path == "/wiki/api/v2/spaces/S1":
            return {"id": "S1", "key": "ENG", "name": "Engineering"}
        # /wiki/api/v2/pages with depth=root
        assert path == "/wiki/api/v2/pages"
        assert params["depth"] == "root"
        assert params["space-id"] == "S1"
        return {
            "results": [
                {
                    "id": "100",
                    "title": "Onboarding handbook",
                    "spaceId": "S1",
                    "version": {"createdAt": "2026-04-30T10:00:00Z"},
                    "_links": {"webui": "/spaces/ENG/pages/100"},
                },
                {
                    "id": "200",
                    "title": "Engineering runbooks",
                    "spaceId": "S1",
                    "_links": {"webui": "/spaces/ENG/pages/200"},
                },
            ],
            "_links": {
                "next": "/wiki/api/v2/pages?space-id=S1&cursor=eyJfaWQiOiAiMSJ9"
            },
        }

    monkeypatch.setattr(
        "backend.app.api.v1.routes.knowledge_import_sources._confluence_get",
        _fake_get,
    )

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/confluence/sections",
        headers=_auth(raw),
        params={"integration_id": str(integration.id), "space_id": "S1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["title"] for item in body["items"]] == [
        "Onboarding handbook",
        "Engineering runbooks",
    ]
    first = body["items"][0]
    assert first["space_key"] == "ENG"
    assert first["space_name"] == "Engineering"
    assert first["space_id"] == "S1"
    assert first["url"] == "https://acme.atlassian.net/wiki/spaces/ENG/pages/100"
    assert first["last_edited_time"] == "2026-04-30T10:00:00Z"
    assert body["next_cursor"] == "eyJfaWQiOiAiMSJ9"
    assert body["has_more"] is True
    assert paths == ["/wiki/api/v2/spaces/S1", "/wiki/api/v2/pages"]


@pytest.mark.asyncio
async def test_list_confluence_spaces_404_for_wrong_kind(
    v1_client, seed_workspace, db_session
) -> None:
    _, raw, workspace = seed_workspace
    wrong = Integration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        kind="notion",
        config={},
        status="ok",
        secret_ciphertext=encrypt("x"),
    )
    db_session.add(wrong)
    await db_session.flush()

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/confluence/spaces",
        headers=_auth(raw),
        params={"integration_id": str(wrong.id)},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_confluence_spaces_400_when_creds_missing(
    v1_client, seed_workspace, db_session
) -> None:
    """Missing site_url/email is operator error — surface as 400, not 502."""
    _, raw, workspace = seed_workspace
    bad = Integration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        kind="confluence",
        config={},  # no site_url/email
        status="ok",
        secret_ciphertext=encrypt("x"),
    )
    db_session.add(bad)
    await db_session.flush()

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/confluence/spaces",
        headers=_auth(raw),
        params={"integration_id": str(bad.id)},
    )
    assert response.status_code == 400
    assert "site_url" in response.json()["detail"]
