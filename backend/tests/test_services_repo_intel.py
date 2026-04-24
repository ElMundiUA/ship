"""Unit tests for :mod:`backend.app.services.repo_intel`.

The harvest service is best-effort by design — the tests here lock in
the contract that callers (the wizard, manual refresh, scheduled
re-harvest) actually depend on:

- versioned, append-only rows with a single live ``is_current=TRUE``
- per-step heuristics (languages / frameworks / entry points / commit
  style) that produce the documented JSONB shapes
- a markdown projection into ``knowledge_buckets`` so agents reading
  the bucket see a human summary without parsing JSONB
- an ``enqueue_harvest`` helper that tolerates a missing redis pool
  in dev by falling back to inline execution

Postgres is the test target so the partial unique index that enforces
"at most one current row per repo" actually fires.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest
from sqlalchemy import inspect, select, text

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
from backend.app.db.models.repo_intel import RepoIntel
from backend.app.integrations.gateway.code_host import (
    BlobContent,
    RepoRef,
)
from backend.app.services import repo_intel as svc
from backend.app.services.repo_intel import (
    enqueue_harvest,
    get_current_intel,
    harvest_repo_intel,
)


# ---------------------------------------------------------------------------
# Fakes — match the CodeHostGateway protocol shape; tests inject these so the
# harvest pipeline never touches the network.
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _FakeFile:
    path: str
    content: str
    sha: str = "sha-1"
    encoding: str = "utf-8"
    size: int = -1

    def __post_init__(self) -> None:
        if self.size == -1:
            self.size = len(self.content.encode("utf-8"))


@dataclass(slots=True)
class FakeCodeHost:
    files: dict[str, _FakeFile] = field(default_factory=dict)
    list_calls: int = 0
    blob_calls: list[str] = field(default_factory=list)

    async def list_files(
        self, ref: RepoRef, *, ref_sha: str | None = None
    ) -> list[str]:
        self.list_calls += 1
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


def _conventional_commits() -> list[dict[str, Any]]:
    """Return a sample of subjects strongly biased toward conventional."""
    samples = [
        "feat: add login flow",
        "fix(auth): handle empty token",
        "chore: bump deps",
        "feat: ship homepage redesign",
        "fix: typo in docs",
        "refactor(api): split routes",
        "docs: clarify install",
        "chore(ci): pin actions",
        "feat!: breaking response shape",
        "fix: race in queue",
        "free-form sentence about a thing",
    ]
    return [{"sha": f"sha-{i}", "message": s} for i, s in enumerate(samples)]


def _freeform_commits() -> list[dict[str, Any]]:
    samples = [
        "Update readme",
        "Move file",
        "tweak settings",
        "something",
        "Apply review feedback",
        "Add logging",
        "WIP",
        "feat: only one conventional in the bunch",
    ]
    return [{"sha": f"sha-{i}", "message": s} for i, s in enumerate(samples)]


async def _no_commits(
    ref: RepoRef, branch: str | None, count: int
) -> list[dict[str, Any]]:
    return []


def _commit_fetcher(commits: list[dict[str, Any]]):
    async def _f(
        ref: RepoRef, branch: str | None, count: int
    ) -> list[dict[str, Any]]:
        return commits

    return _f


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def seed_activated_repo(db_session, seed_workspace):
    """Workspace + GitHub installation + activated repo."""
    _, _, workspace = seed_workspace

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=42_001,
        account_id=4001,
        account_login="acme",
        account_type="Organization",
    )
    db_session.add(install)
    await db_session.flush()

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=9001,
        full_name="acme/intel-target",
        default_branch="main",
        html_url="https://github.com/acme/intel-target",
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()
    return workspace, repo, install


# ---------------------------------------------------------------------------
# 1. Migration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_creates_table(db_session) -> None:
    """``alembic upgrade head`` (run by ``_migrated``) must yield ``repo_intel``.

    Inspects ``information_schema`` so a missing migration / typo'd
    column name surfaces here rather than as a confusing ORM error
    later.
    """
    rows = (
        await db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'repo_intel' ORDER BY ordinal_position"
            )
        )
    ).all()
    columns = [r[0] for r in rows]
    assert "id" in columns
    assert "workspace_id" in columns
    assert "repo_id" in columns
    assert "version" in columns
    assert "is_current" in columns
    assert "languages" in columns
    assert "frameworks" in columns
    assert "package_managers" in columns
    assert "entry_points" in columns
    assert "structure" in columns
    assert "commit_style" in columns
    assert "visual_tokens" in columns
    assert "harvested_at" in columns
    assert "harvested_by" in columns
    assert "harvest_duration_ms" in columns
    assert "harvest_error" in columns

    # Partial unique index lives only in the migration; verify it's
    # there so the "one current row" invariant is actually enforced.
    indexes = (
        await db_session.execute(
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'repo_intel'"
            )
        )
    ).all()
    index_names = {r[0] for r in indexes}
    assert "idx_repo_intel_repo_current" in index_names


# ---------------------------------------------------------------------------
# 2 + 3. Versioning lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_harvest_persists_row_with_is_current_true(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo
    gw = FakeCodeHost(
        files={
            "main.py": _FakeFile(path="main.py", content="print('hi')"),
            "src/index.ts": _FakeFile(
                path="src/index.ts", content="export {}"
            ),
        }
    )

    report = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_no_commits,
    )

    assert report.version == 1
    assert isinstance(report.intel_id, uuid.UUID)

    row = await db_session.get(RepoIntel, report.intel_id)
    assert row is not None
    assert row.is_current is True
    assert row.version == 1
    assert row.harvested_by == "wizard"
    assert row.harvest_duration_ms is not None
    assert row.harvest_duration_ms >= 0
    assert row.harvest_error is None


@pytest.mark.asyncio
async def test_subsequent_harvest_marks_previous_not_current(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo
    gw = FakeCodeHost(
        files={"main.py": _FakeFile(path="main.py", content="print('a')")}
    )

    first = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_no_commits,
    )
    second = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_no_commits,
        triggered_by="manual_refresh",
    )

    assert first.version == 1
    assert second.version == 2

    rows = (
        await db_session.execute(
            select(RepoIntel)
            .where(RepoIntel.repo_id == repo.id)
            .order_by(RepoIntel.version)
        )
    ).scalars().all()
    assert [r.version for r in rows] == [1, 2]
    assert [r.is_current for r in rows] == [False, True]
    assert rows[1].harvested_by == "manual_refresh"


# ---------------------------------------------------------------------------
# 4. Language detection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_language_detection_from_extensions(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo
    files = {
        "src/a.ts": _FakeFile(path="src/a.ts", content="x"),
        "src/b.tsx": _FakeFile(path="src/b.tsx", content="x"),
        "src/c.tsx": _FakeFile(path="src/c.tsx", content="x"),
        "lib/d.py": _FakeFile(path="lib/d.py", content="x"),
        # Ignored extensions — markdown / json / unknown.
        "README.md": _FakeFile(path="README.md", content="x"),
        "config.json": _FakeFile(path="config.json", content="{}"),
    }
    gw = FakeCodeHost(files=files)

    report = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_no_commits,
    )

    row = await db_session.get(RepoIntel, report.intel_id)
    langs: dict[str, float] = row.languages
    # 3 typescript / 1 python = 0.75 / 0.25.
    assert langs.get("typescript") == pytest.approx(0.75, abs=1e-3)
    assert langs.get("python") == pytest.approx(0.25, abs=1e-3)
    # JSONB doesn't preserve insertion order on the round-trip, so we
    # assert the dominant language by ratio rather than dict order.
    dominant = max(langs.items(), key=lambda kv: kv[1])[0]
    assert dominant == "typescript"


# ---------------------------------------------------------------------------
# 5. Framework detection — package.json
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_framework_detection_from_package_json(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo
    files = {
        "package.json": _FakeFile(
            path="package.json",
            content=(
                '{"name":"x","dependencies":{"next":"14.0.0",'
                '"react":"18.0.0","tailwindcss":"^3.4.0"}}'
            ),
        ),
        "src/index.tsx": _FakeFile(path="src/index.tsx", content="x"),
    }
    gw = FakeCodeHost(files=files)

    report = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_no_commits,
    )
    row = await db_session.get(RepoIntel, report.intel_id)

    assert "next.js" in row.frameworks
    assert "react" in row.frameworks
    assert "tailwindcss" in row.frameworks
    assert "npm" in row.package_managers


# ---------------------------------------------------------------------------
# 6. Framework detection — pyproject.toml
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_framework_detection_from_pyproject(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo
    files = {
        "pyproject.toml": _FakeFile(
            path="pyproject.toml",
            content=(
                "[project]\nname = 'x'\n"
                "dependencies = ['fastapi>=0.110', 'pydantic>=2']\n"
            ),
        ),
        "uv.lock": _FakeFile(path="uv.lock", content="# uv lockfile\n"),
        "main.py": _FakeFile(path="main.py", content="x"),
    }
    gw = FakeCodeHost(files=files)

    report = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_no_commits,
    )
    row = await db_session.get(RepoIntel, report.intel_id)

    assert "fastapi" in row.frameworks
    assert "uv" in row.package_managers


# ---------------------------------------------------------------------------
# 7. Entry-point ordering (App Router beats Pages Router)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_entry_points_picks_app_router_first(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo
    files = {
        "app/page.tsx": _FakeFile(path="app/page.tsx", content="x"),
        "pages/_app.tsx": _FakeFile(path="pages/_app.tsx", content="x"),
        "Dockerfile": _FakeFile(path="Dockerfile", content="FROM node"),
        ".github/workflows/ci.yml": _FakeFile(
            path=".github/workflows/ci.yml", content="name: ci"
        ),
    }
    gw = FakeCodeHost(files=files)

    report = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_no_commits,
    )
    row = await db_session.get(RepoIntel, report.intel_id)

    page_entries = [e for e in row.entry_points if e.get("kind") == "page"]
    assert len(page_entries) == 1
    assert page_entries[0]["path"] == "app/page.tsx"

    container_entries = [
        e for e in row.entry_points if e.get("kind") == "container"
    ]
    assert container_entries and container_entries[0]["path"] == "Dockerfile"

    workflow_entries = [
        e for e in row.entry_points if e.get("kind") == "workflow"
    ]
    assert any(
        e["path"] == ".github/workflows/ci.yml" for e in workflow_entries
    )


# ---------------------------------------------------------------------------
# 8 + 9. Commit-style heuristic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_style_conventional_detected(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo
    gw = FakeCodeHost(
        files={"main.py": _FakeFile(path="main.py", content="x")}
    )

    report = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_commit_fetcher(_conventional_commits()),
    )
    row = await db_session.get(RepoIntel, report.intel_id)

    assert row.commit_style["convention"] == "conventional"
    assert "feat" in row.commit_style.get("common_types", [])
    assert row.commit_style.get("samples", 0) > 0


@pytest.mark.asyncio
async def test_commit_style_freeform_when_unmatched(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo
    gw = FakeCodeHost(
        files={"main.py": _FakeFile(path="main.py", content="x")}
    )

    report = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_commit_fetcher(_freeform_commits()),
    )
    row = await db_session.get(RepoIntel, report.intel_id)

    assert row.commit_style["convention"] == "freeform"


# ---------------------------------------------------------------------------
# 10. Knowledge-bucket projection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_writes_repo_intel_md_to_knowledge_bucket(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo
    gw = FakeCodeHost(
        files={
            "main.py": _FakeFile(path="main.py", content="x"),
            "package.json": _FakeFile(
                path="package.json",
                content='{"dependencies":{"next":"14"}}',
            ),
        }
    )

    report = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_no_commits,
    )
    assert report.knowledge_articles_written == 1

    bucket = (
        await db_session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace.id,
                KnowledgeBucket.repo_id == repo.id,
                KnowledgeBucket.scope_kind == BucketScope.REPO,
                KnowledgeBucket.source_kind == BucketSource.REPO_FILES,
                KnowledgeBucket.slug == "repo-intel",
            )
        )
    ).scalars().first()
    assert bucket is not None
    assert bucket.source_ref is not None
    assert bucket.source_ref["path"] == ".ship/knowledge/repo-intel.md"

    article = (
        await db_session.execute(
            select(BucketArticle).where(
                BucketArticle.bucket_id == bucket.id,
                BucketArticle.status == BucketArticleStatus.PUBLISHED,
            )
        )
    ).scalars().first()
    assert article is not None
    assert article.slug == "main"
    assert "# Repo intel" in article.body_md
    assert "next.js" in article.body_md
    assert article.provenance.get("harvest") == "repo_intel"


# ---------------------------------------------------------------------------
# 11. get_current_intel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_current_intel_returns_latest_row(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo
    gw = FakeCodeHost(
        files={"main.py": _FakeFile(path="main.py", content="x")}
    )

    await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_no_commits,
    )
    second = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_no_commits,
    )

    current = await get_current_intel(db_session, repo_id=repo.id)
    assert current is not None
    assert current.id == second.intel_id
    assert current.version == 2
    assert current.is_current is True


# ---------------------------------------------------------------------------
# 12. duration + files_examined are recorded on the row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_harvest_records_duration_and_files_examined(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo
    files = {
        f"src/file_{i}.ts": _FakeFile(path=f"src/file_{i}.ts", content="x")
        for i in range(7)
    }
    gw = FakeCodeHost(files=files)

    report = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=gw,
        commit_fetcher=_no_commits,
    )

    assert report.files_examined == 7
    assert report.duration_ms >= 0

    row = await db_session.get(RepoIntel, report.intel_id)
    assert row.harvest_duration_ms is not None
    assert row.harvest_duration_ms >= 0
    assert row.structure["file_count"] == 7
    assert "src" in row.structure["top_level_dirs"]


# ---------------------------------------------------------------------------
# 13. error path — gateway list_files explodes; row + harvest_error logged
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ExplodingCodeHost:
    """Raises on ``list_files`` to simulate a GitHub outage / auth fail."""

    async def list_files(
        self, ref: RepoRef, *, ref_sha: str | None = None
    ) -> list[str]:
        raise RuntimeError("github rate-limited")

    async def get_blob(
        self,
        ref: RepoRef,
        *,
        path: str,
        ref_sha: str | None = None,
    ) -> BlobContent:  # pragma: no cover — should not be reached
        raise FileNotFoundError(path)


@pytest.mark.asyncio
async def test_harvest_logs_error_and_records_it_when_github_fails(
    db_session, seed_activated_repo
) -> None:
    workspace, repo, _ = seed_activated_repo

    report = await harvest_repo_intel(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=repo.id,
        gateway=ExplodingCodeHost(),
        commit_fetcher=_no_commits,
    )

    row = await db_session.get(RepoIntel, report.intel_id)
    assert row is not None
    assert row.is_current is True
    assert row.harvest_error is not None
    assert "github rate-limited" in row.harvest_error
    # Languages / frameworks / etc. fall back to empty payloads — the
    # row still exists so callers can tell "we tried" from "we never
    # ran".
    assert row.languages == {}
    assert row.frameworks == []
    # Markdown projection is skipped on failure so the bucket isn't
    # populated with a stale "everything is empty" snapshot.
    assert report.knowledge_articles_written == 0


# ---------------------------------------------------------------------------
# 14. enqueue_harvest fallback to inline when no redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enqueue_harvest_falls_back_to_inline_when_no_redis(
    monkeypatch,
) -> None:
    """No redis pool ⇒ harvest runs in an inline ``asyncio.create_task``.

    We patch ``_inline_harvest`` to a lightweight sentinel so we don't
    need to spin up an engine; the contract under test is "the helper
    schedules the inline coroutine when ``redis_pool is None``".
    """
    seen: list[tuple[uuid.UUID, uuid.UUID, str]] = []

    async def _fake_inline(workspace_id, repo_id, triggered_by):
        seen.append((workspace_id, repo_id, triggered_by))

    monkeypatch.setattr(svc, "_inline_harvest", _fake_inline)

    ws_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    await enqueue_harvest(
        None, ws_id, repo_id, triggered_by="manual_refresh"
    )

    # Yield to the event loop so the scheduled task runs.
    await asyncio.sleep(0)

    assert seen == [(ws_id, repo_id, "manual_refresh")]


@pytest.mark.asyncio
async def test_enqueue_harvest_uses_redis_pool_when_provided() -> None:
    """When a redis pool is provided the helper hands off to ``enqueue_job``.

    Mirrors the worker contract documented in the service module so a
    later switch from arq to a different queue surfaces here first.
    """

    @dataclass(slots=True)
    class FakePool:
        calls: list[tuple] = field(default_factory=list)

        async def enqueue_job(self, *args, **kwargs):
            self.calls.append((args, kwargs))

    pool = FakePool()
    ws_id = uuid.uuid4()
    repo_id = uuid.uuid4()

    await enqueue_harvest(pool, ws_id, repo_id, triggered_by="wizard")

    assert len(pool.calls) == 1
    args, kwargs = pool.calls[0]
    assert args[0] == "harvest_repo_intel_job"
    assert args[1] == str(ws_id)
    assert args[2] == str(repo_id)
    assert args[3] == "wizard"
    assert "_job_id" in kwargs
    assert kwargs["_job_id"].startswith(f"harvest:{repo_id}:")
