"""C2 workspace-rollup — collapse per-ticket runner-fail spam into one
workspace-level letter when the whole workspace is down (2026-05-21),
made repo-scoped (2026-05-24).

A single root cause (GitHub Actions billing block, org Actions disabled,
revoked secret) kills every run at preflight, so the per-ticket detector
filed a separate ``runner_fail_loop`` letter per stuck ticket — caught on
askslayer/Visitor where one billing block spammed PAC-33/34/35/36.

``_looks_like_workspace_runner_fail`` now fires only when EVERY activated
repo is dead (≥THRESHOLD distinct ticket dispatches in the window with
**zero** finishes among them, scheduled routines excluded). One dead repo
beside a healthy/idle repo stays repo-local so it can't freeze the whole
workspace's self-heal (askslayer/visitor-web missing CURSOR_API_KEY froze
visitor-back's healthy queue for a day). ``_file_workspace_runner_fail_blocker``
files one deduped letter.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.tenancy import AuditLog
from backend.app.services.fsm_self_heal import (
    WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD,
    _file_workspace_runner_fail_blocker,
    _looks_like_workspace_runner_fail,
)

_DEFAULT_REPO = "acme/alpha"


async def _seed_repo(db_session, ws, full_name: str, external_id: int) -> None:
    """Seed one activated GitHub repo on the workspace."""
    now = datetime.now(timezone.utc)
    install = GitHubInstallation(
        workspace_id=ws.id,
        installation_id=external_id,
        account_id=external_id,
        account_login=full_name.split("/")[0],
        account_type="Organization",
        installed_at=now,
    )
    db_session.add(install)
    await db_session.flush()
    db_session.add(
        WorkspaceRepo(
            workspace_id=ws.id,
            installation_id=install.id,
            provider="github",
            external_id=external_id,
            full_name=full_name,
            default_branch="main",
            private=False,
            html_url=f"https://github.com/{full_name}",
            activated_at=now,
        )
    )
    await db_session.flush()


def _dispatch(
    workspace_id, target: str, *, mins: int = 30, repo: str = _DEFAULT_REPO
) -> AuditLog:
    return AuditLog(
        workspace_id=workspace_id,
        action="agent_run.dispatch",
        target_kind="ticket",
        target_id=target,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=mins),
        payload={"ticket_ref": target, "repo": repo},
    )


def _finish(workspace_id, target: str, *, mins: int = 20) -> AuditLog:
    return AuditLog(
        workspace_id=workspace_id,
        action="agent_run.finish",
        target_kind="agent_run",
        target_id=target,
        created_at=datetime.now(timezone.utc) - timedelta(minutes=mins),
        payload={"ticket_ref": target, "outcome": "ready_next_step"},
    )


@pytest.mark.asyncio
async def test_detects_workspace_wide_runner_fail(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _seed_repo(db_session, ws, _DEFAULT_REPO, 1)
    for i in range(WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD):
        db_session.add(_dispatch(ws.id, f"PAC-{30 + i}"))
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is True


@pytest.mark.asyncio
async def test_one_dead_repo_beside_idle_is_not_workspace_kill(
    db_session, seed_workspace
) -> None:
    """The regression: visitor-web dead (missing key), visitor-back idle.
    One dead repo of two activated must NOT pause the whole workspace."""
    _, _, ws = seed_workspace
    await _seed_repo(db_session, ws, "acme/dead", 1)
    await _seed_repo(db_session, ws, "acme/idle", 2)
    # ``acme/dead`` racks up failing dispatches; ``acme/idle`` sees none.
    for i in range(WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD):
        db_session.add(_dispatch(ws.id, f"PAC-{30 + i}", repo="acme/dead"))
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is False


@pytest.mark.asyncio
async def test_all_repos_dead_is_workspace_kill(db_session, seed_workspace) -> None:
    """When EVERY activated repo is dead, it's a true workspace-wide kill."""
    _, _, ws = seed_workspace
    await _seed_repo(db_session, ws, "acme/one", 1)
    await _seed_repo(db_session, ws, "acme/two", 2)
    for i in range(WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD):
        db_session.add(_dispatch(ws.id, f"ONE-{30 + i}", repo="acme/one"))
        db_session.add(_dispatch(ws.id, f"TWO-{30 + i}", repo="acme/two"))
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is True


@pytest.mark.asyncio
async def test_any_finish_means_repo_is_alive(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _seed_repo(db_session, ws, _DEFAULT_REPO, 1)
    for i in range(WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD):
        db_session.add(_dispatch(ws.id, f"PAC-{30 + i}"))
    # one ticket DID finish → the repo isn't dead → not a kill
    db_session.add(_finish(ws.id, "PAC-30"))
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is False


@pytest.mark.asyncio
async def test_scheduled_routines_dont_count(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _seed_repo(db_session, ws, _DEFAULT_REPO, 1)
    # self-heal / digest ticks dispatch but aren't tickets — a quiet
    # workspace must not trip the rollup.
    for tgt in ("self-heal", "daily-digest", "weekly-audit"):
        db_session.add(_dispatch(ws.id, tgt))
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is False


@pytest.mark.asyncio
async def test_below_threshold_does_not_fire(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _seed_repo(db_session, ws, _DEFAULT_REPO, 1)
    for i in range(WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD - 1):
        db_session.add(_dispatch(ws.id, f"PAC-{30 + i}"))
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is False


@pytest.mark.asyncio
async def test_old_dispatches_outside_window_ignored(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _seed_repo(db_session, ws, _DEFAULT_REPO, 1)
    for i in range(WORKSPACE_RUNNER_FAIL_ROLLUP_THRESHOLD):
        db_session.add(_dispatch(ws.id, f"PAC-{30 + i}", mins=60 * 9))  # 9h ago
    await db_session.flush()
    assert await _looks_like_workspace_runner_fail(db_session, ws.id) is False


@pytest.mark.asyncio
async def test_files_one_letter_idempotent(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace
    await _file_workspace_runner_fail_blocker(db_session, ws.id, 4)
    await db_session.flush()
    await _file_workspace_runner_fail_blocker(db_session, ws.id, 4)
    await db_session.flush()

    count = await db_session.scalar(
        select(func.count(InboxItem.id)).where(
            InboxItem.workspace_id == ws.id,
            InboxItem.intake_reason == "runner_fail_workspace",
        )
    )
    assert int(count or 0) == 1

    row = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.workspace_id == ws.id,
                InboxItem.intake_handle == "runner-fail-workspace",
            )
        )
    ).scalar_one()
    assert row.type == "blocker"
    ai = (row.payload or {}).get("action_items") or []
    assert {a.get("id") for a in ai} == {
        "fixed_resume", "pause_workspace", "already_handled"
    }
