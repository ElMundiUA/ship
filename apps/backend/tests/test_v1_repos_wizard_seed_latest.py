"""Tests for ``GET /v1/workspaces/{ws}/repos/{repo}/wizard_seed/latest``.

The route replays the most recent ``repo.wizard_seed`` audit log row
as a :class:`backend.app.api.v1.routes.repos.WizardSeedOut`. It backs
the post-onboarding "What just happened" page (P5-09) when the
sessionStorage cache is empty (tab reloaded, opened on another
device, etc.).

Coverage:

- 200 happy path round-trips every WizardSeedOut field — including
  the nested ``codeowners`` and ``intel`` blocks — from the audit
  payload.
- 404 when the repo has never been wizard-seeded.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def seeded_repo(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=900_701,
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
        external_id=30_032_900,
        full_name="acme/done-target",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/done-target",
        description=None,
        activated_at=datetime.now(timezone.utc),
        preset="default",
    )
    db_session.add(repo)
    await db_session.flush()
    await db_session.commit()
    return raw, workspace, install, repo


@pytest.mark.asyncio
async def test_get_latest_wizard_seed_replays_full_payload(
    v1_client, db_session, seeded_repo
) -> None:
    """Every field on the latest audit row deserialises into ``WizardSeedOut``."""
    from backend.app.db.models.tenancy import AuditLog

    raw, workspace, _install, repo = seeded_repo

    intel_id = str(uuid.uuid4())
    audit_payload = {
        "presets": ["default"],
        "knowledge_slugs": ["onboarding-101"],
        "tracker_kind": "linear",
        "tracker_source": "repo",
        "files": [".ship/config.yml", ".github/workflows/ship.yml"],
        "pr_number": 42,
        "pr_url": "https://github.com/acme/done-target/pull/42",
        "branch": "ship/wizard-default-1",
        "run_token_rotated": True,
        "run_token_prefix": "shp_abcd1234",
        "bundle_version": 7,
        "bundle_hash": "deadbeefcafe",
        "synthetic_lanes_created": 3,
        "codeowners": {
            "file_found": True,
            "rules_count": 4,
            "routing_rules_created": 1,
            "unresolved_owners": ["@ext-contractor"],
        },
        "intel": {
            "enqueued": False,
            "job_id": None,
            "intel_id": intel_id,
        },
    }

    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=None,
            actor_token_id=None,
            action="repo.wizard_seed",
            target_kind="workspace_repo",
            target_id=str(repo.id),
            payload=audit_payload,
        )
    )
    await db_session.flush()
    await db_session.commit()

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed/latest",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pr_number"] == 42
    assert body["pr_url"] == audit_payload["pr_url"]
    assert body["branch"] == audit_payload["branch"]
    assert body["files"] == audit_payload["files"]
    assert body["presets"] == ["default"]
    assert body["knowledge_slugs"] == ["onboarding-101"]
    assert body["tracker_kind"] == "linear"
    assert body["run_token_prefix"] == "shp_abcd1234"
    assert body["run_token_rotated"] is True
    assert body["synthetic_lanes_created"] == 3
    assert body["codeowners"] == audit_payload["codeowners"]
    assert body["intel"] == {
        "enqueued": False,
        "job_id": None,
        "intel_id": intel_id,
    }


@pytest.mark.asyncio
async def test_get_latest_wizard_seed_returns_most_recent(
    v1_client, db_session, seeded_repo
) -> None:
    """When multiple seed rows exist we serve the highest ``id``."""
    from backend.app.db.models.tenancy import AuditLog

    raw, workspace, _install, repo = seeded_repo

    for n in (1, 2, 3):
        db_session.add(
            AuditLog(
                workspace_id=workspace.id,
                action="repo.wizard_seed",
                target_kind="workspace_repo",
                target_id=str(repo.id),
                payload={
                    "presets": ["default"],
                    "knowledge_slugs": [],
                    "files": [],
                    "pr_number": n,
                    "pr_url": f"https://example/pull/{n}",
                    "branch": f"ship/seed-{n}",
                },
            )
        )
    await db_session.flush()
    await db_session.commit()

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed/latest",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pr_number"] == 3


@pytest.mark.asyncio
async def test_get_latest_wizard_seed_404_when_never_seeded(
    v1_client, seeded_repo
) -> None:
    raw, workspace, _install, repo = seeded_repo
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}/wizard_seed/latest",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_latest_wizard_seed_404_unknown_repo(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/repos/{uuid.uuid4()}/wizard_seed/latest",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 404
