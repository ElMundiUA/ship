"""Phase 7c — ``POST /buckets/{slug}/sync`` end-to-end via the Notion fetcher.

The Phase 7b tests in ``test_v1_connector_bucket.py`` cover the stub
fallback path (unsupported resource_ref shape). These tests exercise
the *real* connector code path: the endpoint calls
``fetch_connector_pages`` which dispatches to the Notion fetcher
which (via a ``MockTransport``) returns a synthetic page. Useful
signal:

- Article body is the rendered Notion markdown (not the stub banner).
- ``provenance`` includes the fetched ``page_id`` + ``url``, so
  downstream summary/chat surfaces can deep-link back.
- Re-syncing the same page collapses to ``skip`` via content-sha
  dedup — same semantics as Phase 7b, now hitting the real fetcher.
- A supported shape with an integration that has no decryptable
  secret surfaces as 502 (the operator needs to reconnect).
- Upstream 404 (page not shared with the bot) is 502 with a
  re-share hint, not a 500.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import encrypt
from backend.app.services.connectors import set_http_client_override


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


# ---------------------------------------------------------------------------
# Mock Notion transport
# ---------------------------------------------------------------------------


def _page_payload(page_id: str, title: str) -> dict[str, Any]:
    return {
        "object": "page",
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "last_edited_time": "2026-04-20T10:00:00.000Z",
        "properties": {
            "Name": {
                "type": "title",
                "title": [
                    {
                        "plain_text": title,
                        "annotations": {},
                        "href": None,
                    }
                ],
            }
        },
    }


def _blocks_payload(title_text: str) -> dict[str, Any]:
    return {
        "object": "list",
        "results": [
            {
                "object": "block",
                "id": uuid.uuid4().hex,
                "type": "heading_1",
                "heading_1": {
                    "rich_text": [
                        {
                            "plain_text": title_text,
                            "annotations": {},
                            "href": None,
                        }
                    ]
                },
            },
            {
                "object": "block",
                "id": uuid.uuid4().hex,
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {
                            "plain_text": "Step one: verify the thing.",
                            "annotations": {},
                            "href": None,
                        }
                    ]
                },
            },
        ],
        "has_more": False,
        "next_cursor": None,
    }


def _ok_handler(page_id: str, *, title: str = "Runbook: DB restore"):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/v1/pages/{page_id}":
            return httpx.Response(200, json=_page_payload(page_id, title))
        if path == f"/v1/blocks/{page_id}/children":
            return httpx.Response(200, json=_blocks_payload(title))
        return httpx.Response(500, json={"error": f"unexpected: {path}"})

    return handler


def _not_shared_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(404, json={"code": "object_not_found"})


@pytest_asyncio.fixture
async def mock_notion_ok(request):
    """Install a MockTransport that answers Notion calls for ``page_id``."""

    page_id = getattr(request, "param", "page-ok")
    transport = httpx.MockTransport(_ok_handler(page_id))
    client = httpx.AsyncClient(transport=transport)
    set_http_client_override(client)
    try:
        yield page_id
    finally:
        set_http_client_override(None)
        await client.aclose()


@pytest_asyncio.fixture
async def mock_notion_404():
    transport = httpx.MockTransport(_not_shared_handler)
    client = httpx.AsyncClient(transport=transport)
    set_http_client_override(client)
    try:
        yield
    finally:
        set_http_client_override(None)
        await client.aclose()


# ---------------------------------------------------------------------------
# Notion integration + bucket fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def notion_integration(db_session, seed_workspace) -> Integration:
    _, _, workspace = seed_workspace
    integration = Integration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        kind="notion",
        config={"notion_workspace_name": "Example"},
        status="ok",
        secret_ciphertext=encrypt("notion_access_token_xyz"),
    )
    db_session.add(integration)
    await db_session.flush()
    return integration


@pytest_asyncio.fixture
async def notion_integration_no_secret(db_session, seed_workspace) -> Integration:
    _, _, workspace = seed_workspace
    integration = Integration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        kind="notion",
        config={},
        status="ok",
        secret_ciphertext=None,
    )
    db_session.add(integration)
    await db_session.flush()
    return integration


async def _mint_bucket(
    db_session, workspace_id: uuid.UUID, integration: Integration, page_id: str
) -> KnowledgeBucket:
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        slug=f"notion-page-{page_id[:6]}",
        name="Notion page",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.CONNECTOR_PROXY,
        source_ref={
            "integration_id": str(integration.id),
            "integration_kind": "notion",
            "resource_ref": {"page_id": page_id},
        },
    )
    db_session.add(bucket)
    await db_session.flush()
    return bucket


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_ingests_real_notion_page(
    v1_client,
    seed_workspace,
    notion_integration,
    mock_notion_ok,
    db_session,
) -> None:
    """Happy path — sync resolves via fetcher and stores rendered markdown."""

    _, raw, workspace = seed_workspace
    page_id = mock_notion_ok
    bucket = await _mint_bucket(
        db_session, workspace.id, notion_integration, page_id
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/sync",
        headers=_auth(raw),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["decision"] == "new"
    assert len(payload["article_ids"]) == 1

    article = (
        await db_session.execute(
            select(BucketArticle).where(
                BucketArticle.id == uuid.UUID(payload["article_ids"][0])
            )
        )
    ).scalars().one()

    # Body should be the rendered markdown, NOT the stub banner.
    assert "# Runbook: DB restore" in article.body_md
    assert "Step one: verify the thing." in article.body_md
    assert "Connector fetcher is not wired" not in article.body_md

    prov = article.provenance or {}
    assert prov.get("kind") == "connector_proxy"
    assert prov.get("connector_kind") == "notion"
    assert prov.get("page_id") == page_id
    assert f"notion.so/{page_id}" in str(prov.get("url") or "")
    assert prov.get("resource_ref") == {"page_id": page_id}


@pytest.mark.asyncio
async def test_resync_real_notion_page_is_skip(
    v1_client,
    seed_workspace,
    notion_integration,
    mock_notion_ok,
    db_session,
) -> None:
    _, raw, workspace = seed_workspace
    page_id = mock_notion_ok
    bucket = await _mint_bucket(
        db_session, workspace.id, notion_integration, page_id
    )

    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/sync",
        headers=_auth(raw),
    )
    assert first.status_code == 200
    assert first.json()["decision"] == "new"

    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/sync",
        headers=_auth(raw),
    )
    assert second.status_code == 200, second.text
    assert second.json()["decision"] == "skip"


@pytest.mark.asyncio
async def test_sync_config_error_becomes_502(
    v1_client,
    seed_workspace,
    notion_integration_no_secret,
    db_session,
) -> None:
    """Supported shape + missing secret → 502 with a reconnect hint.

    Guards against a silently-broken integration producing stub
    articles forever. If the shape *is* supported we want the
    operator to notice and reconnect; if the shape isn't supported
    we quietly fall back (tested in the Phase 7b suite).
    """

    _, raw, workspace = seed_workspace
    bucket = await _mint_bucket(
        db_session, workspace.id, notion_integration_no_secret, "page-blocked"
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/sync",
        headers=_auth(raw),
    )
    assert resp.status_code == 502, resp.text
    assert "readable access token" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_sync_notion_404_becomes_502(
    v1_client,
    seed_workspace,
    notion_integration,
    mock_notion_404,
    db_session,
) -> None:
    _, raw, workspace = seed_workspace
    bucket = await _mint_bucket(
        db_session, workspace.id, notion_integration, "page-not-shared"
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/sync",
        headers=_auth(raw),
    )
    assert resp.status_code == 502, resp.text
    assert "shared with the page" in resp.json()["detail"]
