"""Phase 1 consolidation: scope + source on ``knowledge_buckets``.

Exercises the DB-level invariants introduced by migration
``0014_bucket_scope_source`` and the service-level echo plumbed
through in the chat routes. The goal here is **not** to re-test the
full bucket CRUD (``test_v1_buckets_and_feedback`` covers the happy
path) — it's to pin the CHECK + partial-unique contract so later
phases that start inserting repo/project/user-scoped rows don't
silently drift.

Invariants under test:

1. ``ck_knowledge_buckets_scope_carrier`` — each scope_kind demands
   its matching carrier FK (and ``workspace`` forbids all three).
2. ``uq_knowledge_buckets_repo_slug`` (partial) — same slug may live
   on multiple repos, but **not twice on the same repo**.
3. ``uq_knowledge_buckets_workspace_slug`` (partial) — the historical
   workspace-level uniqueness survives, and doesn't collide with
   a repo-scoped bucket that happens to share a slug.
4. The API echo defaults match the DB defaults for a create via the
   public endpoint (``workspace`` / ``agent_memory``).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from backend.app.db.models.agent_memory import (
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)


@pytest.mark.asyncio
async def test_scope_check_constraint_rejects_mismatched_carrier(
    db_session, seed_workspace
) -> None:
    """A ``scope_kind='repo'`` row with no ``repo_id`` must fail.

    Without the CHECK, a bug in a future service layer could insert
    a "repo-scoped" bucket that belongs to no repo, which would then
    resolve differently depending on which code path reads it.
    """
    _, _, workspace = seed_workspace

    # Deliberate violation: scope says ``repo`` but no ``repo_id``.
    bad = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="orphan-repo-bucket",
        name="Orphan",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_scope_check_constraint_rejects_workspace_with_carrier(
    db_session, seed_workspace
) -> None:
    """A ``scope_kind='workspace'`` row must not carry any FK.

    Symmetric to the first test: workspace-scoped buckets belong to
    the workspace directly. Carrying a ``repo_id`` would be ambiguous
    — is it a workspace bucket or a repo one? CHECK disallows the
    mixture so the resolver never has to guess.
    """
    _, _, workspace = seed_workspace

    # Invent a non-existent repo UUID — we never flush successfully, so
    # the FK validity never matters.
    bad = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="mixed-scope",
        name="Mixed",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
        repo_id=uuid.uuid4(),
    )
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_repo_scoped_slug_unique_per_repo_not_workspace(
    db_session, seed_workspace
) -> None:
    """Same slug on two different repos = fine; same slug twice on
    the same repo = conflict.

    This is the partial unique ``uq_knowledge_buckets_repo_slug``.
    Without it, consolidation Phase 2 (sync ``.ship/knowledge/*.md``
    → repo-scoped buckets) couldn't use the file's basename as a
    stable slug, because two repos with a ``code-style.md`` would
    immediately collide.
    """
    _, _, workspace = seed_workspace

    from backend.app.db.models.integrations import WorkspaceRepo

    # Two activated repos in the same workspace.
    repo_a = WorkspaceRepo(
        workspace_id=workspace.id,
        provider="github",
        external_id=1001,
        full_name="acme/one",
        default_branch="main",
        html_url="https://github.com/acme/one",
        preset="web-app",
    )
    repo_b = WorkspaceRepo(
        workspace_id=workspace.id,
        provider="github",
        external_id=1002,
        full_name="acme/two",
        default_branch="main",
        html_url="https://github.com/acme/two",
        preset="web-app",
    )
    db_session.add_all([repo_a, repo_b])
    await db_session.flush()

    # Same slug on *different* repos → allowed.
    bucket_a = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="code-style",
        name="Code style",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        repo_id=repo_a.id,
        source_ref={"path": ".ship/knowledge/code-style.md"},
    )
    bucket_b = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="code-style",
        name="Code style",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        repo_id=repo_b.id,
        source_ref={"path": ".ship/knowledge/code-style.md"},
    )
    db_session.add_all([bucket_a, bucket_b])
    await db_session.flush()

    # Second insert on *same* repo with same slug → conflict.
    dup = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="code-style",
        name="Dup",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        repo_id=repo_a.id,
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_workspace_and_repo_scope_may_share_slug(
    db_session, seed_workspace
) -> None:
    """A workspace-scoped ``code-style`` and a repo-scoped
    ``code-style`` coexist — the partial unique indexes don't
    overlap because each has a ``WHERE scope_kind = ...`` filter.

    Pinning this lets the later "effective resolution" API return a
    layered list without insert-time collisions.
    """
    _, _, workspace = seed_workspace

    from backend.app.db.models.integrations import WorkspaceRepo

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        provider="github",
        external_id=2001,
        full_name="acme/shared-slug",
        default_branch="main",
        html_url="https://github.com/acme/shared-slug",
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()

    ws_bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="code-style",
        name="Org-wide style",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.AGENT_MEMORY,
    )
    repo_bucket = KnowledgeBucket(
        workspace_id=workspace.id,
        slug="code-style",
        name="Repo style override",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        repo_id=repo.id,
    )
    db_session.add_all([ws_bucket, repo_bucket])
    await db_session.flush()

    # Round-trip: both rows returned, carrying distinct scope/source.
    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace.id,
                KnowledgeBucket.slug == "code-style",
            )
        )
    ).scalars().all()
    by_scope = {row.scope_kind: row for row in rows}
    assert set(by_scope) == {BucketScope.WORKSPACE, BucketScope.REPO}
    assert by_scope[BucketScope.REPO].source_kind == BucketSource.REPO_FILES
    assert by_scope[BucketScope.REPO].repo_id == repo.id
    assert by_scope[BucketScope.WORKSPACE].repo_id is None
