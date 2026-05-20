"""Tests for file-overlap telemetry (ELS-156 / A5.3)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from backend.app.db.models.tenancy import AuditLog
from backend.app.services.file_overlap_telemetry import (
    ACTION_HONOURED,
    ACTION_HONOUR_SKIPPED,
    ACTION_IGNORED,
    ACTION_WARNING,
    emit_overlap_warnings,
    evaluate_honour,
    normalize_repo_path,
    paths_from_pr_files,
    record_honour_on_dev_finish,
    weekly_file_overlap_metrics,
)


def test_normalize_repo_path_strips_dot_slash() -> None:
    assert normalize_repo_path("./src/a.py") == "src/a.py"
    assert normalize_repo_path("\\apps\\x.py") == "apps/x.py"


def test_paths_from_pr_files_includes_previous_filename() -> None:
    files = [
        {"filename": "src/new.py", "previous_filename": "src/old.py"},
        {"filename": "README.md"},
    ]
    assert paths_from_pr_files(files) == {"src/new.py", "src/old.py", "README.md"}


def test_evaluate_honour_ignored_on_intersection() -> None:
    outcome, touched = evaluate_honour(
        ["src/a.py", "src/b.py"],
        {"src/b.py", "other.py"},
    )
    assert outcome == "ignored"
    assert touched == ["src/b.py"]


def test_evaluate_honour_honoured_when_disjoint() -> None:
    outcome, touched = evaluate_honour(["src/a.py"], {"src/b.py"})
    assert outcome == "honoured"
    assert touched == []


@pytest.mark.asyncio
async def test_emit_overlap_warnings_skips_empty_paths(
    db_session, seed_workspace
) -> None:
    ws = seed_workspace[2].id
    emit_overlap_warnings(
        db_session,
        workspace_id=ws,
        ticket_ref="ELS-1",
        project_id="proj",
        run_id=None,
        structured_warnings=[{"overlap_kind": "schema", "paths": []}],
    )
    await db_session.flush()
    rows = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.action == ACTION_WARNING)
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_emit_overlap_warnings_one_row_per_sibling(
    db_session, seed_workspace
) -> None:
    ws = seed_workspace[2].id
    emit_overlap_warnings(
        db_session,
        workspace_id=ws,
        ticket_ref="ELS-147",
        project_id="proj-1",
        run_id=None,
        structured_warnings=[
            {
                "sibling_ticket_ref": "ELS-144",
                "pr_number": 276,
                "overlap_kind": "schema",
                "paths": ["apps/backend/migrations/versions/0074_x.py"],
            }
        ],
    )
    await db_session.flush()
    row = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws,
                AuditLog.action == ACTION_WARNING,
            )
        )
    ).scalar_one()
    assert row.target_id == "ELS-147"
    assert row.payload["sibling_pr_number"] == 276
    assert row.payload["overlap_kind"] == "schema"
    assert "0074_x.py" in row.payload["conflicted_paths"][0]


@pytest.mark.asyncio
async def test_record_honour_honoured_when_pr_avoids_warned_paths(
    db_session, seed_workspace, monkeypatch
) -> None:
    ws = seed_workspace[2].id
    ticket = "ELS-156"
    run_id = "run_abc123"
    now = datetime.now(timezone.utc)
    db_session.add(
        AuditLog(
            workspace_id=ws,
            action="agent_run.dispatch",
            target_kind="ticket",
            target_id=ticket,
            created_at=now - timedelta(minutes=5),
            payload={},
        )
    )
    db_session.add(
        AuditLog(
            workspace_id=ws,
            action=ACTION_WARNING,
            target_kind="ticket",
            target_id=ticket,
            created_at=now - timedelta(minutes=4),
            payload={
                "conflicted_paths": ["src/warned.py"],
                "overlap_kind": "hard",
                "sibling_pr_number": 10,
                "run_id": None,
            },
        )
    )
    await db_session.flush()

    async def _fake_resolve(*_a, **_k):
        repo = MagicMock()
        repo.full_name = "acme/ship"
        install = MagicMock()
        install.installation_id = 1
        return repo, install

    monkeypatch.setattr(
        "backend.app.services.file_overlap_telemetry._resolve_pr_repo",
        _fake_resolve,
    )

    class _Host:
        async def list_pull_request_files(self, *_a, **_k):
            return [{"filename": "src/other.py"}]

    monkeypatch.setattr(
        "backend.app.services.file_overlap_telemetry.GitHubCodeHost",
        lambda **_k: _Host(),
    )

    await record_honour_on_dev_finish(
        db_session,
        workspace_id=ws,
        ticket_ref=ticket,
        run_id=run_id,
        fsm_stage="dev_implementation",
        comment="Done. PR: https://github.com/acme/ship/pull/99\n[Ship SDLC:role-developer]",
    )
    await db_session.flush()

    honoured = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws,
                AuditLog.action == ACTION_HONOURED,
            )
        )
    ).scalar_one()
    assert honoured.payload["run_id"] == run_id
    assert honoured.payload["pr_number"] == 99


@pytest.mark.asyncio
async def test_record_honour_ignored_on_rename_touch(
    db_session, seed_workspace, monkeypatch
) -> None:
    ws = seed_workspace[2].id
    ticket = "ELS-156"
    run_id = "run_rename"
    now = datetime.now(timezone.utc)
    db_session.add(
        AuditLog(
            workspace_id=ws,
            action="agent_run.dispatch",
            target_kind="ticket",
            target_id=ticket,
            created_at=now - timedelta(minutes=1),
            payload={},
        )
    )
    db_session.add(
        AuditLog(
            workspace_id=ws,
            action=ACTION_WARNING,
            target_kind="ticket",
            target_id=ticket,
            payload={
                "conflicted_paths": ["src/old.py"],
                "overlap_kind": "hard",
                "run_id": None,
            },
        )
    )
    await db_session.flush()

    async def _fake_resolve(*_a, **_k):
        repo = MagicMock()
        repo.full_name = "acme/ship"
        install = MagicMock()
        install.installation_id = 1
        return repo, install

    monkeypatch.setattr(
        "backend.app.services.file_overlap_telemetry._resolve_pr_repo",
        _fake_resolve,
    )

    class _Host:
        async def list_pull_request_files(self, *_a, **_k):
            return [
                {
                    "filename": "src/new.py",
                    "previous_filename": "src/old.py",
                }
            ]

    monkeypatch.setattr(
        "backend.app.services.file_overlap_telemetry.GitHubCodeHost",
        lambda **_k: _Host(),
    )

    await record_honour_on_dev_finish(
        db_session,
        workspace_id=ws,
        ticket_ref=ticket,
        run_id=run_id,
        fsm_stage="dev_implementation",
        comment="PR: https://github.com/acme/ship/pull/42",
    )
    await db_session.flush()

    ignored = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.action == ACTION_IGNORED,
                AuditLog.workspace_id == ws,
            )
        )
    ).scalar_one()
    assert ignored.payload["touched_paths"] == ["src/old.py"]


@pytest.mark.asyncio
async def test_record_honour_idempotent(db_session, seed_workspace) -> None:
    ws = seed_workspace[2].id
    ticket = "ELS-9"
    run_id = "run_dup"
    db_session.add(
        AuditLog(
            workspace_id=ws,
            action=ACTION_HONOURED,
            target_kind="ticket",
            target_id=ticket,
            payload={"run_id": run_id},
        )
    )
    await db_session.flush()

    await record_honour_on_dev_finish(
        db_session,
        workspace_id=ws,
        ticket_ref=ticket,
        run_id=run_id,
        fsm_stage="dev_implementation",
        comment="PR: https://github.com/acme/ship/pull/1",
    )
    await db_session.flush()
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws,
                AuditLog.action == ACTION_HONOURED,
            )
        )
    ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_record_honour_skipped_without_repo(
    db_session, seed_workspace, monkeypatch
) -> None:
    ws = seed_workspace[2].id
    ticket = "ELS-8"
    now = datetime.now(timezone.utc)
    db_session.add(
        AuditLog(
            workspace_id=ws,
            action="agent_run.dispatch",
            target_kind="ticket",
            target_id=ticket,
            created_at=now,
            payload={},
        )
    )
    db_session.add(
        AuditLog(
            workspace_id=ws,
            action=ACTION_WARNING,
            target_kind="ticket",
            target_id=ticket,
            created_at=now + timedelta(seconds=1),
            payload={"conflicted_paths": ["x.py"], "run_id": None},
        )
    )
    await db_session.flush()

    monkeypatch.setattr(
        "backend.app.services.file_overlap_telemetry._resolve_pr_repo",
        AsyncMock(return_value=None),
    )

    await record_honour_on_dev_finish(
        db_session,
        workspace_id=ws,
        ticket_ref=ticket,
        run_id="run_skip",
        fsm_stage="dev_implementation",
        comment="PR: https://github.com/acme/ship/pull/5",
    )
    await db_session.flush()
    skipped = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws,
                AuditLog.action == ACTION_HONOUR_SKIPPED,
                AuditLog.target_id == ticket,
            )
        )
    ).scalar_one()
    assert skipped.payload["reason"] == "no_code_host"


@pytest.mark.asyncio
async def test_weekly_metrics_honour_rate(db_session, seed_workspace) -> None:
    ws = seed_workspace[2].id
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    for action in (ACTION_WARNING, ACTION_HONOURED, ACTION_IGNORED):
        db_session.add(
            AuditLog(
                workspace_id=ws,
                action=action,
                target_kind="ticket",
                target_id="ELS-1",
                created_at=since,
                payload={},
            )
        )
    await db_session.flush()
    metrics = await weekly_file_overlap_metrics(db_session, workspace_id=ws, days=7)
    assert metrics["warnings_fired"] == 1
    assert metrics["honoured"] == 1
    assert metrics["ignored"] == 1
    assert metrics["honour_rate"] == 0.5


@pytest.mark.asyncio
async def test_weekly_metrics_honour_rate_null_when_no_outcomes(
    db_session, seed_workspace
) -> None:
    ws = seed_workspace[2].id
    metrics = await weekly_file_overlap_metrics(db_session, workspace_id=ws, days=7)
    assert metrics["honour_rate"] is None
