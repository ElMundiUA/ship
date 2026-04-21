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
