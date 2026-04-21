"""Unit tests for :mod:`backend.app.services.bucket_repo_files_sync`.

Exercises the happy path plus the edge cases the webhook / activation
paths will actually hit in production:

1. **Fresh repo** — two ``.md`` files under ``.ship/knowledge/``;
   expect two ``KnowledgeBucket`` rows with ``scope_kind='repo'`` +
   ``source_kind='repo_files'`` + matching slug/source_ref.
2. **Unchanged re-run** — same SHA the second time; nothing is
   written (``files_skipped_unchanged`` counts them).
3. **File edit** — ``content_sha`` changes; the existing row's
   ``name`` / ``description`` / ``source_ref`` update in place.
4. **File deletion** — a previously-seen file disappears from the
   tree; its bucket row flips to ``archived_at != None``.
5. **Resurrection** — archived row reappears; ``archived_at``
   clears back to ``None`` and content refreshes.
6. **Oversize / binary** — skipped, not created.
7. **Non-KB markdown** — markdown under ``docs/`` is ignored.

The Postgres fixture gives us real CHECK/partial-unique enforcement
so any accidental cross-scope leakage (e.g. forgetting to set
``repo_id`` on a scope='repo' row) would fail at ``session.flush``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

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
from backend.app.integrations.gateway.code_host import (
    BlobContent,
    RepoRef,
)
from backend.app.services.bucket_repo_files_sync import sync_repo_files


# ---------------------------------------------------------------------------
# Fake gateway — structural match against the Protocol, enough for sync.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FakeFile:
    path: str
    content: str
    sha: str
    encoding: str = "utf-8"
    size: int = -1  # set in __post_init__

    def __post_init__(self) -> None:
        if self.size == -1:
            self.size = len(self.content.encode("utf-8"))


@dataclass(slots=True)
class FakeCodeHost:
    files: dict[str, _FakeFile] = field(default_factory=dict)
    # If the test wants to assert that ``get_blob`` was *not* called
    # for unchanged files, we can count hits here.
    blob_calls: list[str] = field(default_factory=list)

    async def list_files(
        self, ref: RepoRef, *, ref_sha: str | None = None
    ) -> list[str]:
        return sorted(self.files.keys())

    async def get_blob(
        self,
        ref: RepoRef,
        *,
        path: str,
        ref_sha: str | None = None,
    ) -> BlobContent:
        self.blob_calls.append(path)
        f = self.files.get(path)
        if f is None:
            raise FileNotFoundError(path)
        return BlobContent(
            path=f.path,
            ref=ref_sha or "main",
            sha=f.sha,
            size=f.size,
            encoding=f.encoding,
            content=f.content,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def seed_activated_repo(db_session, seed_workspace):
    """Provision a workspace + installation + activated repo.

    Returns ``(workspace, repo, install)``. The install carries a real
    ``installation_id`` so downstream code that touches GitHub won't
    immediately fall over if we accidentally let the real gateway run
    (the test still passes a ``FakeCodeHost`` to keep things off the
    wire).
    """
    _, _, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=42_000,
        account_id=4000,
        account_login="acme",
        account_type="Organization",
    )
    db_session.add(install)
    await db_session.flush()

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=9000,
        full_name="acme/knowledge-base",
        default_branch="main",
        html_url="https://github.com/acme/knowledge-base",
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()
    return workspace, repo, install


async def _load_repo_buckets(
    db_session, *, workspace_id, repo_id
) -> list[KnowledgeBucket]:
    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(KnowledgeBucket)
            .where(
                KnowledgeBucket.workspace_id == workspace_id,
                KnowledgeBucket.repo_id == repo_id,
                KnowledgeBucket.scope_kind == BucketScope.REPO,
                KnowledgeBucket.source_kind == BucketSource.REPO_FILES,
            )
            .order_by(KnowledgeBucket.slug)
        )
    ).scalars().all()
    return list(rows)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fresh_repo_creates_one_bucket_per_file(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, install = seed_activated_repo
    gw = FakeCodeHost(
        files={
            ".ship/knowledge/code-style.md": _FakeFile(
                path=".ship/knowledge/code-style.md",
                sha="sha-code-style-v1",
                content="# Code style\n\nWe use ruff.",
            ),
            ".ship/knowledge/ui-runbook.md": _FakeFile(
                path=".ship/knowledge/ui-runbook.md",
                sha="sha-ui-runbook-v1",
                content="# UI runbook\n\nFollow the brand kit.",
            ),
            # Non-KB markdown must be ignored.
            "docs/readme.md": _FakeFile(
                path="docs/readme.md",
                sha="unrelated",
                content="# Readme",
            ),
        }
    )

    report = await sync_repo_files(db_session, repo, install, gateway=gw)

    assert report.files_discovered == 2
    assert report.buckets_created == 2
    assert report.buckets_updated == 0
    assert report.buckets_archived == 0

    rows = await _load_repo_buckets(
        db_session, workspace_id=workspace.id, repo_id=repo.id
    )
    slugs = [r.slug for r in rows]
    assert slugs == ["code-style", "ui-runbook"]
    for row in rows:
        assert row.name.startswith(("Code style", "UI runbook"))
        assert row.source_ref is not None
        assert row.source_ref["path"].startswith(".ship/knowledge/")
        assert row.source_ref["branch"] == "main"
        assert row.archived_at is None


@pytest.mark.asyncio
async def test_unchanged_rerun_is_noop(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, install = seed_activated_repo
    files = {
        ".ship/knowledge/code-style.md": _FakeFile(
            path=".ship/knowledge/code-style.md",
            sha="sha-v1",
            content="# Code style\n\nWe use ruff.",
        )
    }
    gw = FakeCodeHost(files=files)

    first = await sync_repo_files(db_session, repo, install, gateway=gw)
    assert first.buckets_created == 1

    # Second run hits the SHA fast-path: no writes, counts skip.
    second = await sync_repo_files(db_session, repo, install, gateway=gw)
    assert second.buckets_created == 0
    assert second.buckets_updated == 0
    assert second.buckets_archived == 0
    assert second.files_skipped_unchanged == 1


@pytest.mark.asyncio
async def test_edit_updates_existing_row_in_place(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, install = seed_activated_repo
    gw = FakeCodeHost(
        files={
            ".ship/knowledge/code-style.md": _FakeFile(
                path=".ship/knowledge/code-style.md",
                sha="sha-v1",
                content="# Code style v1\n\nOriginal text.",
            )
        }
    )
    await sync_repo_files(db_session, repo, install, gateway=gw)
    before = (
        await _load_repo_buckets(
            db_session, workspace_id=workspace.id, repo_id=repo.id
        )
    )[0]
    before_id = before.id

    # Edit: bump SHA, rewrite content.
    gw.files[".ship/knowledge/code-style.md"] = _FakeFile(
        path=".ship/knowledge/code-style.md",
        sha="sha-v2",
        content="# Code style v2\n\nRewritten.",
    )

    report = await sync_repo_files(db_session, repo, install, gateway=gw)
    assert report.buckets_updated == 1
    assert report.buckets_created == 0

    after = (
        await _load_repo_buckets(
            db_session, workspace_id=workspace.id, repo_id=repo.id
        )
    )[0]
    assert after.id == before_id
    assert after.name == "Code style v2"
    assert after.description == "Rewritten."
    assert after.source_ref["content_sha"] == "sha-v2"


@pytest.mark.asyncio
async def test_deletion_archives_row(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, install = seed_activated_repo
    gw = FakeCodeHost(
        files={
            ".ship/knowledge/ui-runbook.md": _FakeFile(
                path=".ship/knowledge/ui-runbook.md",
                sha="sha-v1",
                content="# UI runbook\n\nBody.",
            )
        }
    )
    await sync_repo_files(db_session, repo, install, gateway=gw)

    # File disappears from the tree.
    gw.files.clear()

    report = await sync_repo_files(db_session, repo, install, gateway=gw)
    assert report.buckets_archived == 1

    rows = await _load_repo_buckets(
        db_session, workspace_id=workspace.id, repo_id=repo.id
    )
    assert len(rows) == 1
    assert rows[0].archived_at is not None


@pytest.mark.asyncio
async def test_resurrection_clears_archived_at(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, install = seed_activated_repo
    gw = FakeCodeHost(
        files={
            ".ship/knowledge/ui-runbook.md": _FakeFile(
                path=".ship/knowledge/ui-runbook.md",
                sha="sha-v1",
                content="# UI runbook\n\nBody.",
            )
        }
    )
    await sync_repo_files(db_session, repo, install, gateway=gw)
    gw.files.clear()
    await sync_repo_files(db_session, repo, install, gateway=gw)  # archive

    # Operator re-commits the file with new content.
    gw.files[".ship/knowledge/ui-runbook.md"] = _FakeFile(
        path=".ship/knowledge/ui-runbook.md",
        sha="sha-v2",
        content="# UI runbook 2\n\nRewritten body.",
    )
    report = await sync_repo_files(db_session, repo, install, gateway=gw)
    # Updated in-place (counts as update, not create — it was the same
    # row archived a moment ago).
    assert report.buckets_updated == 1
    assert report.buckets_created == 0

    rows = await _load_repo_buckets(
        db_session, workspace_id=workspace.id, repo_id=repo.id
    )
    assert len(rows) == 1
    assert rows[0].archived_at is None
    assert rows[0].name == "UI runbook 2"


@pytest.mark.asyncio
async def test_oversize_and_binary_files_are_skipped(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, install = seed_activated_repo
    oversized = "x" * (256 * 1024 + 1)
    gw = FakeCodeHost(
        files={
            ".ship/knowledge/huge.md": _FakeFile(
                path=".ship/knowledge/huge.md",
                sha="huge-v1",
                content=oversized,
            ),
            ".ship/knowledge/image.md": _FakeFile(
                path=".ship/knowledge/image.md",
                sha="image-v1",
                # Pretend somebody committed a binary blob named .md.
                content="\x00\x01\x02\x03",
                encoding="base64",
            ),
            ".ship/knowledge/ok.md": _FakeFile(
                path=".ship/knowledge/ok.md",
                sha="ok-v1",
                content="# Ok\n\nFine.",
            ),
        }
    )

    report = await sync_repo_files(db_session, repo, install, gateway=gw)
    assert report.buckets_created == 1
    assert report.files_skipped_too_big == 1
    assert report.files_skipped_binary == 1

    rows = await _load_repo_buckets(
        db_session, workspace_id=workspace.id, repo_id=repo.id
    )
    assert [r.slug for r in rows] == ["ok"]


# ---------------------------------------------------------------------------
# Phase 5a: bucket_articles dual-write
# ---------------------------------------------------------------------------


async def _load_articles(db_session, *, bucket_id) -> list[BucketArticle]:
    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(BucketArticle)
            .where(BucketArticle.bucket_id == bucket_id)
            .order_by(BucketArticle.version)
        )
    ).scalars().all()
    return list(rows)


@pytest.mark.asyncio
async def test_fresh_sync_creates_article_per_bucket(
    db_session, seed_activated_repo
) -> None:
    """Each new ``repo_files`` bucket gets a ``main`` article at v1.

    Cross-checks that the new article carries the full projection —
    title, body, content_sha, published status, and provenance pointing
    back at the source path — so downstream readers (the console detail
    page, the Distiller) can trust it without re-fetching from git.
    """
    workspace, repo, install = seed_activated_repo
    body = "# Code style\n\nWe use ruff."
    gw = FakeCodeHost(
        files={
            ".ship/knowledge/code-style.md": _FakeFile(
                path=".ship/knowledge/code-style.md",
                sha="vendor-sha-v1",
                content=body,
            ),
        }
    )

    report = await sync_repo_files(db_session, repo, install, gateway=gw)
    assert report.articles_created == 1
    assert report.articles_updated == 0
    assert report.articles_unchanged == 0

    buckets = await _load_repo_buckets(
        db_session, workspace_id=workspace.id, repo_id=repo.id
    )
    assert len(buckets) == 1
    articles = await _load_articles(db_session, bucket_id=buckets[0].id)
    assert len(articles) == 1
    a = articles[0]
    assert a.slug == "main"
    assert a.version == 1
    assert a.status == BucketArticleStatus.PUBLISHED
    assert a.supersedes_id is None
    assert a.body_md == body
    assert a.title == "Code style"
    assert a.provenance["source_kind"] == BucketSource.REPO_FILES
    assert a.provenance["path"] == ".ship/knowledge/code-style.md"
    assert a.provenance["vendor_sha"] == "vendor-sha-v1"
    assert a.provenance["branch"] == "main"


@pytest.mark.asyncio
async def test_unchanged_rerun_does_not_churn_articles(
    db_session, seed_activated_repo
) -> None:
    """SHA fast-path on the bucket must also skip article writes.

    Protects against "every push to main makes a new article version" —
    which would make the version history meaningless and balloon the
    ``bucket_articles`` table on quiet repos.
    """
    workspace, repo, install = seed_activated_repo
    gw = FakeCodeHost(
        files={
            ".ship/knowledge/ui-runbook.md": _FakeFile(
                path=".ship/knowledge/ui-runbook.md",
                sha="vendor-sha-v1",
                content="# UI runbook\n\nBody.",
            )
        }
    )
    first = await sync_repo_files(db_session, repo, install, gateway=gw)
    assert first.articles_created == 1

    second = await sync_repo_files(db_session, repo, install, gateway=gw)
    assert second.articles_created == 0
    assert second.articles_updated == 0
    assert second.articles_unchanged == 1

    buckets = await _load_repo_buckets(
        db_session, workspace_id=workspace.id, repo_id=repo.id
    )
    articles = await _load_articles(db_session, bucket_id=buckets[0].id)
    # Still exactly one row — no history generated by a no-op re-run.
    assert [a.version for a in articles] == [1]


@pytest.mark.asyncio
async def test_edit_bumps_version_and_supersedes(
    db_session, seed_activated_repo
) -> None:
    """A content change inserts v2 ``published`` + flips v1 to ``superseded``.

    Verifies the partial-unique index on published articles never sees
    two rows for the same slug simultaneously — SQLAlchemy's UPDATE
    (flipping status) runs before the INSERT in the same flush.
    """
    workspace, repo, install = seed_activated_repo
    gw = FakeCodeHost(
        files={
            ".ship/knowledge/code-style.md": _FakeFile(
                path=".ship/knowledge/code-style.md",
                sha="vendor-sha-v1",
                content="# Code style v1\n\nOriginal.",
            )
        }
    )
    await sync_repo_files(db_session, repo, install, gateway=gw)

    # Edit the file.
    gw.files[".ship/knowledge/code-style.md"] = _FakeFile(
        path=".ship/knowledge/code-style.md",
        sha="vendor-sha-v2",
        content="# Code style v2\n\nRewritten.",
    )
    report = await sync_repo_files(db_session, repo, install, gateway=gw)
    assert report.articles_updated == 1

    buckets = await _load_repo_buckets(
        db_session, workspace_id=workspace.id, repo_id=repo.id
    )
    articles = await _load_articles(db_session, bucket_id=buckets[0].id)
    assert [a.version for a in articles] == [1, 2]

    v1 = articles[0]
    v2 = articles[1]
    assert v1.status == BucketArticleStatus.SUPERSEDED
    assert v2.status == BucketArticleStatus.PUBLISHED
    assert v2.supersedes_id == v1.id
    assert v2.body_md == "# Code style v2\n\nRewritten."
    assert v2.title == "Code style v2"
    # content_sha is over the body, not the vendor sha — so v1 and v2
    # have distinct hashes.
    assert v1.content_sha != v2.content_sha


@pytest.mark.asyncio
async def test_file_deletion_archives_current_article(
    db_session, seed_activated_repo
) -> None:
    """When a file disappears the current article flips to ``archived``."""
    workspace, repo, install = seed_activated_repo
    gw = FakeCodeHost(
        files={
            ".ship/knowledge/ui-runbook.md": _FakeFile(
                path=".ship/knowledge/ui-runbook.md",
                sha="vendor-sha-v1",
                content="# UI runbook\n\nBody.",
            )
        }
    )
    await sync_repo_files(db_session, repo, install, gateway=gw)

    gw.files.clear()
    report = await sync_repo_files(db_session, repo, install, gateway=gw)
    assert report.articles_archived == 1

    buckets = await _load_repo_buckets(
        db_session, workspace_id=workspace.id, repo_id=repo.id
    )
    articles = await _load_articles(db_session, bucket_id=buckets[0].id)
    assert len(articles) == 1
    assert articles[0].status == BucketArticleStatus.ARCHIVED
    assert articles[0].archived_at is not None


@pytest.mark.asyncio
async def test_resurrection_creates_new_version_not_collide(
    db_session, seed_activated_repo
) -> None:
    """Re-adding a deleted file must insert v2, not collide with archived v1.

    The UNIQUE ``(bucket_id, slug, version)`` index would explode if we
    tried to reuse v=1; the resurrection branch has to look at
    ``MAX(version)`` across all statuses.
    """
    workspace, repo, install = seed_activated_repo
    gw = FakeCodeHost(
        files={
            ".ship/knowledge/ui-runbook.md": _FakeFile(
                path=".ship/knowledge/ui-runbook.md",
                sha="vendor-sha-v1",
                content="# UI runbook\n\nOriginal.",
            )
        }
    )
    await sync_repo_files(db_session, repo, install, gateway=gw)

    gw.files.clear()
    await sync_repo_files(db_session, repo, install, gateway=gw)  # archive

    gw.files[".ship/knowledge/ui-runbook.md"] = _FakeFile(
        path=".ship/knowledge/ui-runbook.md",
        sha="vendor-sha-v2",
        content="# UI runbook 2\n\nRewritten body.",
    )
    report = await sync_repo_files(db_session, repo, install, gateway=gw)
    # Bucket resurrects (update), article is a fresh publish (create,
    # since there was no live article after archival).
    assert report.articles_created == 1

    buckets = await _load_repo_buckets(
        db_session, workspace_id=workspace.id, repo_id=repo.id
    )
    articles = await _load_articles(db_session, bucket_id=buckets[0].id)
    # Two rows: v1 archived, v2 published. No collision on the UNIQUE.
    assert [a.version for a in articles] == [1, 2]
    assert articles[0].status == BucketArticleStatus.ARCHIVED
    assert articles[1].status == BucketArticleStatus.PUBLISHED
    assert articles[1].body_md == "# UI runbook 2\n\nRewritten body."


@pytest.mark.asyncio
async def test_unchanged_bucket_but_missing_article_backfills(
    db_session, seed_activated_repo
) -> None:
    """Idempotent backfill for pre-Phase-5a buckets without articles.

    If the bucket row already exists at the right SHA but ``bucket_articles``
    is empty for it (because this install was provisioned before Phase
    5a shipped), the next sync pass must populate the missing article
    rather than stay on the no-op fast path. Without this, pre-existing
    tenants would be permanently stuck without articles until somebody
    edits every file.
    """
    from backend.app.services.bucket_repo_files_sync import _build_row

    workspace, repo, install = seed_activated_repo
    body = "# Legacy\n\nWas synced before Phase 5a."
    blob = BlobContent(
        path=".ship/knowledge/legacy.md",
        ref="main",
        sha="vendor-sha-legacy",
        size=len(body.encode("utf-8")),
        encoding="utf-8",
        content=body,
    )
    # Seed the bucket the way an old sync would: bucket row only, no
    # article. We don't call sync_repo_files for this seeding because
    # that would already write the article via the new dual-write path.
    row = _build_row(repo=repo, blob=blob)
    db_session.add(row)
    await db_session.flush()

    gw = FakeCodeHost(
        files={
            ".ship/knowledge/legacy.md": _FakeFile(
                path=".ship/knowledge/legacy.md",
                sha="vendor-sha-legacy",
                content=body,
            )
        }
    )
    report = await sync_repo_files(db_session, repo, install, gateway=gw)
    # Bucket is untouched (SHA match), but the article gets created.
    assert report.files_skipped_unchanged == 1
    assert report.articles_created == 1

    articles = await _load_articles(db_session, bucket_id=row.id)
    assert len(articles) == 1
    assert articles[0].version == 1
    assert articles[0].status == BucketArticleStatus.PUBLISHED
