"""Tests for the workspace knowledge endpoint.

Covers:
- empty workspace returns ``{buckets: []}`` (no repos registered)
- legacy repo files and repo-scoped DB rows are ignored
- workspace-scoped DB buckets are listed and readable
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from backend.app.db.models.agent_memory import (
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.tenancy import ArtifactRepo


pytestmark = pytest.mark.asyncio


def _write_bucket(root: Path, slug: str, body: str) -> None:
    folder = root / ".ship" / "knowledge"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{slug}.md").write_text(textwrap.dedent(body), encoding="utf-8")


async def test_empty_workspace_returns_empty_list(v1_client, seed_workspace):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    resp = await v1_client.get(f"/v1/workspaces/{ws.id}/knowledge", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_id"] == str(ws.id)
    assert body["buckets"] == []


async def test_legacy_project_bucket_is_ignored(
    v1_client, seed_workspace, db_session, tmp_path
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    _write_bucket(
        tmp_path / "proj",
        "brandbook",
        """\
        # Helio Platform · brandbook

        We make a payments rails for Latin America.
        """,
    )
    db_session.add(
        ArtifactRepo(
            workspace_id=ws.id,
            kind="project",
            url=f"file://{tmp_path / 'proj'}",
        )
    )
    await db_session.flush()

    resp = await v1_client.get(f"/v1/workspaces/{ws.id}/knowledge", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["buckets"] == []


async def test_workspace_db_bucket_is_listed_and_readable(
    v1_client, seed_workspace, db_session, tmp_path
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    _write_bucket(
        tmp_path / "ws",
        "code-style",
        "# code-style (workspace copy)\n\nworkspace level body.",
    )
    _write_bucket(
        tmp_path / "proj",
        "code-style",
        "# code-style (project copy)\n\nproject level body wins.",
    )
    db_session.add(
        ArtifactRepo(workspace_id=ws.id, kind="workspace", url=f"file://{tmp_path / 'ws'}")
    )
    db_session.add(
        ArtifactRepo(workspace_id=ws.id, kind="project", url=f"file://{tmp_path / 'proj'}")
    )
    db_session.add(
        KnowledgeBucket(
            workspace_id=ws.id,
            slug="code-style",
            name="Code style",
            description="DB-owned workspace knowledge.",
            scope_kind=BucketScope.WORKSPACE,
            source_kind=BucketSource.EXTERNAL_STATIC,
        )
    )
    await db_session.flush()

    resp = await v1_client.get(f"/v1/workspaces/{ws.id}/knowledge", headers=headers)
    assert resp.status_code == 200, resp.text
    [bucket] = resp.json()["buckets"]
    assert bucket["visibility"] == "workspace"
    assert bucket["scope_kind"] == "workspace"
    assert bucket["source_kind"] == "external_static"
    assert bucket["excerpt"] == "DB-owned workspace knowledge."

    detail = await v1_client.get(
        f"/v1/workspaces/{ws.id}/knowledge/code-style", headers=headers
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["body"] == "DB-owned workspace knowledge."
    assert body["visibility"] == "workspace"


async def test_detail_404_when_missing(v1_client, seed_workspace):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/knowledge/does-not-exist", headers=headers
    )
    assert resp.status_code == 404


async def test_catalog_source_flags_do_not_resurrect_legacy_knowledge(
    v1_client, seed_workspace, db_session, tmp_path
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    _write_bucket(tmp_path / "proj", "testing", "# testing\n\nproject body.")
    db_session.add(
        ArtifactRepo(workspace_id=ws.id, kind="project", url=f"file://{tmp_path / 'proj'}")
    )
    await db_session.flush()

    patch = await v1_client.patch(
        f"/v1/workspaces/{ws.id}",
        headers=headers,
        json={"catalog_sources": {"project": False}},
    )
    assert patch.status_code == 200, patch.text

    resp = await v1_client.get(f"/v1/workspaces/{ws.id}/knowledge", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["buckets"] == []


# ---------------------------------------------------------------------------
# DB-only knowledge cutover
# ---------------------------------------------------------------------------


def _seed_repo_files_bucket(
    db_session,
    *,
    workspace_id,
    repo_id,
    slug,
    title,
    excerpt,
    path,
    content_sha,
    branch="main",
    archived=False,
):
    from datetime import datetime, timezone

    from backend.app.db.models.agent_memory import (
        BucketScope,
        BucketSource,
        KnowledgeBucket,
    )

    row = KnowledgeBucket(
        workspace_id=workspace_id,
        repo_id=repo_id,
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.REPO_FILES,
        slug=slug,
        name=title,
        description=excerpt,
        source_ref={
            "path": path,
            "content_sha": content_sha,
            "branch": branch,
            "size": len(excerpt),
        },
        archived_at=datetime.now(timezone.utc) if archived else None,
    )
    db_session.add(row)
    return row


async def _seed_workspace_repo(db_session, *, workspace_id, full_name="acme/notes"):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    install = GitHubInstallation(
        workspace_id=workspace_id,
        installation_id=7777,
        account_id=123,
        account_login="acme",
        account_type="Organization",
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace_id,
        installation_id=install.id,
        provider="github",
        external_id=4242,
        full_name=full_name,
        default_branch="main",
        html_url=f"https://github.com/{full_name}",
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


async def test_db_repo_files_bucket_is_hidden(
    v1_client, seed_workspace, db_session
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    repo = await _seed_workspace_repo(db_session, workspace_id=ws.id)
    _seed_repo_files_bucket(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        slug="code-style",
        title="Code style",
        excerpt="We use ruff.",
        path=".ship/knowledge/code-style.md",
        content_sha="sha-v1",
    )
    await db_session.flush()

    resp = await v1_client.get(f"/v1/workspaces/{ws.id}/knowledge", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 2
    assert body["buckets"] == []


async def test_archived_db_row_is_hidden_from_list(
    v1_client, seed_workspace, db_session
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    repo = await _seed_workspace_repo(db_session, workspace_id=ws.id)
    _seed_repo_files_bucket(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        slug="deprecated",
        title="Deprecated",
        excerpt="…",
        path=".ship/knowledge/deprecated.md",
        content_sha="sha-old",
        archived=True,
    )
    _seed_repo_files_bucket(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        slug="kept",
        title="Kept",
        excerpt="still here",
        path=".ship/knowledge/kept.md",
        content_sha="sha-live",
    )
    await db_session.flush()

    resp = await v1_client.get(f"/v1/workspaces/{ws.id}/knowledge", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["buckets"] == []


async def test_workspace_bucket_wins_over_legacy_and_repo_for_same_slug(
    v1_client, seed_workspace, db_session, tmp_path
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    # Legacy side: a disk-scan ArtifactRepo with the same slug.
    _write_bucket(
        tmp_path / "proj",
        "shared",
        "# shared (legacy copy)\n\nlegacy excerpt.",
    )
    db_session.add(
        ArtifactRepo(
            workspace_id=ws.id,
            kind="project",
            url=f"file://{tmp_path / 'proj'}",
        )
    )

    # DB side.
    repo = await _seed_workspace_repo(db_session, workspace_id=ws.id)
    _seed_repo_files_bucket(
        db_session,
        workspace_id=ws.id,
        repo_id=repo.id,
        slug="shared",
        title="Shared (DB copy)",
        excerpt="db excerpt.",
        path=".ship/knowledge/shared.md",
        content_sha="sha-db",
    )
    db_session.add(
        KnowledgeBucket(
            workspace_id=ws.id,
            slug="shared",
            name="Shared (workspace DB)",
            description="workspace db excerpt.",
            scope_kind=BucketScope.WORKSPACE,
            source_kind=BucketSource.EXTERNAL_STATIC,
        )
    )
    await db_session.flush()

    resp = await v1_client.get(f"/v1/workspaces/{ws.id}/knowledge", headers=headers)
    assert resp.status_code == 200
    buckets = resp.json()["buckets"]
    assert len(buckets) == 1
    assert buckets[0]["title"] == "Shared (workspace DB)"
    assert buckets[0]["source_kind"] == "external_static"
