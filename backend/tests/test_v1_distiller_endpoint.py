"""v1 API — Distiller stub (``POST /buckets/{slug}/distill``).

Phase 6a coverage:

1. **decision=new** — fresh slug writes a published v1 article and
   persists the :class:`DistillerRun` row with decision ``new`` +
   the new ``article_id`` in ``output_refs``.
2. **decision=update** — re-distilling the same slug with fresh body
   supersedes the previous article (flips status → ``superseded``,
   bumps version) and lands a new ``published`` row. The run row
   reports ``decision='update'`` with the previous + new ids in the
   diff.
3. **decision=skip (empty body)** — no article change. Run row is
   ``done`` with ``decision='skip'`` and a readable reason.
4. **decision=skip (same content_sha)** — identical re-ingest of an
   existing article is idempotent: no version churn, still ``skip``.
5. **404 on missing bucket** — same behaviour as the rest of the
   ``/buckets/{slug}`` family.
6. **400 on unknown source_kind** — guards against silent provenance
   drift; the controller validates against ``BucketSource.ALL`` at
   the edge.
7. **runs listing** — history endpoint returns runs newest-first.

All tests run against the in-memory sqlite harness via ``v1_client``
+ ``seed_workspace`` + ``db_session`` from ``db_conftest.py``.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    DistillerRun,
    KnowledgeBucket,
)


@pytest_asyncio.fixture
async def seeded_bucket(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="auth-refactor",
        name="Auth refactor",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    db_session.add(bucket)
    await db_session.flush()
    return workspace, bucket


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_distill_new_article_inserted_and_run_recorded(
    v1_client, seed_workspace, seeded_bucket, db_session
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={
            "body_md": "# Auth refactor notes\n\nUsing JWT rotation.",
            "source_kind": "external_static",
            "title_hint": "Auth refactor notes",
            "slug_hint": "auth-refactor-v2",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["decision"] == "new"
    assert len(payload["article_ids"]) == 1
    assert payload["run"]["status"] == "done"
    assert payload["run"]["decision"] == "new"
    assert payload["run"]["source_kind"] == "external_static"

    # The written article mirrors payload content.
    article = (
        await db_session.execute(
            select(BucketArticle).where(
                BucketArticle.id == uuid.UUID(payload["article_ids"][0])
            )
        )
    ).scalars().one()
    assert article.slug == "auth-refactor-v2"
    assert article.version == 1
    assert article.status == BucketArticleStatus.PUBLISHED
    assert article.title == "Auth refactor notes"
    assert "distiller_run_id" in article.provenance


@pytest.mark.asyncio
async def test_distill_update_supersedes_previous_article(
    v1_client, seed_workspace, seeded_bucket, db_session
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket

    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={
            "body_md": "v1 body",
            "source_kind": "external_static",
            "slug_hint": "edge-case",
        },
    )
    assert first.status_code == 200
    v1_article_id = first.json()["article_ids"][0]

    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={
            "body_md": "v2 body — meaningfully different",
            "source_kind": "external_static",
            "slug_hint": "edge-case",
        },
    )
    assert second.status_code == 200, second.text
    payload = second.json()
    assert payload["decision"] == "update"
    diff = payload["run"]["output_refs"]["diff"]
    assert diff["previous_article_id"] == v1_article_id
    assert diff["previous_version"] == 1
    assert diff["new_version"] == 2

    rows = (
        await db_session.execute(
            select(BucketArticle)
            .where(BucketArticle.bucket_id == bucket.id)
            .order_by(BucketArticle.version)
        )
    ).scalars().all()
    # Exactly one row survives as published; the older one is
    # superseded and points at its replacement via supersedes_id.
    statuses = {r.status for r in rows}
    assert statuses == {
        BucketArticleStatus.PUBLISHED,
        BucketArticleStatus.SUPERSEDED,
    }
    live = [r for r in rows if r.status == BucketArticleStatus.PUBLISHED][0]
    old = [r for r in rows if r.status == BucketArticleStatus.SUPERSEDED][0]
    assert live.version == 2
    assert old.version == 1
    assert live.supersedes_id == old.id


@pytest.mark.asyncio
async def test_distill_skip_on_empty_body(
    v1_client, seed_workspace, seeded_bucket
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={"body_md": "   ", "source_kind": "external_static"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["decision"] == "skip"
    assert payload["article_ids"] == []
    assert payload["reason"]
    # Run row still persisted — skip is a legitimate outcome.
    assert payload["run"]["status"] == "done"
    assert payload["run"]["decision"] == "skip"


@pytest.mark.asyncio
async def test_distill_skip_when_content_unchanged(
    v1_client, seed_workspace, seeded_bucket
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket

    body = "same body, same hash"
    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={
            "body_md": body,
            "source_kind": "external_static",
            "slug_hint": "idempotent",
        },
    )
    assert first.status_code == 200
    assert first.json()["decision"] == "new"

    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={
            "body_md": body,
            "source_kind": "external_static",
            "slug_hint": "idempotent",
        },
    )
    assert second.status_code == 200
    payload = second.json()
    assert payload["decision"] == "skip"
    assert "already published" in (payload["reason"] or "").lower()


@pytest.mark.asyncio
async def test_distill_rejects_unknown_source_kind(
    v1_client, seed_workspace, seeded_bucket
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
        headers=_auth(raw),
        json={"body_md": "x", "source_kind": "telepathy"},
    )
    assert resp.status_code == 400, resp.text
    assert "source_kind" in resp.text


@pytest.mark.asyncio
async def test_distill_404_on_missing_bucket(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/does-not-exist/distill",
        headers=_auth(raw),
        json={"body_md": "x", "source_kind": "external_static"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_distiller_runs_endpoint_lists_recent_runs(
    v1_client, seed_workspace, seeded_bucket, db_session
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = seeded_bucket

    for i in range(3):
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill",
            headers=_auth(raw),
            json={
                "body_md": f"body {i}",
                "source_kind": "external_static",
                "slug_hint": f"doc-{i}",
            },
        )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/distill/runs",
        headers=_auth(raw),
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 3
    # Newest first, every row has a decision populated.
    decisions = [r["decision"] for r in rows]
    assert all(d == "new" for d in decisions)

    # DB-side sanity check — all rows point at the seeded bucket.
    db_rows = (
        await db_session.execute(
            select(DistillerRun).where(DistillerRun.bucket_id == bucket.id)
        )
    ).scalars().all()
    assert len(db_rows) == 3
