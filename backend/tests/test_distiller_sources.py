"""Unit tests for :mod:`backend.app.services.distiller_sources`.

Phase 6c coverage:

1. ``ensure_bucket`` — creates a repo-scoped bucket first time,
   returns the existing row on the second call, rejects wrong
   carrier combinations.
2. ``ingest_pr_merge`` — builds a deterministic ``pr-<number>``
   article under a repo-scoped bucket with PR metadata in the
   provenance; idempotent on replay (``decision=skip``); ignores
   install PRs; returns ``None`` when the payload isn't a merged
   PR.
3. ``ingest_external_static_upload`` — records filename in
   provenance and slug-derives from filename.

All tests pin ``classifier=classify_stub`` so CI is deterministic
even when ``OPENAI_API_KEY`` is present in the shell.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.services.distiller import classify_stub
from backend.app.services.distiller_sources import (
    PR_SUMMARIES_SLUG,
    ensure_bucket,
    ingest_external_static_upload,
    ingest_pr_merge,
)


async def _seed_repo(db_session, workspace):
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=111_222,
        account_id=1,
        account_login="acme",
        account_type="Organization",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=5_000_001,
        full_name="acme/pr-ingest",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/pr-ingest",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


def _merged_pr_payload(
    *,
    number: int = 101,
    title: str = "Refactor rate limiter",
    body: str = "Switch to token-bucket. Caps at 200 RPS.",
    head_ref: str = "feature/rate-limit",
) -> dict:
    return {
        "action": "closed",
        "pull_request": {
            "id": 9_999_000 + number,
            "number": number,
            "title": title,
            "body": body,
            "state": "closed",
            "merged": True,
            "user": {"login": "octo"},
            "merged_by": {"login": "reviewer"},
            "merged_at": "2026-04-21T10:00:00Z",
            "html_url": f"https://github.com/acme/pr-ingest/pull/{number}",
            "head": {"ref": head_ref},
            "base": {"ref": "main"},
        },
    }


# ---------------------------------------------------------------------------
# ensure_bucket
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_bucket_creates_then_reuses(db_session, seed_workspace):
    _, _, workspace = seed_workspace
    repo = await _seed_repo(db_session, workspace)

    first = await ensure_bucket(
        db_session,
        workspace_id=workspace.id,
        slug=PR_SUMMARIES_SLUG,
        name="PRs",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.EXTERNAL_STATIC,
        repo_id=repo.id,
    )
    assert first.scope_kind == BucketScope.REPO
    assert first.repo_id == repo.id
    assert first.source_kind == BucketSource.EXTERNAL_STATIC

    second = await ensure_bucket(
        db_session,
        workspace_id=workspace.id,
        slug=PR_SUMMARIES_SLUG,
        name="PRs",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.EXTERNAL_STATIC,
        repo_id=repo.id,
    )
    assert second.id == first.id

    # Only one row landed.
    rows = (
        await db_session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace.id,
                KnowledgeBucket.slug == PR_SUMMARIES_SLUG,
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_ensure_bucket_rejects_missing_carrier(
    db_session, seed_workspace
):
    _, _, workspace = seed_workspace
    with pytest.raises(ValueError, match="repo-scoped"):
        await ensure_bucket(
            db_session,
            workspace_id=workspace.id,
            slug="broken",
            name="Broken",
            scope_kind=BucketScope.REPO,
        )


@pytest.mark.asyncio
async def test_ensure_bucket_rejects_workspace_with_carrier(
    db_session, seed_workspace
):
    _, _, workspace = seed_workspace
    repo = await _seed_repo(db_session, workspace)
    with pytest.raises(ValueError, match="workspace-scoped"):
        await ensure_bucket(
            db_session,
            workspace_id=workspace.id,
            slug="no-carriers",
            name="Workspace bucket",
            scope_kind=BucketScope.WORKSPACE,
            repo_id=repo.id,
        )


# ---------------------------------------------------------------------------
# ingest_pr_merge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_pr_merge_creates_repo_bucket_and_article(
    db_session, seed_workspace
):
    _, _, workspace = seed_workspace
    repo = await _seed_repo(db_session, workspace)

    outcome = await ingest_pr_merge(
        db_session,
        workspace_id=workspace.id,
        repo=repo,
        payload=_merged_pr_payload(number=101),
        classifier=classify_stub,
    )
    assert outcome is not None
    assert outcome.decision == "new"
    assert outcome.classifier == "stub"
    assert len(outcome.article_ids) == 1

    # Bucket landed at repo scope, slug ``pr-summaries``.
    bucket = (
        await db_session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace.id,
                KnowledgeBucket.slug == PR_SUMMARIES_SLUG,
                KnowledgeBucket.repo_id == repo.id,
            )
        )
    ).scalars().one()
    assert bucket.scope_kind == BucketScope.REPO

    # Article has pr-<n> slug and provenance carries the webhook fields.
    article = (
        await db_session.execute(
            select(BucketArticle).where(
                BucketArticle.id == outcome.article_ids[0]
            )
        )
    ).scalars().one()
    assert article.slug == "pr-101"
    assert article.status == BucketArticleStatus.PUBLISHED
    prov = article.provenance or {}
    assert prov.get("kind") == "pr_merged"
    assert prov.get("pr_number") == 101
    assert prov.get("repo_full_name") == "acme/pr-ingest"
    assert "pull/101" in (prov.get("html_url") or "")


@pytest.mark.asyncio
async def test_ingest_pr_merge_idempotent_on_replay(
    db_session, seed_workspace
):
    """GitHub replays webhook deliveries. The Distiller must dedupe via
    content_sha so the bucket doesn't balloon on every retry."""
    _, _, workspace = seed_workspace
    repo = await _seed_repo(db_session, workspace)

    payload = _merged_pr_payload(number=202, body="First delivery.")
    first = await ingest_pr_merge(
        db_session,
        workspace_id=workspace.id,
        repo=repo,
        payload=payload,
        classifier=classify_stub,
    )
    assert first is not None and first.decision == "new"

    replay = await ingest_pr_merge(
        db_session,
        workspace_id=workspace.id,
        repo=repo,
        payload=payload,  # same bytes → same hash
        classifier=classify_stub,
    )
    assert replay is not None
    assert replay.decision == "skip"

    articles = (
        await db_session.execute(
            select(BucketArticle).where(BucketArticle.slug == "pr-202")
        )
    ).scalars().all()
    # One row, still the original v1.
    assert len(articles) == 1
    assert articles[0].version == 1


