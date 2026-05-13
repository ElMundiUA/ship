"""Tests for the tracker-FSM catalog API (Wizard v2 iter 7).

Covers:

- GET returns canonical ``SHIP_DEFAULT_STATES`` + tracker mapping
  hints even when the workspace has no integrations and no repos.
- ``?repos=false`` skips the per-repo render loop entirely (used
  by the settings page's "top summary" block).
- Per-repo render picks the per-repo binding when one exists,
  otherwise falls back to the workspace default, otherwise marks
  the repo as ``source="none"`` with ``tracker_kind=null``.
- Markdown actually embeds the tracker kind — we don't rely on
  an exact snapshot (that would glue the tests to copy changes);
  we just assert the relevant kind shows up and SHIP_DEFAULT_STATES
  ids are rendered as ``## States`` entries.
- RBAC: any workspace member (including viewers) can read; a user
  outside the workspace gets 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


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
        external_id=30_071_000,
        full_name="acme/fsm-demo",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/fsm-demo",
        description=None,
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    await db_session.commit()
    return raw, workspace, repo


async def _seed_workspace_tracker(db_session, workspace_id, kind, config):
    from backend.app.db.models.tenancy import Integration

    row = Integration(
        workspace_id=workspace_id,
        repo_id=None,
        kind=kind,
        config=config,
        status="ok",
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.commit()
    return row


async def _seed_repo_tracker(db_session, workspace_id, repo_id, kind, config):
    from backend.app.db.models.tenancy import Integration

    row = Integration(
        workspace_id=workspace_id,
        repo_id=repo_id,
        kind=kind,
        config=config,
        status="ok",
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.commit()
    return row


@pytest.mark.asyncio
async def test_get_returns_canonical_states_and_hints(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/tracker-fsm",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["install_path"] == ".ship/tracker-fsm.md"

    # Canonical states — we don't hard-code the count (ops can add one
    # via RFC) but we anchor on ids that must be present for the FSM
    # to mean anything.
    ids = {s["id"] for s in body["states"]}
    assert {
        "triage",
        "ready",
        "in_progress",
        "in_review",
        "rework",
        "needs_info",
        "blocked",
        "merged",
        "done",
        "cancelled",
    }.issubset(ids)

    # Transitions survive pydantic round-trip as lists (service layer
    # returns tuples; schema flattens them).
    in_review = next(s for s in body["states"] if s["id"] == "in_review")
    assert "rework" in in_review["transitions"]
    assert "merged" in in_review["transitions"]

    # Mapping hints carry the three tracker kinds we render today.
    assert set(body["mapping_hints"].keys()) == {"linear", "github", "jira"}
    assert body["mapping_hints"]["linear"]["in_review"]

    # No integrations yet.
    assert body["workspace_default_kind"] is None
    # No activated repos in this workspace → empty repos list.
    assert body["repos"] == []


@pytest.mark.asyncio
async def test_get_skips_repos_when_requested(
    v1_client, db_session, seeded_repo
) -> None:
    raw, workspace, repo = seeded_repo
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/tracker-fsm?repos=false",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Even though a repo exists, we asked to skip the render loop.
    assert body["repos"] == []
    # States/hints still there.
    assert len(body["states"]) >= 10


@pytest.mark.asyncio
async def test_repo_render_prefers_per_repo_binding(
    v1_client, db_session, seeded_repo
) -> None:
    raw, workspace, repo = seeded_repo
    # Workspace default is Linear, per-repo override is Jira.
    await _seed_workspace_tracker(
        db_session, workspace.id, "linear", {"team_id": "WS-TEAM"}
    )
    await _seed_repo_tracker(
        db_session, workspace.id, repo.id, "jira", {"project": "WIDG"}
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/tracker-fsm",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_default_kind"] == "linear"
    assert len(body["repos"]) == 1
    card = body["repos"][0]
    assert card["full_name"] == repo.full_name
    assert card["tracker_kind"] == "jira"
    assert card["source"] == "repo"
    # Rendered markdown mentions Jira and carries the state list.
    assert "jira" in card["markdown"].lower()
    assert "## States" in card["markdown"]
    # Override note surfaces workspace default too.
    assert "linear" in card["markdown"].lower()


@pytest.mark.asyncio
async def test_repo_render_falls_back_to_workspace_default(
    v1_client, db_session, seeded_repo
) -> None:
    raw, workspace, repo = seeded_repo
    await _seed_workspace_tracker(
        db_session, workspace.id, "linear", {"team_id": "WS-TEAM"}
    )

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/tracker-fsm",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    card = resp.json()["repos"][0]
    assert card["tracker_kind"] == "linear"
    assert card["source"] == "workspace"


@pytest.mark.asyncio
async def test_repo_render_marks_none_when_nothing_bound(
    v1_client, seeded_repo
) -> None:
    raw, workspace, repo = seeded_repo
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/tracker-fsm",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_default_kind"] is None
    card = body["repos"][0]
    assert card["tracker_kind"] is None
    assert card["source"] == "none"
    # Still renders — the markdown tells ops how to bind one.
    assert "not connected" in card["markdown"].lower()


@pytest.mark.asyncio
async def test_get_rejects_non_members(
    v1_client, db_session, seed_workspace
) -> None:
    """Non-members see 404 so workspace existence stays hidden."""
    import secrets

    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import ApiToken, User

    _, _owner_raw, workspace = seed_workspace

    outsider = User(email=f"fsm-outsider-{uuid.uuid4().hex[:6]}@example.com")
    db_session.add(outsider)
    await db_session.flush()
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=outsider.id,
            name="fsm-outsider-pat",
            hashed_secret=_hash_token(raw),
            prefix=PAT_PREFIX,
            scopes=[],
        )
    )
    await db_session.flush()

    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/tracker-fsm",
        headers={"Authorization": f"Bearer {raw}"},
    )
    # ``_require_membership`` raises 404 for non-members so the
    # workspace id doesn't leak to outsiders.
    assert resp.status_code == 404
