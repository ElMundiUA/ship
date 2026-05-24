"""Variant A — explicit project→repo dispatch routing.

Covers ``dispatcher._pick_dispatch_repo`` precedence (binding > default >
unresolved), the ``no_target_repo`` clarification letter, and the
``/repo-routing`` API. Replaces the retired name-heuristic +
oldest-activated fallback that froze askslayer (2026-05-24).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
    WorkspaceRepoRouting,
)
from backend.app.services.dispatcher import (
    _file_no_target_repo_letter,
    _pick_dispatch_repo,
)


async def _seed_repo(db_session, ws_id, full_name: str, ext: int) -> WorkspaceRepo:
    now = datetime.now(timezone.utc)
    install = GitHubInstallation(
        workspace_id=ws_id,
        installation_id=ext,
        account_id=ext,
        account_login=full_name.split("/")[0],
        account_type="Organization",
        installed_at=now,
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=ws_id,
        installation_id=install.id,
        provider="github",
        external_id=ext,
        full_name=full_name,
        default_branch="main",
        private=False,
        html_url=f"https://github.com/{full_name}",
        activated_at=now,
    )
    db_session.add(repo)
    await db_session.flush()
    return repo


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


# ---------------------------------------------------------------------------
# _pick_dispatch_repo precedence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pick_binding_wins(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    back = await _seed_repo(db_session, ws.id, "acme/back", 1)
    web = await _seed_repo(db_session, ws.id, "acme/web", 2)
    db_session.add(WorkspaceRepoRouting(workspace_id=ws.id, project_native_id=None, repo_id=web.id))
    db_session.add(WorkspaceRepoRouting(workspace_id=ws.id, project_native_id="PRJ-1", repo_id=back.id))
    await db_session.flush()
    picked = await _pick_dispatch_repo(db_session, workspace_id=ws.id, project_id="PRJ-1")
    assert picked is not None
    repo, _install, route = picked
    assert repo.id == back.id and route == "binding"


@pytest.mark.asyncio
async def test_pick_falls_back_to_default(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    back = await _seed_repo(db_session, ws.id, "acme/back", 1)
    db_session.add(WorkspaceRepoRouting(workspace_id=ws.id, project_native_id=None, repo_id=back.id))
    await db_session.flush()
    # Unbound project → default.
    picked = await _pick_dispatch_repo(db_session, workspace_id=ws.id, project_id="PRJ-NEW")
    assert picked is not None
    repo, _install, route = picked
    assert repo.id == back.id and route == "default"
    # Projectless → default too.
    picked2 = await _pick_dispatch_repo(db_session, workspace_id=ws.id, project_id=None)
    assert picked2 is not None and picked2[0].id == back.id


@pytest.mark.asyncio
async def test_pick_unresolved_returns_none_once_configured(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    back = await _seed_repo(db_session, ws.id, "acme/back", 1)
    # Routing IS configured (a binding for another project, no default).
    db_session.add(WorkspaceRepoRouting(workspace_id=ws.id, project_native_id="PRJ-OTHER", repo_id=back.id))
    await db_session.flush()
    # Unbound project + no default → None (caller files a clarification;
    # no silent dump on an arbitrary repo).
    assert await _pick_dispatch_repo(db_session, workspace_id=ws.id, project_id="PRJ-X") is None
    assert await _pick_dispatch_repo(db_session, workspace_id=ws.id) is None


@pytest.mark.asyncio
async def test_pick_zero_config_transition_guard(db_session, seed_workspace) -> None:
    """A workspace with NO routing rows keeps the pre-Variant-A behaviour
    (oldest-activated) so the rollout can't break un-backfilled
    workspaces. Once any row exists the strict path takes over."""
    _, _, ws = seed_workspace
    oldest = await _seed_repo(db_session, ws.id, "acme/oldest", 1)
    await _seed_repo(db_session, ws.id, "acme/newer", 2)
    await db_session.flush()
    picked = await _pick_dispatch_repo(db_session, workspace_id=ws.id, project_id="PRJ-X")
    assert picked is not None
    repo, _install, route = picked
    assert repo.id == oldest.id and route == "transition_oldest"


@pytest.mark.asyncio
async def test_pick_deactivated_bound_repo_is_unresolved(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    back = await _seed_repo(db_session, ws.id, "acme/back", 1)
    db_session.add(WorkspaceRepoRouting(workspace_id=ws.id, project_native_id="PRJ-1", repo_id=back.id))
    await db_session.flush()
    back.activated_at = None  # deactivated / uninstalled
    await db_session.flush()
    assert await _pick_dispatch_repo(db_session, workspace_id=ws.id, project_id="PRJ-1") is None


@pytest.mark.asyncio
async def test_no_target_repo_letter_dedup(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    for _ in range(2):
        await _file_no_target_repo_letter(
            db_session,
            workspace_id=ws.id,
            ticket_ref="PAC-32",
            project_id=None,
            project_name=None,
        )
        await db_session.flush()
    rows = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == ws.id,
                InboxItem.intake_reason == "no_target_repo",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].type == "clarification"
    assert rows[0].intake_handle == "no-target-repo:PAC-32"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_set_default_and_bind(v1_client, db_session, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    back = await _seed_repo(db_session, ws.id, "acme/back", 1)
    web = await _seed_repo(db_session, ws.id, "acme/web", 2)
    await db_session.flush()

    # set default
    r = await v1_client.put(
        f"/v1/workspaces/{ws.id}/repo-routing/default",
        headers=_auth(raw),
        json={"repo_id": str(back.id)},
    )
    assert r.status_code == 200, r.text
    assert r.json()["default_repo_id"] == str(back.id)

    # bind a project
    r = await v1_client.put(
        f"/v1/workspaces/{ws.id}/repo-routing/projects/PRJ-1",
        headers=_auth(raw),
        json={"repo_id": str(web.id)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["default_repo_id"] == str(back.id)
    assert any(
        b["project_native_id"] == "PRJ-1" and b["repo_id"] == str(web.id)
        for b in body["bindings"]
    )

    # GET reflects both
    r = await v1_client.get(
        f"/v1/workspaces/{ws.id}/repo-routing", headers=_auth(raw)
    )
    assert r.status_code == 200
    assert r.json()["default_repo_id"] == str(back.id)


@pytest.mark.asyncio
async def test_api_default_rejects_foreign_repo(v1_client, db_session, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    await _seed_repo(db_session, ws.id, "acme/back", 1)
    await db_session.flush()
    r = await v1_client.put(
        f"/v1/workspaces/{ws.id}/repo-routing/default",
        headers=_auth(raw),
        json={"repo_id": str(uuid.uuid4())},
    )
    assert r.status_code == 404