@pytest.mark.asyncio
async def test_ingest_pr_merge_skips_unmerged_payload(
    db_session, seed_workspace
):
    _, _, workspace = seed_workspace
    repo = await _seed_repo(db_session, workspace)

    payload = _merged_pr_payload(number=303)
    payload["pull_request"]["merged"] = False

    outcome = await ingest_pr_merge(
        db_session,
        workspace_id=workspace.id,
        repo=repo,
        payload=payload,
        classifier=classify_stub,
    )
    assert outcome is None

    # No bucket minted for an unmerged PR.
    buckets = (
        await db_session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace.id
            )
        )
    ).scalars().all()
    assert buckets == []


@pytest.mark.asyncio
async def test_ingest_pr_merge_skips_ship_install_pr(
    db_session, seed_workspace
):
    """Ship's own install PRs are plumbing; they'd pollute the bucket
    with noise about the Ship bot itself. Adapter filters them out."""
    _, _, workspace = seed_workspace
    repo = await _seed_repo(db_session, workspace)

    outcome = await ingest_pr_merge(
        db_session,
        workspace_id=workspace.id,
        repo=repo,
        payload=_merged_pr_payload(
            number=1, head_ref="ship/install-abc123"
        ),
        classifier=classify_stub,
    )
    assert outcome is None


# ---------------------------------------------------------------------------
# ingest_external_static_upload
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_external_static_upload_uses_filename(
    db_session, seed_workspace
):
    _, _, workspace = seed_workspace
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="ops-runbooks",
        name="Ops runbooks",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
    )
    db_session.add(bucket)
    await db_session.flush()

    outcome = await ingest_external_static_upload(
        db_session,
        workspace_id=workspace.id,
        bucket=bucket,
        actor_user_id=None,
        filename="oncall-handbook.md",
        content_type="text/markdown",
        body_md="# On-call handbook\n\nEscalation paths, SLOs, ...",
        classifier=classify_stub,
    )
    assert outcome.decision == "new"
    article = (
        await db_session.execute(
            select(BucketArticle).where(
                BucketArticle.id == outcome.article_ids[0]
            )
        )
    ).scalars().one()
    # Slug comes from the filename base.
    assert article.slug == "oncall-handbook"
    assert article.title == "oncall-handbook.md"
    prov = article.provenance or {}
    assert prov.get("kind") == "external_static_upload"
    assert prov.get("filename") == "oncall-handbook.md"
    assert prov.get("content_type") == "text/markdown"
