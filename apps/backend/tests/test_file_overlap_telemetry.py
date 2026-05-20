"""Unit/integration tests for file-overlap telemetry (ELS-156)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from backend.app.db.models.tenancy import AuditLog, Org, Workspace
from backend.app.services.file_overlap_telemetry import (
    ACTION_HONOUR_SKIPPED,
    ACTION_HONOURED,
    ACTION_IGNORED,
    ACTION_WARNING,
    emit_file_overlap_warnings,
    evaluate_file_overlap_honour,
    intersect_warned_paths,
    normalize_repo_path,
    parse_github_pr_url,
    paths_from_pr_file_entries,
    weekly_file_overlap_metrics,
)


def test_normalize_repo_path_strips_dot_slash() -> None:
    assert normalize_repo_path("./src/a.py") == "src/a.py"
    assert normalize_repo_path("src\\b.py") == "src/b.py"


def test_paths_from_pr_file_entries_includes_previous_filename() -> None:
    paths = paths_from_pr_file_entries(
        [
            {"filename": "src/new.py", "previous_filename": "src/old.py"},
            {"filename": "src/other.py"},
        ]
    )
    assert paths == {"src/new.py", "src/old.py", "src/other.py"}


def test_intersect_warned_paths() -> None:
    touched = intersect_warned_paths(
        {"src/a.py", "src/b.py"},
        {"src/b.py", "src/c.py"},
    )
    assert touched == ["src/b.py"]


def test_parse_github_pr_url() -> None:
    parsed = parse_github_pr_url(
        "Done.\nPR: https://github.com/acme/ship/pull/42\n[Ship SDLC:role-developer]"
    )
    assert parsed == ("acme", "ship", 42)


@pytest.mark.asyncio
async def test_emit_file_overlap_warnings_skips_empty_paths(db_session) -> None:
    org = Org(slug=f"t-{uuid.uuid4().hex[:8]}", name="Org", plan="free")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(
        org_id=org.id,
        slug=f"t-{uuid.uuid4().hex[:8]}",
        name="Ws",
        settings={},
    )
    db_session.add(ws)
    await db_session.flush()

    emit_file_overlap_warnings(
        db_session,
        workspace_id=ws.id,
        ticket_ref="ELS-156",
        project_id="proj-1",
        run_id="run_abc",
        warnings=[{"overlap_kind": "hard", "paths": [], "pr_number": 10}],
    )
    await db_session.flush()
    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws.id,
                AuditLog.action == ACTION_WARNING,
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_weekly_metrics_honour_rate_null_when_no_outcomes(db_session) -> None:
    org = Org(slug=f"t-{uuid.uuid4().hex[:8]}", name="Org", plan="free")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(
        org_id=org.id,
        slug=f"t-{uuid.uuid4().hex[:8]}",
        name="Ws",
        settings={},
    )
    db_session.add(ws)
    await db_session.flush()
    db_session.add(
        AuditLog(
            workspace_id=ws.id,
            action=ACTION_WARNING,
            target_kind="ticket",
            target_id="ELS-1",
            payload={"run_id": "run_x"},
            created_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()
    metrics = await weekly_file_overlap_metrics(db_session, workspace_id=ws.id)
    assert metrics.warnings_fired == 1
    assert metrics.honoured == 0
    assert metrics.ignored == 0
    assert metrics.honour_rate is None


@pytest.mark.asyncio
async def test_evaluate_honour_honoured_when_pr_avoids_warned_paths(
    db_session, monkeypatch
) -> None:
    from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
    from backend.app.core.config import get_settings

    org = Org(slug=f"t-{uuid.uuid4().hex[:8]}", name="Org", plan="free")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(
        org_id=org.id,
        slug=f"t-{uuid.uuid4().hex[:8]}",
        name="Ws",
        settings={},
    )
    db_session.add(ws)
    await db_session.flush()

    run_id = "run_honour_test"
    emit_file_overlap_warnings(
        db_session,
        workspace_id=ws.id,
        ticket_ref="ELS-156",
        project_id="proj-1",
        run_id=run_id,
        warnings=[
            {
                "overlap_kind": "hard",
                "pr_number": 10,
                "paths": ["src/a.py"],
            }
        ],
    )
    await db_session.flush()

    from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo

    install = GitHubInstallation(
        workspace_id=ws.id,
        installation_id=123456,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=ws.id,
        installation_id=install.id,
        provider="github",
        external_id=1,
        full_name="acme/ship",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/ship",
        description=None,
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()

    async def _list_files(self, ref, *, limit=100):
        return [{"filename": "src/b.py"}]

    monkeypatch.setattr(GitHubCodeHost, "list_pull_request_files", _list_files)

    action = await evaluate_file_overlap_honour(
        db_session,
        workspace_id=ws.id,
        ticket_ref="ELS-156",
        run_id=run_id,
        fsm_stage="dev_implementation",
        comment="PR: https://github.com/acme/ship/pull/99",
        settings=get_settings(),
    )
    assert action == ACTION_HONOURED


@pytest.mark.asyncio
async def test_evaluate_honour_ignored_on_rename_touch(
    db_session, monkeypatch
) -> None:
    from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
    from backend.app.core.config import get_settings

    org = Org(slug=f"t-{uuid.uuid4().hex[:8]}", name="Org", plan="free")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(
        org_id=org.id,
        slug=f"t-{uuid.uuid4().hex[:8]}",
        name="Ws",
        settings={},
    )
    db_session.add(ws)
    await db_session.flush()

    run_id = "run_ignore_test"
    emit_file_overlap_warnings(
        db_session,
        workspace_id=ws.id,
        ticket_ref="ELS-156",
        project_id="proj-1",
        run_id=run_id,
        warnings=[
            {
                "overlap_kind": "hard",
                "pr_number": 10,
                "paths": ["src/old.py"],
            }
        ],
    )
    await db_session.flush()

    from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo

    install = GitHubInstallation(
        workspace_id=ws.id,
        installation_id=123457,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=ws.id,
        installation_id=install.id,
        provider="github",
        external_id=2,
        full_name="acme/ship",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/ship",
        description=None,
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()

    async def _list_files(self, ref, *, limit=100):
        return [
            {
                "filename": "src/new.py",
                "previous_filename": "src/old.py",
            }
        ]

    monkeypatch.setattr(GitHubCodeHost, "list_pull_request_files", _list_files)

    action = await evaluate_file_overlap_honour(
        db_session,
        workspace_id=ws.id,
        ticket_ref="ELS-156",
        run_id=run_id,
        fsm_stage="dev_implementation",
        comment="https://github.com/acme/ship/pull/100",
        settings=get_settings(),
    )
    assert action == ACTION_IGNORED


@pytest.mark.asyncio
async def test_evaluate_honour_skipped_when_no_pr_in_comment(
    db_session,
) -> None:
    org = Org(slug=f"t-{uuid.uuid4().hex[:8]}", name="Org", plan="free")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(
        org_id=org.id,
        slug=f"t-{uuid.uuid4().hex[:8]}",
        name="Ws",
        settings={},
    )
    db_session.add(ws)
    await db_session.flush()

    run_id = "run_no_pr"
    emit_file_overlap_warnings(
        db_session,
        workspace_id=ws.id,
        ticket_ref="ELS-156",
        project_id="proj-1",
        run_id=run_id,
        warnings=[
            {
                "overlap_kind": "hard",
                "pr_number": 10,
                "paths": ["src/a.py"],
            }
        ],
    )
    await db_session.flush()

    from backend.app.core.config import get_settings

    action = await evaluate_file_overlap_honour(
        db_session,
        workspace_id=ws.id,
        ticket_ref="ELS-156",
        run_id=run_id,
        fsm_stage="dev_implementation",
        comment="Blocked: tests failed. [Ship SDLC:role-developer]",
        settings=get_settings(),
    )
    assert action is None

    from sqlalchemy import select

    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws.id,
                AuditLog.action.in_((ACTION_HONOURED, ACTION_IGNORED)),
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_evaluate_honour_skipped_when_pr_files_fetch_fails(
    db_session, monkeypatch
) -> None:
    from backend.app.core.config import get_settings
    from backend.app.integrations.github.code_host_adapter import GitHubCodeHost

    org = Org(slug=f"t-{uuid.uuid4().hex[:8]}", name="Org", plan="free")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(
        org_id=org.id,
        slug=f"t-{uuid.uuid4().hex[:8]}",
        name="Ws",
        settings={},
    )
    db_session.add(ws)
    await db_session.flush()

    run_id = "run_fetch_fail"
    emit_file_overlap_warnings(
        db_session,
        workspace_id=ws.id,
        ticket_ref="ELS-156",
        project_id="proj-1",
        run_id=run_id,
        warnings=[
            {
                "overlap_kind": "hard",
                "pr_number": 10,
                "paths": ["src/a.py"],
            }
        ],
    )
    await db_session.flush()

    from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo

    install = GitHubInstallation(
        workspace_id=ws.id,
        installation_id=123458,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=ws.id,
        installation_id=install.id,
        provider="github",
        external_id=3,
        full_name="acme/ship",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/ship",
        description=None,
        activated_at=datetime.now(timezone.utc),
        preset="web-app",
    )
    db_session.add(repo)
    await db_session.flush()

    async def _list_files_fail(self, ref, *, limit=100):
        raise RuntimeError("github unavailable")

    monkeypatch.setattr(GitHubCodeHost, "list_pull_request_files", _list_files_fail)

    action = await evaluate_file_overlap_honour(
        db_session,
        workspace_id=ws.id,
        ticket_ref="ELS-156",
        run_id=run_id,
        fsm_stage="dev_implementation",
        comment="PR: https://github.com/acme/ship/pull/101",
        settings=get_settings(),
    )
    assert action == ACTION_HONOUR_SKIPPED
