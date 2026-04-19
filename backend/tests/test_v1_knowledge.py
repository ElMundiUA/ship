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
