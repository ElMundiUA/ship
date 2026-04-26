"""One-shot knowledge seed endpoint (Phase 2a).

Exercises ``POST /v1/workspaces/{ws}/repos/{repo}/knowledge_seed``:
opens a single PR dropping ``.ship/knowledge/<slug>.md`` starter
buckets into the tenant repo. Unlike ``install_bundle`` this endpoint
does **not** touch ``.github/workflows/``; buckets are pure markdown
that the knowledge lister scans on read.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seed_workspace_with_repo(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=888_002,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=42_888_002,
        full_name="acme/knowledge-target",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/knowledge-target",
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()
    return raw, workspace, install, repo


def test_knowledge_starter_files_default_seeds_everything() -> None:
    from backend.app.services.catalog import (
        knowledge_starter_slugs,
        knowledge_starter_files,
    )

    files = knowledge_starter_files(None)
    paths = [p for p, _ in files]
    slugs = knowledge_starter_slugs()
    assert paths == [f".ship/knowledge/{s}.md" for s in slugs]
    assert ".ship/knowledge/code-style.md" in paths
    assert ".ship/knowledge/ui-runbook.md" in paths
    assert ".ship/knowledge/ship-recipes/flow-pr-self-review.md" in paths
    assert ".ship/knowledge/ship-recipes/role-ba.md" not in paths
    for _, content in files:
        # Sanity: the starter must be non-empty markdown. If we ever
        # ship a blank template by mistake, the knowledge lister would
        # happily index an empty bucket and the UI would look broken.
        assert content.strip().startswith("#"), "starter body must start with a heading"
        assert len(content) > 200, "starter body is suspiciously short"


def test_knowledge_starter_files_filters_selection() -> None:
    from backend.app.services.catalog import knowledge_starter_files

    files = knowledge_starter_files(["code-style", "ship-recipes/flow-pr-self-review"])
    assert [p for p, _ in files] == [
        ".ship/knowledge/code-style.md",
        ".ship/knowledge/ship-recipes/flow-pr-self-review.md",
    ]
    assert "Source: `pattern/flow-pr-self-review`" in files[1][1]


def test_knowledge_starter_files_rejects_unknown_slug() -> None:
    from backend.app.services.catalog import (
        CatalogError,
        knowledge_starter_files,
    )

    with pytest.raises(CatalogError):
        knowledge_starter_files(["does-not-exist"])


def test_knowledge_starter_files_dedups_and_trims() -> None:
    from backend.app.services.catalog import knowledge_starter_files

    files = knowledge_starter_files(
        ["code-style", " code-style ", "code-style"]
    )
    assert [p for p, _ in files] == [".ship/knowledge/code-style.md"]


@pytest.mark.asyncio
async def test_knowledge_seed_is_deprecated(
    v1_client, seed_workspace_with_repo
) -> None:
    raw, workspace, _install, repo = seed_workspace_with_repo

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/knowledge_seed",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["code"] == "repo_knowledge_deprecated"


@pytest.mark.asyncio
async def test_knowledge_seed_deprecation_ignores_custom_selection(
    v1_client, seed_workspace_with_repo
) -> None:
    raw, workspace, _install, repo = seed_workspace_with_repo

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/knowledge_seed",
        headers={"Authorization": f"Bearer {raw}"},
        json={"selection": ["ui-runbook", "ship-recipes/flow-pr-self-review"]},
    )
    assert response.status_code == 410, response.text
    assert response.json()["detail"]["code"] == "repo_knowledge_deprecated"


@pytest.mark.asyncio
async def test_knowledge_seed_rejects_unknown_slug(
    v1_client, seed_workspace_with_repo
) -> None:
    raw, workspace, _install, repo = seed_workspace_with_repo

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/knowledge_seed",
        headers={"Authorization": f"Bearer {raw}"},
        json={"selection": ["does-not-exist"]},
    )
    assert response.status_code == 410, response.text


# NOTE: the "GitHub App missing" 412 branch is already exercised by
# ``test_v1_install_bundle.test_install_bundle_requires_github_app`` and
# the knowledge-seed endpoint copies that branch verbatim from the
# bundle install. A dedicated test here would require disentangling the
# ``workspace_repos → github_installations`` FK (cascade deletes the
# repo on install removal) which adds fixture complexity for zero
# extra coverage — revisit if the two endpoints ever diverge.
