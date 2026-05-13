"""Tests for the workspace-scoped artifact resolver (RFC-0006).

We assert three behaviours that drive the cloud-platform pitch:

1. ``catalog_sources={"global": false}`` hides the public Ship artifacts.
2. A workspace-level repo can shadow / override a global artifact (workspace
   precedence over global).
3. Project-level repos override workspace-level repos.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from backend.app.db.models.tenancy import ArtifactRepo


pytestmark = pytest.mark.asyncio


def _write_pattern(root: Path, pattern_id: str, *, summary: str) -> None:
    """Materialise a minimal valid pattern under ``<root>/artifacts/patterns/``.

    Includes every field that ``_split_frontmatter`` enforces in strict
    mode — otherwise the legacy parser raises a 500 even though we're inside
    the new ``/v1`` resolver.
    """
    folder = root / "artifacts" / "patterns" / pattern_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ARTIFACT.md").write_text(
        textwrap.dedent(
            f"""\
            ---
            artifact_kind: pattern
            id: {pattern_id}
            name: {pattern_id}
            version: 0.1.0
            channel: stable
            min_shipctl: 0.3.0
            updated_at: "2026-04-19T00:00:00+00:00"
            content_sha256: 0000000000000000000000000000000000000000000000000000000000000000
            deprecated: false
            replaced_by: null
            yanked: false
            description: {summary}
            ---
            # {pattern_id}
            """
        ),
        encoding="utf-8",
    )


async def test_global_listing_is_visible_by_default(v1_client, seed_workspace):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    resp = await v1_client.get(f"/v1/workspaces/{ws.id}/artifacts/patterns", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "pattern"
    # The global Ship monorepo ships several patterns; assert at least one and
    # that they're flagged as ``global``.
    assert len(body["patterns"]) > 0
    assert all(p["effective_source"] == "global" for p in body["patterns"])


async def test_disabling_global_hides_global_artifacts(v1_client, seed_workspace):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    patch = await v1_client.patch(
        f"/v1/workspaces/{ws.id}",
        headers=headers,
        json={"catalog_sources": {"global": False}},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["catalog_sources"]["global"] is False

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/artifacts/patterns", headers=headers
    )
    assert resp.status_code == 200
    assert resp.json()["patterns"] == []


async def test_workspace_repo_overrides_global(
    v1_client, seed_workspace, db_session, tmp_path, monkeypatch
):
    """A pattern with the same id in a workspace repo wins over the global one."""
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    # Pick any existing global pattern id and override its summary locally.
    global_resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/artifacts/patterns", headers=headers
    )
    existing_id = global_resp.json()["patterns"][0]["id"]

    overlay_root = tmp_path / "ws-overlay"
    _write_pattern(overlay_root, existing_id, summary="Overridden in workspace")

    repo = ArtifactRepo(
        workspace_id=ws.id, kind="workspace", url=f"file://{overlay_root}"
    )
    db_session.add(repo)
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/artifacts/patterns/{existing_id}",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["effective_source"] == "workspace"
    assert body["source_repo_id"] == str(repo.id)


async def test_project_repo_overrides_workspace(
    v1_client, seed_workspace, db_session, tmp_path
):
    """Project-level layer beats workspace layer for the same artifact id."""
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    artifact_id = "ship-cloud-overlay-fixture"
    ws_root = tmp_path / "ws"
    proj_root = tmp_path / "proj"
    _write_pattern(ws_root, artifact_id, summary="from workspace")
    _write_pattern(proj_root, artifact_id, summary="from project")

    db_session.add(
        ArtifactRepo(workspace_id=ws.id, kind="workspace", url=f"file://{ws_root}")
    )
    db_session.add(
        ArtifactRepo(workspace_id=ws.id, kind="project", url=f"file://{proj_root}")
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/artifacts/patterns/{artifact_id}",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["effective_source"] == "project"


async def test_detail_returns_readme_and_layer_history(
    v1_client, seed_workspace, db_session, tmp_path
):
    """Detail endpoint should expose README body and every overriding layer."""
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    artifact_id = "ship-cloud-detail-fixture"
    ws_root = tmp_path / "ws"
    proj_root = tmp_path / "proj"
    _write_pattern(ws_root, artifact_id, summary="from workspace")
    _write_pattern(proj_root, artifact_id, summary="from project")

    db_session.add(
        ArtifactRepo(workspace_id=ws.id, kind="workspace", url=f"file://{ws_root}")
    )
    db_session.add(
        ArtifactRepo(workspace_id=ws.id, kind="project", url=f"file://{proj_root}")
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{ws.id}/artifacts/patterns/{artifact_id}",
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # winner is the highest-priority layer (project)
    assert body["effective_source"] == "project"
    # README body comes from ARTIFACT.md (everything below the closing `---`)
    assert artifact_id in body["readme"]
    # both layers are reported, project first then workspace
    assert [layer["effective_source"] for layer in body["layers"]] == [
        "project",
        "workspace",
    ]
    # internal resolver bookkeeping is stripped from the wire shape
    assert "_body" not in body
    assert "_full" not in body


async def test_artifact_repo_register_and_list(
    v1_client, seed_workspace, tmp_path
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    _write_pattern(tmp_path / "demo", "demo-pattern", summary="demo")

    create = await v1_client.post(
        f"/v1/workspaces/{ws.id}/artifact-repos",
        headers=headers,
        json={"kind": "workspace", "url": f"file://{tmp_path / 'demo'}"},
    )
    assert create.status_code == 201, create.text
    assert create.json()["url"].startswith("file://")

    listed = await v1_client.get(
        f"/v1/workspaces/{ws.id}/artifact-repos", headers=headers
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1


async def test_patch_workspace_rejects_unknown_catalog_keys(
    v1_client, seed_workspace
):
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    resp = await v1_client.patch(
        f"/v1/workspaces/{ws.id}",
        headers=headers,
        json={"catalog_sources": {"unknown_layer": True}},
    )
    assert resp.status_code == 422
