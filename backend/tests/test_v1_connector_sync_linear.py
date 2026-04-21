"""Phase 7c — ``POST /buckets/{slug}/sync`` end-to-end via the Linear fetcher.

Mirrors ``test_v1_connector_sync_notion.py``. Exercises the real
Linear code path (dispatcher → ``fetch_linear_pages`` → Linear
GraphQL via a MockTransport) against the connector sync endpoint.

Covered:

- Happy path renders the issue into markdown and persists an article
  whose provenance includes the Linear issue_id + URL.
- Second sync with identical upstream returns ``skip``.
- Missing secret → 502 with a reconnect hint.
- Linear 401 → 502 so the operator knows to re-auth.
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


def _issue_payload(identifier: str) -> dict[str, Any]:
    return {
        "data": {
            "issue": {
                "id": "c2d4ac30-5a1b-4a45-9a6e-65f0b1c2a980",
                "identifier": identifier,
                "title": "Rebuild retriever with bucket scopes",
                "description": "## Goal\n\nSwitch retriever to bucket_articles only.\n",
                "url": f"https://linear.app/elmundi/issue/{identifier}/rebuild",
                "updatedAt": "2026-04-20T09:15:00.000Z",
                "state": {"name": "In Progress", "type": "started"},
                "assignee": {"name": "denys", "displayName": "denys"},
                "creator": {"name": "ksenia", "displayName": "ksenia"},
                "team": {"key": "ELM", "name": "ElMundi Core"},
                "priority": 2,
                "priorityLabel": "High",
                "labels": {"nodes": [{"name": "phase-5"}]},
            }
        }
    }


@pytest_asyncio.fixture
async def mock_linear_ok(request):
    identifier = getattr(request, "param", "ELM-42")

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == "https://api.linear.app/graphql":
            return httpx.Response(200, json=_issue_payload(identifier))
        return httpx.Response(500, json={"error": f"unexpected: {req.url}"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    set_http_client_override(client)
    try:
        yield identifier
    finally:
        set_http_client_override(None)
        await client.aclose()


@pytest_asyncio.fixture
async def mock_linear_401():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "unauthorized"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport)
    set_http_client_override(client)
    try:
        yield
    finally:
        set_http_client_override(None)
        await client.aclose()


@pytest_asyncio.fixture
async def linear_integration(db_session, seed_workspace) -> Integration:
    _, _, workspace = seed_workspace
    integration = Integration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        kind="linear",
        config={"linear_workspace_name": "ElMundi"},
        status="ok",
        secret_ciphertext=encrypt("lin_oauth_token_xyz"),
    )
    db_session.add(integration)
    await db_session.flush()
    return integration


@pytest_asyncio.fixture
async def linear_integration_no_secret(
    db_session, seed_workspace
) -> Integration:
    _, _, workspace = seed_workspace
    integration = Integration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        kind="linear",
        config={},
        status="ok",
        secret_ciphertext=None,
    )
    db_session.add(integration)
    await db_session.flush()
    return integration


async def _mint_bucket(
    db_session,
    workspace_id: uuid.UUID,
    integration: Integration,
    identifier: str,
) -> KnowledgeBucket:
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        slug=f"linear-{identifier.lower()}",
        name=f"Linear {identifier}",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.CONNECTOR_PROXY,
        source_ref={
            "integration_id": str(integration.id),
            "integration_kind": "linear",
            "resource_ref": {"issue_id": identifier},
        },
    )
    db_session.add(bucket)
    await db_session.flush()
    return bucket


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_ingests_real_linear_issue(
    v1_client,
    seed_workspace,
    linear_integration,
    mock_linear_ok,
    db_session,
) -> None:
    _, raw, workspace = seed_workspace
    identifier = mock_linear_ok
    bucket = await _mint_bucket(
        db_session, workspace.id, linear_integration, identifier
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

    assert f"# {identifier} · " in article.body_md
    assert "state: **In Progress**" in article.body_md
    assert "Connector fetcher is not wired" not in article.body_md

    prov = article.provenance or {}
    assert prov.get("kind") == "connector_proxy"
    assert prov.get("connector_kind") == "linear"
    assert prov.get("identifier") == identifier
    assert f"linear.app/elmundi/issue/{identifier}" in str(prov.get("url") or "")
    assert prov.get("resource_ref") == {"issue_id": identifier}


@pytest.mark.asyncio
async def test_resync_real_linear_issue_is_skip(
    v1_client,
    seed_workspace,
    linear_integration,
    mock_linear_ok,
    db_session,
) -> None:
    _, raw, workspace = seed_workspace
    identifier = mock_linear_ok
    bucket = await _mint_bucket(
        db_session, workspace.id, linear_integration, identifier
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
async def test_sync_linear_missing_secret_becomes_502(
    v1_client,
    seed_workspace,
    linear_integration_no_secret,
    db_session,
) -> None:
    _, raw, workspace = seed_workspace
    bucket = await _mint_bucket(
        db_session, workspace.id, linear_integration_no_secret, "ELM-404"
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/sync",
        headers=_auth(raw),
    )
    assert resp.status_code == 502, resp.text
    assert "readable access token" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_sync_linear_401_becomes_502(
    v1_client,
    seed_workspace,
    linear_integration,
    mock_linear_401,
    db_session,
) -> None:
    _, raw, workspace = seed_workspace
    bucket = await _mint_bucket(
        db_session, workspace.id, linear_integration, "ELM-5"
    )

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/sync",
        headers=_auth(raw),
    )
    assert resp.status_code == 502, resp.text
    assert "reconnect" in resp.json()["detail"].lower()
