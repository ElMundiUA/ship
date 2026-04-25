"""v1 API — connector-proxy bucket create + sync (Phase 7b).

Covers the two new surfaces that close the connector-bucket loop:

1. ``POST /v1/workspaces/{ws}/buckets`` accepts ``source_kind=
   connector_proxy`` together with a ``source_ref`` that references
   an existing Integration row. Without a valid integration the
   route rejects the payload; with one it persists the normalized
   ``source_ref`` (integration_id + integration_kind + resource_ref).
2. ``POST /v1/workspaces/{ws}/buckets/{slug}/sync`` triggers the
   Distiller via ``ingest_connector_page`` with a deterministic
   stub body, records a ``DistillerRun``, and is gated to
   connector-proxy buckets only.

All tests pin ``classifier=stub`` (either via the request payload
or because the route hard-wires stub for sync) so the verdicts stay
deterministic on CI boxes with an ``OPENAI_API_KEY`` exported.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketScope,
    BucketSource,
    DistillerRun,
    KnowledgeBucket,
)
from backend.app.db.models.tenancy import Integration


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


@pytest_asyncio.fixture
async def seeded_integration(db_session, seed_workspace):
    """Mint a Confluence integration row the bucket can point at."""

    _, _, workspace = seed_workspace
    integration = Integration(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        kind="notion",
        config={"workspace_url": "https://example.notion.site"},
        status="ok",
    )
    db_session.add(integration)
    await db_session.flush()
    return workspace, integration


# ---------------------------------------------------------------------------
# POST /buckets (connector_proxy)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_connector_bucket_persists_source_ref(
    v1_client, seed_workspace, seeded_integration, db_session
) -> None:
    _, raw, workspace = seed_workspace
    _, integration = seeded_integration

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets",
        headers=_auth(raw),
        json={
            "slug": "runbooks-confluence",
            "name": "Runbooks (Notion)",
            "description": "Live mirror of the ops runbooks space",
            "scope_kind": BucketScope.WORKSPACE,
            "source_kind": BucketSource.CONNECTOR_PROXY,
            "source_ref": {
                "integration_id": str(integration.id),
                "resource_ref": {"database_id": "abc-123"},
            },
        },
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert payload["source_kind"] == BucketSource.CONNECTOR_PROXY
    assert payload["scope_kind"] == BucketScope.WORKSPACE
    assert payload["source_ref"]["integration_id"] == str(integration.id)
    assert payload["source_ref"]["integration_kind"] == "notion"
    assert payload["source_ref"]["resource_ref"] == {"database_id": "abc-123"}

    # And the stored row must match — we don't want the API to
    # silently drop fields on the way in.
    stored = (
        await db_session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.id == uuid.UUID(payload["id"])
            )
        )
    ).scalars().one()
    assert stored.source_kind == BucketSource.CONNECTOR_PROXY
    assert stored.source_ref["integration_id"] == str(integration.id)
    assert stored.source_ref["integration_kind"] == "notion"


@pytest.mark.asyncio
async def test_create_connector_bucket_requires_integration_id(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets",
        headers=_auth(raw),
        json={
            "slug": "missing-ref",
            "name": "No integration",
            "source_kind": BucketSource.CONNECTOR_PROXY,
        },
    )
    assert resp.status_code == 400
    assert "integration_id" in resp.text.lower()


@pytest.mark.asyncio
async def test_create_connector_bucket_rejects_other_workspace_integration(
    v1_client, seed_workspace, db_session
) -> None:
    """Cross-tenant safety: bucket cannot point at another workspace's integration."""

    _, raw, workspace = seed_workspace

    # Mint an Integration in a fake workspace by creating a second
    # workspace row so the FK holds but the bucket create rejects it.
    from backend.app.db.models.tenancy import Org, Workspace

    other_org = Org(id=uuid.uuid4(), name="Other org", slug=f"other-{uuid.uuid4().hex[:8]}")
    db_session.add(other_org)
    await db_session.flush()
    other_ws = Workspace(
        id=uuid.uuid4(),
        org_id=other_org.id,
        slug=f"other-{uuid.uuid4().hex[:8]}",
        name="Other ws",
    )
    db_session.add(other_ws)
    await db_session.flush()
    foreign = Integration(
        id=uuid.uuid4(),
        workspace_id=other_ws.id,
        kind="linear",
        config={},
        status="ok",
    )
    db_session.add(foreign)
    await db_session.flush()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets",
        headers=_auth(raw),
        json={
            "slug": "foreign-integration",
            "name": "Foreign integration",
            "source_kind": BucketSource.CONNECTOR_PROXY,
            "source_ref": {"integration_id": str(foreign.id)},
        },
    )
    assert resp.status_code == 400
    assert "not found in this workspace" in resp.text.lower()


@pytest.mark.asyncio
async def test_create_connector_bucket_rejects_invalid_uuid(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets",
        headers=_auth(raw),
        json={
            "slug": "bad-uuid",
            "name": "Bad uuid",
            "source_kind": BucketSource.CONNECTOR_PROXY,
            "source_ref": {"integration_id": "not-a-uuid"},
        },
    )
    assert resp.status_code == 400
    assert "uuid" in resp.text.lower()


# ---------------------------------------------------------------------------
# POST /buckets/{slug}/sync
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def connector_bucket(db_session, seeded_integration):
    workspace, integration = seeded_integration
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="notion-space",
        name="Notion space",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.CONNECTOR_PROXY,
        source_ref={
            "integration_id": str(integration.id),
            "integration_kind": integration.kind,
            "resource_ref": {"database_id": "db-42"},
        },
    )
    db_session.add(bucket)
    await db_session.flush()
    return workspace, bucket


@pytest.mark.asyncio
async def test_sync_rejects_unsupported_resource_ref(
    v1_client, seed_workspace, connector_bucket, db_session
) -> None:
    _, raw, _ = seed_workspace
    workspace, bucket = connector_bucket

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/sync",
        headers=_auth(raw),
    )
    assert resp.status_code == 400, resp.text
    assert "returned no pages" in resp.text
    article_count = (
        await db_session.execute(
            select(BucketArticle).where(BucketArticle.bucket_id == bucket.id)
        )
    ).scalars().all()
    assert article_count == []


@pytest.mark.asyncio
async def test_repeated_unsupported_sync_stays_rejected(
    v1_client, seed_workspace, connector_bucket
) -> None:
    _, raw, _ = seed_workspace
    workspace, bucket = connector_bucket

    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/sync",
        headers=_auth(raw),
    )
    assert first.status_code == 400

    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/sync",
        headers=_auth(raw),
    )
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_sync_rejects_non_connector_bucket(
    v1_client, seed_workspace, db_session
) -> None:
    _, raw, workspace = seed_workspace
    # Mint a plain external_static bucket — sync should 400 because
    # the route is scoped to connector_proxy source kinds only.
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="not-a-connector",
        name="Not a connector",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
    )
    db_session.add(bucket)
    await db_session.flush()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/sync",
        headers=_auth(raw),
    )
    assert resp.status_code == 400
    assert "connector_proxy" in resp.text.lower()


@pytest.mark.asyncio
async def test_sync_404_on_missing_bucket(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/ghost/sync",
        headers=_auth(raw),
    )
    assert resp.status_code == 404
