"""End-to-end tests for the repo-driven onboarding API.

Exercises the full flow against a temporary git repo created on disk:

1. ``inspect`` returns a profile with the recommended workflows pre-filled.
2. ``install-workflows`` writes ``.github/workflows/*.yml`` + ``.ship/*``
   files and commits them.
3. ``seed-knowledge`` writes the brandbook / code-style / testing markdowns
   and commits them.

Each test seeds its own temp repo so they're hermetic.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _make_repo(root: Path) -> Path:
    """Create a tiny but realistic Node/Next-ish repo and git-init it."""
    repo = root / "demo"
    repo.mkdir()
    _write(
        repo / "README.md",
        "# Aurora\n\nA tiny demo for onboarding tests.\n\n[Docs](https://example.com/aurora)\n",
    )
    _write(
        repo / "package.json",
        json.dumps(
            {
                "name": "aurora",
                "description": "Aurora demo for onboarding.",
                "homepage": "https://example.com/aurora",
                "license": "MIT",
                "devDependencies": {
                    "next": "^14",
                    "vitest": "^1",
                    "@playwright/test": "^1",
                    "prettier": "^3",
                    "eslint": "^9",
                },
            },
            indent=2,
        ),
    )
    _write(repo / ".editorconfig", "root = true\n[*]\nindent_size = 2\n")
    _write(repo / ".prettierrc", "{}\n")
    _write(repo / ".github" / "workflows" / "ci.yml", "name: ci\n")
    _write(repo / "tests" / "smoke.test.ts", "import {it} from 'vitest';\n")

    def _git(args: list[str]) -> None:
        subprocess.run(
            ["git", *args], cwd=repo, check=True, capture_output=True, timeout=10
        )

    _git(["init", "--initial-branch=main"])
    _git(["config", "user.email", "test@example.com"])
    _git(["config", "user.name", "Test"])
    _git(["add", "."])
    _git(["commit", "-m", "initial"])
    return repo


# ---------------------------------------------------------------------------
# Inspect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inspect_returns_profile_with_recommendations(
    v1_client, seed_user_with_token, tmp_path
) -> None:
    repo = _make_repo(tmp_path)
    _, raw = seed_user_with_token
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.post(
        "/v1/onboarding/inspect",
        headers=headers,
        json={"source": f"file://{repo}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_kind"] == "file"
    assert body["primary_language"] == "typescript"
    assert "Next.js" in body["frameworks"]
    assert "Vitest" in body["test_frameworks"]
    assert "Playwright" in body["test_frameworks"]
    assert body["has_ci"] is True
    assert "github-actions" in body["ci_systems"]
    # Playwright in deps → hosted-e2e-regression should be recommended.
    assert "pr-and-ci-gate" in body["recommended_workflows"]
    assert "hosted-e2e-regression" in body["recommended_workflows"]


@pytest.mark.asyncio
async def test_inspect_rejects_missing_path(v1_client, seed_user_with_token) -> None:
    _, raw = seed_user_with_token
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.post(
        "/v1/onboarding/inspect",
        headers=headers,
        json={"source": "file:///does/not/exist/xyz"},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["detail"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_inspect_requires_auth(v1_client) -> None:
    response = await v1_client.post(
        "/v1/onboarding/inspect", json={"source": "file:///tmp/whatever"}
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Install workflows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_workflows_writes_files_and_commits(
    v1_client, seed_workspace, tmp_path
) -> None:
    repo = _make_repo(tmp_path)
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    response = await v1_client.post(
        "/v1/onboarding/install-workflows",
        headers=headers,
        json={
            "workspace_id": str(workspace.id),
            "repo_source": f"file://{repo}",
            "workflow_ids": ["pr-and-ci-gate", "pipeline-self-heal"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["commit_made"] is True
    assert body["head_after"] != body["head_before"]
    installed_ids = sorted(a["id"] for a in body["installed"])
    assert installed_ids == ["pipeline-self-heal", "pr-and-ci-gate"]
    assert body["skipped"] == []

    # Files actually exist on disk.
    assert (repo / ".github" / "workflows" / "pr-and-ci-gate.yml").exists()
    assert (repo / ".github" / "workflows" / "pipeline-self-heal.yml").exists()
    assert (repo / ".ship" / "workflows" / "pr-and-ci-gate.md").exists()
    assert (repo / ".ship" / "lock.yaml").exists()
    lock = (repo / ".ship" / "lock.yaml").read_text(encoding="utf-8")
    assert "pr-and-ci-gate" in lock and "pipeline-self-heal" in lock

    # And HEAD really moved.
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    assert head == body["head_after"]


@pytest.mark.asyncio
async def test_install_workflows_registers_project_artifact_repo(
    v1_client, seed_workspace, tmp_path
) -> None:
    """Installing workflows should auto-register the project's `project`
    artifact repo so the resolver can read .ship/artifacts/ back without a
    second manual step."""
    repo = _make_repo(tmp_path)
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    repo_source = f"file://{repo}"

    # First call: empty list, then install, then list shows one row.
    before = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/artifact-repos", headers=headers
    )
    assert before.status_code == 200
    assert before.json() == []

    install = await v1_client.post(
        "/v1/onboarding/install-workflows",
        headers=headers,
        json={
            "workspace_id": str(workspace.id),
            "repo_source": repo_source,
            "workflow_ids": ["pr-and-ci-gate"],
        },
    )
    assert install.status_code == 200, install.text

    after = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/artifact-repos", headers=headers
    )
    assert after.status_code == 200
    rows = after.json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "project"
    assert rows[0]["url"] == repo_source
    assert rows[0]["last_sync_error"] is None  # file:// is read inline

    # Re-running install against the same repo must not duplicate the row.
    again = await v1_client.post(
        "/v1/onboarding/install-workflows",
        headers=headers,
        json={
            "workspace_id": str(workspace.id),
            "repo_source": repo_source,
            "workflow_ids": ["pr-and-ci-gate"],
        },
    )
    assert again.status_code == 200, again.text
    after2 = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/artifact-repos", headers=headers
    )
    assert len(after2.json()) == 1


@pytest.mark.asyncio
async def test_install_workflows_accepts_remote_repo_without_sync_marker(
    v1_client, seed_workspace, tmp_path
) -> None:
    """Non-file:// URLs are accepted by the schema but are no longer flagged
    with a sync-worker placeholder; the legacy git-sync worker is gone and
    the upcoming GitHub App integration will populate these rows via
    installation IDs instead. The row stays clean (last_sync_error is None)
    so the settings page can decide whether to show "not yet wired" copy."""
    repo = _make_repo(tmp_path)
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    remote = "https://github.com/example/aurora"

    seed = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/artifact-repos",
        headers=headers,
        json={"kind": "project", "url": remote, "default_branch": "main"},
    )
    assert seed.status_code == 201
    listing = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/artifact-repos", headers=headers
    )
    rows = [r for r in listing.json() if r["url"] == remote]
    assert len(rows) == 1
    assert rows[0]["last_sync_error"] is None
    assert rows[0]["last_sync_at"] is None
    _ = repo  # silence unused — repo only seeded for fixture parity.


@pytest.mark.asyncio
async def test_install_workflows_reports_unknown_ids(
    v1_client, seed_workspace, tmp_path
) -> None:
    repo = _make_repo(tmp_path)
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.post(
        "/v1/onboarding/install-workflows",
        headers=headers,
        json={
            "workspace_id": str(workspace.id),
            "repo_source": f"file://{repo}",
            "workflow_ids": ["pr-and-ci-gate", "definitely-not-a-thing"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert [s["id"] for s in body["skipped"]] == ["definitely-not-a-thing"]


@pytest.mark.asyncio
async def test_install_workflows_requires_admin(
    v1_client, seed_user_with_token, tmp_path
) -> None:
    """Token-holder is not a member of any workspace → 404."""
    repo = _make_repo(tmp_path)
    _, raw = seed_user_with_token
    headers = {"Authorization": f"Bearer {raw}"}
    import uuid

    response = await v1_client.post(
        "/v1/onboarding/install-workflows",
        headers=headers,
        json={
            "workspace_id": str(uuid.uuid4()),
            "repo_source": f"file://{repo}",
            "workflow_ids": ["pr-and-ci-gate"],
        },
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Seed knowledge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_seed_knowledge_writes_three_docs_and_commits(
    v1_client, seed_workspace, tmp_path
) -> None:
    repo = _make_repo(tmp_path)
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    response = await v1_client.post(
        "/v1/onboarding/seed-knowledge",
        headers=headers,
        json={
            "workspace_id": str(workspace.id),
            "repo_source": f"file://{repo}",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["commit_made"] is True
    slugs = sorted(d["slug"] for d in body["docs"])
    assert slugs == ["brandbook", "code-style", "testing"]

    # Files exist with non-trivial content.
    for slug in slugs:
        path = repo / ".ship" / "knowledge" / f"{slug}.md"
        assert path.exists()
        text = path.read_text(encoding="utf-8")
        assert len(text) > 200
    brand = (repo / ".ship" / "knowledge" / "brandbook.md").read_text(encoding="utf-8")
    assert "Aurora" in brand


@pytest.mark.asyncio
async def test_seed_knowledge_subset(v1_client, seed_workspace, tmp_path) -> None:
    repo = _make_repo(tmp_path)
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    response = await v1_client.post(
        "/v1/onboarding/seed-knowledge",
        headers=headers,
        json={
            "workspace_id": str(workspace.id),
            "repo_source": f"file://{repo}",
            "bucket_slugs": ["brandbook"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert [d["slug"] for d in body["docs"]] == ["brandbook"]
    assert (repo / ".ship" / "knowledge" / "brandbook.md").exists()
    assert not (repo / ".ship" / "knowledge" / "code-style.md").exists()


@pytest.mark.asyncio
async def test_seed_knowledge_unknown_slug_is_400(
    v1_client, seed_workspace, tmp_path
) -> None:
    repo = _make_repo(tmp_path)
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    response = await v1_client.post(
        "/v1/onboarding/seed-knowledge",
        headers=headers,
        json={
            "workspace_id": str(workspace.id),
            "repo_source": f"file://{repo}",
            "bucket_slugs": ["does-not-exist"],
        },
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unknown_bucket"
