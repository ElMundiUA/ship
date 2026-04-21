"""Tests for the workspace knowledge endpoint.

Covers:
- empty workspace returns ``{buckets: []}`` (no repos registered)
- a project repo's ``.ship/knowledge/<slug>.md`` shows up in the listing
- project layer wins over workspace layer for the same slug (precedence
  mirrors the catalog resolver)
- detail endpoint returns the full markdown body
- disabling the ``project`` source in ``catalog_sources`` hides project
  buckets without affecting workspace ones
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

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


async def test_project_bucket_shows_up(
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
    buckets = resp.json()["buckets"]
    assert len(buckets) == 1
    [bucket] = buckets
    assert bucket["slug"] == "brandbook"
    assert bucket["title"] == "Helio Platform · brandbook"
    assert bucket["visibility"] == "project"
    assert "payments rails" in bucket["excerpt"]
    # list endpoint never returns the body
    assert "body" not in bucket


async def test_project_overrides_workspace(
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
    await db_session.flush()

    resp = await v1_client.get(f"/v1/workspaces/{ws.id}/knowledge", headers=headers)
    assert resp.status_code == 200, resp.text
    [bucket] = resp.json()["buckets"]
    assert bucket["visibility"] == "project"

    detail = await v1_client.get(
        f"/v1/workspaces/{ws.id}/knowledge/code-style", headers=headers
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert "project level body wins" in body["body"]
    assert body["visibility"] == "project"


async def test_detail_404_when_missing(v1_client, seed_workspace):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/knowledge/does-not-exist", headers=headers
    )
    assert resp.status_code == 404


async def test_disabling_project_source_hides_project_buckets(
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
# Phase 2: DB-backed ``repo_files`` buckets
# ---------------------------------------------------------------------------
#
# The sync service stores ``.ship/knowledge/*.md`` as ``scope_kind='repo'``
# + ``source_kind='repo_files'`` rows in ``knowledge_buckets``. The route
# reads those rows first; legacy disk-lister output is a fallback for the
# self-hosted dev surface. Tests here seed the rows directly (skipping the
# GitHub round-trip) so we can isolate the route's projection logic from
# the sync service's correctness (covered separately in
# ``test_bucket_repo_files_sync.py``).


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


async def test_db_repo_files_bucket_shows_up_with_phase2_fields(
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
    [bucket] = body["buckets"]
    assert bucket["slug"] == "code-style"
    assert bucket["title"] == "Code style"
    # Phase 2 additions exposed for new consumers.
    assert bucket["scope_kind"] == "repo"
    assert bucket["source_kind"] == "repo_files"
    assert bucket["repo_full_name"] == "acme/notes"
    assert bucket["repo_url"] == "https://github.com/acme/notes"
    assert bucket["path"] == ".ship/knowledge/code-style.md"
    assert bucket["source_ref"]["content_sha"] == "sha-v1"
    # Legacy ``visibility`` keeps its slot so old clients don't 500.
    assert bucket["visibility"] == "repo"


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
    slugs = [b["slug"] for b in resp.json()["buckets"]]
    assert slugs == ["kept"]


async def test_db_row_wins_over_legacy_for_same_slug(
    v1_client, seed_workspace, db_session, tmp_path
):
    """DB-backed repo_files row shadows a legacy disk-lister entry.

    This is the SaaS-meets-self-hosted corner: an operator mirrors a
    knowledge repo into a local ``ArtifactRepo`` (old path) and also
    activates the same repo as a ``WorkspaceRepo`` (new path). We
    want exactly one entry in the response, and it must be the DB
    one because that row carries the vendor SHA + push trail.
    """
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
    await db_session.flush()

    resp = await v1_client.get(f"/v1/workspaces/{ws.id}/knowledge", headers=headers)
    assert resp.status_code == 200
    buckets = resp.json()["buckets"]
    assert len(buckets) == 1
    assert buckets[0]["title"] == "Shared (DB copy)"
    assert buckets[0]["source_kind"] == "repo_files"
