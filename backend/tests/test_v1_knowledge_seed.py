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
        KNOWLEDGE_STARTERS,
        knowledge_starter_files,
    )

    files = knowledge_starter_files(None)
    paths = [p for p, _ in files]
    assert paths == [f".ship/knowledge/{s}.md" for s in KNOWLEDGE_STARTERS]
    for _, content in files:
        # Sanity: the starter must be non-empty markdown. If we ever
        # ship a blank template by mistake, the knowledge lister would
        # happily index an empty bucket and the UI would look broken.
        assert content.strip().startswith("#"), "starter body must start with a heading"
        assert len(content) > 200, "starter body is suspiciously short"


def test_knowledge_starter_files_filters_selection() -> None:
    from backend.app.services.catalog import knowledge_starter_files

    files = knowledge_starter_files(["code-style"])
    assert [p for p, _ in files] == [".ship/knowledge/code-style.md"]


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
async def test_knowledge_seed_opens_single_pr_for_default_selection(
    monkeypatch, v1_client, db_session, seed_workspace_with_repo
) -> None:
    from backend.app.integrations.github.workflows import StarterWorkflowPR

    raw, workspace, _install, repo = seed_workspace_with_repo
    captured: dict[str, object] = {}

    async def _commit(
        repo, install, *, files, title, branch_label, pr_body_header, settings,
        return_url=None, client=None,
    ):
        captured["files"] = [p for p, _ in files]
        captured["branch_label"] = branch_label
        captured["title"] = title
        captured["return_url"] = return_url
        return StarterWorkflowPR(
            pr_url="https://github.com/acme/knowledge-target/pull/77",
            pr_number=77,
            branch="ship/bundle-knowledge-seed-456",
        )

    monkeypatch.setattr(
        "backend.app.integrations.github.workflows.commit_bundle_pr", _commit
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/knowledge_seed",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pr_number"] == 77
    assert body["pr_url"].endswith("/pull/77")
    assert body["selection"] == ["code-style", "ui-runbook"]
    assert ".ship/knowledge/code-style.md" in body["files"]
    assert ".ship/knowledge/ui-runbook.md" in body["files"]
    # No workflows should get committed by the knowledge seed path.
    assert not any(
        p.startswith(".github/workflows/") for p in body["files"]
    )
    assert captured["branch_label"] == "knowledge-seed"
    assert "starter knowledge" in str(captured["title"]).lower()


@pytest.mark.asyncio
async def test_knowledge_seed_honours_custom_selection(
    monkeypatch, v1_client, db_session, seed_workspace_with_repo
) -> None:
    from backend.app.integrations.github.workflows import StarterWorkflowPR

    raw, workspace, _install, repo = seed_workspace_with_repo

    async def _commit(
        repo, install, *, files, title, branch_label, pr_body_header, settings,
        return_url=None, client=None,
    ):
        return StarterWorkflowPR(
            pr_url="https://github.com/acme/knowledge-target/pull/78",
            pr_number=78,
            branch="ship/bundle-knowledge-seed-457",
        )

    monkeypatch.setattr(
        "backend.app.integrations.github.workflows.commit_bundle_pr", _commit
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/knowledge_seed",
        headers={"Authorization": f"Bearer {raw}"},
        json={"selection": ["ui-runbook"]},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["selection"] == ["ui-runbook"]
    assert body["files"] == [".ship/knowledge/ui-runbook.md"]


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
    assert response.status_code == 422, response.text


# NOTE: the "GitHub App missing" 412 branch is already exercised by
# ``test_v1_install_bundle.test_install_bundle_requires_github_app`` and
# the knowledge-seed endpoint copies that branch verbatim from the
# bundle install. A dedicated test here would require disentangling the
# ``workspace_repos → github_installations`` FK (cascade deletes the
# repo on install removal) which adds fixture complexity for zero
# extra coverage — revisit if the two endpoints ever diverge.
