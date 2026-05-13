"""Wizard picker — `/v1/workspaces/{ws}/knowledge/sources/notion/resources`.

Stubs the Notion `/search` call via monkeypatch on the route's
`_notion_search` helper (same seam pattern as the OAuth tests use for
`exchange_code_for_token`). No live Notion calls.
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import encrypt


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def _seed_notion_integration(db_session, workspace_id, *, secret: str | None = "ntn_test") -> Integration:
    row = Integration(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        kind="notion",
        config={"notion_workspace_name": "Acme"},
        status="ok",
        secret_ciphertext=encrypt(secret) if secret else None,
    )
    db_session.add(row)
    return row


@pytest.mark.asyncio
async def test_list_notion_resources_returns_picker_items(
    v1_client, seed_workspace, db_session, monkeypatch
) -> None:
    _, raw, workspace = seed_workspace
    integration = _seed_notion_integration(db_session, workspace.id)
    await db_session.flush()

    captured: dict = {}

    async def _fake_search(*, token, body, workspace_id):
        captured["token"] = token
        captured["body"] = body
        captured["workspace_id"] = workspace_id
        return {
            "results": [
                {
                    "object": "page",
                    "id": "page-uuid-1",
                    "url": "https://notion.so/page-1",
                    "last_edited_time": "2026-04-30T10:00:00.000Z",
                    "icon": {"type": "emoji", "emoji": "📘"},
                    "parent": {"type": "workspace"},
                    "properties": {
                        "Name": {
                            "type": "title",
                            "title": [{"plain_text": "Onboarding handbook"}],
                        }
                    },
                },
                {
                    "object": "database",
                    "id": "db-uuid-1",
                    "url": "https://notion.so/db-1",
                    "title": [{"plain_text": "Customer FAQ"}],
                    "parent": {"type": "page_id", "page_id": "parent-1"},
                },
                {"object": "block", "id": "ignored"},  # filtered out
            ],
            "next_cursor": "cursor-2",
            "has_more": True,
        }

    monkeypatch.setattr(
        "backend.app.api.v1.routes.knowledge_import_sources._notion_search",
        _fake_search,
    )

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/notion/resources",
        headers=_auth(raw),
        params={"integration_id": str(integration.id), "q": "onboarding", "type": "any"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["next_cursor"] == "cursor-2"
    assert body["has_more"] is True
    assert [item["id"] for item in body["items"]] == ["page-uuid-1", "db-uuid-1"]
    page_item = body["items"][0]
    assert page_item["type"] == "page"
    assert page_item["title"] == "Onboarding handbook"
    assert page_item["icon"] == "📘"
    assert page_item["parent_path"] == "Workspace"
    db_item = body["items"][1]
    assert db_item["type"] == "database"
    assert db_item["title"] == "Customer FAQ"
    assert db_item["parent_path"] == "Page"

    # Forwarded params: q ends up as `query`, type=any drops the filter,
    # token came from the encrypted integration secret.
    assert captured["token"] == "ntn_test"
    assert captured["body"]["query"] == "onboarding"
    assert "filter" not in captured["body"]
    assert captured["body"]["page_size"] > 0


@pytest.mark.asyncio
async def test_list_notion_resources_defaults_to_pages_only(
    v1_client, seed_workspace, db_session, monkeypatch
) -> None:
    """Default ``type=page`` filter matches what the connector can fetch."""
    _, raw, workspace = seed_workspace
    integration = _seed_notion_integration(db_session, workspace.id)
    await db_session.flush()

    captured = {}

    async def _fake_search(*, token, body, workspace_id):
        captured["body"] = body
        return {"results": [], "next_cursor": None, "has_more": False}

    monkeypatch.setattr(
        "backend.app.api.v1.routes.knowledge_import_sources._notion_search",
        _fake_search,
    )

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/notion/resources",
        headers=_auth(raw),
        params={"integration_id": str(integration.id)},
    )
    assert response.status_code == 200
    assert captured["body"]["filter"] == {"value": "page", "property": "object"}


@pytest.mark.asyncio
async def test_list_notion_resources_404_when_integration_kind_mismatches(
    v1_client, seed_workspace, db_session
) -> None:
    """A non-Notion integration row (e.g. linear) must 404 — the route
    refuses to leak any other provider's token via this path."""
    _, raw, workspace = seed_workspace
    wrong_kind = Integration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        kind="linear",
        config={},
        status="ok",
        secret_ciphertext=encrypt("x"),
    )
    db_session.add(wrong_kind)
    await db_session.flush()

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/notion/resources",
        headers=_auth(raw),
        params={"integration_id": str(wrong_kind.id)},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_notion_resources_400_when_secret_missing(
    v1_client, seed_workspace, db_session
) -> None:
    _, raw, workspace = seed_workspace
    integration = _seed_notion_integration(db_session, workspace.id, secret=None)
    await db_session.flush()

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/notion/resources",
        headers=_auth(raw),
        params={"integration_id": str(integration.id)},
    )
    assert response.status_code == 400
    assert "reconnect" in response.json()["detail"].lower()
