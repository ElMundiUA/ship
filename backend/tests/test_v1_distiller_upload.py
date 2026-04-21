"""v1 API — external-static upload (``POST /buckets/{slug}/upload``).

Phase 6c / 7 minimal surface. We pin ``classifier=stub`` so the tests
stay deterministic on CI boxes that have an ``OPENAI_API_KEY``
exported.

Coverage:

1. ``new`` article from a valid .md file.
2. ``skip`` on replay of the same bytes (content_sha dedupe).
3. 400 on oversize file.
4. 400 on wrong content-type *and* wrong extension.
5. 400 on non-UTF-8 bytes.
6. 404 on unknown bucket.
"""

from __future__ import annotations

import io
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)


@pytest_asyncio.fixture
async def upload_bucket(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="upload-target",
        name="Upload target",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
    )
    db_session.add(bucket)
    await db_session.flush()
    return workspace, bucket


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_upload_new_article(
    v1_client, seed_workspace, upload_bucket, db_session
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = upload_bucket

    body = b"# Runbook\n\nStep 1: breathe. Step 2: page Alice."
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/upload",
        headers=_auth(raw),
        files={"file": ("runbook.md", io.BytesIO(body), "text/markdown")},
        data={"classifier": "stub"},
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["decision"] == "new"
    assert payload["classifier"] == "stub"

    article = (
        await db_session.execute(
            select(BucketArticle).where(
                BucketArticle.id == uuid.UUID(payload["article_ids"][0])
            )
        )
    ).scalars().one()
    assert article.slug == "runbook"
    prov = article.provenance or {}
    assert prov.get("kind") == "external_static_upload"
    assert prov.get("filename") == "runbook.md"


@pytest.mark.asyncio
async def test_upload_replay_is_skipped(
    v1_client, seed_workspace, upload_bucket
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = upload_bucket

    body = b"# Same again\n\nIdentical bytes, identical hash."
    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/upload",
        headers=_auth(raw),
        files={"file": ("same.md", io.BytesIO(body), "text/markdown")},
        data={"classifier": "stub"},
    )
    assert first.status_code == 200
    assert first.json()["decision"] == "new"

    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/upload",
        headers=_auth(raw),
        files={"file": ("same.md", io.BytesIO(body), "text/markdown")},
        data={"classifier": "stub"},
    )
    assert second.status_code == 200
    assert second.json()["decision"] == "skip"


@pytest.mark.asyncio
async def test_upload_rejects_oversize_file(
    v1_client, seed_workspace, upload_bucket
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = upload_bucket

    too_big = b"x" * 1_500_000  # 1.5 MiB, over the 1 MiB cap
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/upload",
        headers=_auth(raw),
        files={"file": ("huge.md", io.BytesIO(too_big), "text/markdown")},
        data={"classifier": "stub"},
    )
    assert resp.status_code == 400
    assert "too large" in resp.text.lower()


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_content_type(
    v1_client, seed_workspace, upload_bucket
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = upload_bucket

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/upload",
        headers=_auth(raw),
        files={"file": ("image.png", io.BytesIO(b"\x89PNG"), "image/png")},
        data={"classifier": "stub"},
    )
    assert resp.status_code == 400
    assert "content type" in resp.text.lower()


@pytest.mark.asyncio
async def test_upload_rejects_non_utf8_bytes(
    v1_client, seed_workspace, upload_bucket
) -> None:
    _, raw, workspace = seed_workspace
    _, bucket = upload_bucket

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/{bucket.slug}/upload",
        headers=_auth(raw),
        files={
            "file": (
                "latin1.md",
                io.BytesIO(b"\xff\xfe\xfd invalid utf-8"),
                "text/markdown",
            )
        },
        data={"classifier": "stub"},
    )
    assert resp.status_code == 400
    assert "utf-8" in resp.text.lower()


@pytest.mark.asyncio
async def test_upload_404_on_missing_bucket(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets/does-not-exist/upload",
        headers=_auth(raw),
        files={"file": ("x.md", io.BytesIO(b"x"), "text/markdown")},
        data={"classifier": "stub"},
    )
    assert resp.status_code == 404
