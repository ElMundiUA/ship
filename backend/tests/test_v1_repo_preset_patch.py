"""Per-repo preset PATCH endpoint (B9).

Covers the three interesting cases: happy path (preset changes,
seed adds missing lanes), reshape path (bound lanes get re-flagged
to match the new preset), and the legacy-id-collapse path (P5-01:
``KNOWN_PRESETS`` shrank to ``("default",)`` so the old
"unknown preset → 422" gate is now "legacy id → normalize to
default").
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def seed_preset_workspace(db_session, seed_workspace):
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.db.models.pipelines import Pipeline

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=777_002,
        account_login="acme",
        account_type="Organization",
        repository_selection="all",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=777_007,
        full_name="acme/preset-switch",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/preset-switch",
        activated_at=datetime.now(timezone.utc),
        preset="adoption-minimum",
    )
    db_session.add(repo)
    await db_session.flush()

    # adoption-minimum enables pr_review + code_map; seed a matching pair.
    db_session.add(
        Pipeline(
            workspace_id=workspace.id,
            repo_id=repo.id,
            lane_id="pr_review",
            name="PR review",
            workflow_id="pr-and-ci-gate",
            enabled=True,
            config={},
        )
    )
    db_session.add(
        Pipeline(
            workspace_id=workspace.id,
            repo_id=repo.id,
            lane_id="code_map",
            name="Code map",
            workflow_id="knowledge-intake",
            enabled=True,
            config={},
        )
    )
    await db_session.flush()
    return raw, workspace, repo


@pytest.mark.asyncio
async def test_patch_preset_updates_and_adds_missing_lanes(
    v1_client, db_session, seed_preset_workspace
) -> None:
    from backend.app.db.models.pipelines import Pipeline

    raw, workspace, repo = seed_preset_workspace
    ws_id = workspace.id
    repo_id = repo.id

    response = await v1_client.patch(
        f"/v1/workspaces/{ws_id}/repos/{repo_id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"preset": "web-app"},
    )
    assert response.status_code == 200, response.text
    # Post-P5-01 ``"web-app"`` collapses to ``"default"`` before
    # being persisted on the repo row.
    assert response.json()["preset"] == "default"

    db_session.expire_all()
    kinds = {
        row.lane_id
        for row in (
            await db_session.execute(
                select(Pipeline).where(Pipeline.workspace_id == ws_id)
            )
        ).scalars()
    }
    # The canonical ``"default"`` preset enables every default-enabled
    # recipe (pr_review, daily_standup, code_map, tech_debt) — the
    # additive seed materializes any rows missing from the prior
    # adoption-minimum-seeded pair.
    assert {"pr_review", "code_map", "daily_standup", "tech_debt"}.issubset(kinds)


@pytest.mark.asyncio
async def test_patch_preset_reshape_flips_enabled_flags(
    v1_client, db_session, seed_preset_workspace
) -> None:
    from backend.app.db.models.pipelines import Pipeline

    raw, workspace, repo = seed_preset_workspace
    ws_id = workspace.id
    repo_id = repo.id

    # Pre-enable code_map (already true) and pr_review (already true).
    # Flip to adoption-minimum again with reshape=true after the
    # operator hand-toggled pr_review off — reshape should put it
    # back on.
    pr_review = (
        await db_session.execute(
            select(Pipeline).where(
                Pipeline.workspace_id == ws_id, Pipeline.lane_id == "pr_review"
            )
        )
    ).scalars().one()
    pr_review.enabled = False
    await db_session.flush()

    response = await v1_client.patch(
        f"/v1/workspaces/{ws_id}/repos/{repo_id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"preset": "adoption-minimum", "reshape": True},
    )
    assert response.status_code == 200, response.text

    db_session.expire_all()
    pr_review_after = (
        await db_session.execute(
            select(Pipeline).where(
                Pipeline.workspace_id == ws_id, Pipeline.lane_id == "pr_review"
            )
        )
    ).scalars().one()
    assert pr_review_after.enabled is True


@pytest.mark.asyncio
async def test_patch_preset_accepts_legacy_collapses_to_default(
    v1_client, seed_preset_workspace
) -> None:
    """Post-P5-01 the PATCH endpoint stops 422-ing on unknown preset
    ids. Legacy ids (``"adoption-minimum"`` here, but every entry in
    ``LEGACY_PRESETS``) pass through and collapse to ``"default"``
    before being persisted."""
    raw, workspace, repo = seed_preset_workspace

    response = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/repos/{repo.id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"preset": "monorepo"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["preset"] == "default"
