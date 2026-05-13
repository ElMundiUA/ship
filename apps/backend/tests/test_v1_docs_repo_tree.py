"""Wizard picker — `/v1/.../knowledge/sources/docs-repo/tree`.

Stubs the GitHub round-trip via the route's `_docs_repo_fetch_tree`
helper (same seam pattern used elsewhere) so tests don't reach the
real GitHub API.
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def _seed_repo(db_session, workspace_id, *, with_installation: bool = True) -> WorkspaceRepo:
    install = None
    if with_installation:
        install = GitHubInstallation(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            installation_id=42,
            account_login="askslayer",
            account_id=1,
            account_type="User",
        )
        db_session.add(install)
    repo = WorkspaceRepo(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        installation_id=install.id if install else None,
        full_name="askslayer/visitor-back",
        provider="github",
        external_id=12345,
        default_branch="main",
        html_url="https://github.com/askslayer/visitor-back",
    )
    db_session.add(repo)
    return repo


@pytest.mark.asyncio
async def test_docs_repo_tree_returns_doc_nodes_and_filters_empty_dirs(
    v1_client, seed_workspace, db_session, monkeypatch
) -> None:
    _, raw, workspace = seed_workspace
    repo = _seed_repo(db_session, workspace.id)
    await db_session.flush()

    async def _fake_tree(gateway, *, owner, repo, ref):
        assert owner == "askslayer"
        assert repo == "visitor-back"
        return {
            "ref": "abc123",
            "blobs": [
                "README.md",
                "docs/setup.md",
                "docs/usage.mdx",
                "src/main.py",  # filtered: not a doc extension
                "build/output.bin",
            ],
            "trees": [
                "docs",
                "src",
                "build",  # filtered: no doc descendants
            ],
            "truncated": False,
        }

    monkeypatch.setattr(
        "backend.app.api.v1.routes.knowledge_import_sources._docs_repo_fetch_tree",
        _fake_tree,
    )

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/docs-repo/tree",
        headers=_auth(raw),
        params={"repo_id": str(repo.id)},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ref"] == "abc123"
    assert body["truncated"] is False

    # Empty folders ("build/") and non-doc files dropped, doc folders+blobs kept.
    paths_by_type = {(node["type"], node["path"]) for node in body["nodes"]}
    assert ("tree", "docs") in paths_by_type
    assert ("tree", "build") not in paths_by_type
    assert ("blob", "README.md") in paths_by_type
    assert ("blob", "docs/setup.md") in paths_by_type
    assert ("blob", "src/main.py") not in paths_by_type


@pytest.mark.asyncio
async def test_docs_repo_tree_404_for_unknown_repo(
    v1_client, seed_workspace
) -> None:
    """A repo_id that doesn't exist must 404 — same path that protects
    against foreign-workspace ids since the membership + ``workspace_id``
    match are checked together."""
    _, raw, workspace = seed_workspace
    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/docs-repo/tree",
        headers=_auth(raw),
        params={"repo_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_docs_repo_tree_400_when_no_installation(
    v1_client, seed_workspace, db_session
) -> None:
    _, raw, workspace = seed_workspace
    repo = _seed_repo(db_session, workspace.id, with_installation=False)
    await db_session.flush()

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/docs-repo/tree",
        headers=_auth(raw),
        params={"repo_id": str(repo.id)},
    )
    assert response.status_code == 400
    assert "installation" in response.json()["detail"].lower()
