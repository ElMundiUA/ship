"""ELS-278 — overlay label removal resumes the pending FSM stage."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select

from backend.app.db.models.integrations import (
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationSyncState,
)
from backend.app.db.models.tenancy import AuditLog, Integration
from backend.app.security.encryption import encrypt
from backend.app.services import tracker_poller


def _issue(
    identifier: str,
    state: str,
    updated_at: str,
    *,
    labels: list[str] | None = None,
) -> dict[str, Any]:
    label_nodes = [{"name": name} for name in (labels or [])]
    return {
        "id": str(uuid.uuid4()),
        "identifier": identifier,
        "title": f"Test ticket {identifier}",
        "state": {"name": state},
        "labels": {"nodes": label_nodes},
        "updatedAt": updated_at,
        "createdAt": updated_at,
        "comments": {"nodes": []},
    }


async def _seed_linear_install(db_session, workspace_id) -> uuid.UUID:
    install = NativeIntegrationInstallation(
        workspace_id=workspace_id,
        provider="linear",
        auth_mode="oauth",
        external_account_id="default",
        external_account_name="Linear workspace",
        capabilities={},
        scopes=[],
        config={},
        status="ready",
    )
    db_session.add(install)
    await db_session.flush()

    db_session.add(
        NativeIntegrationCredential(
            installation_id=install.id,
            kind="access_token",
            secret_ciphertext=encrypt("lin_oauth_test_token_xxx"),
            scopes=[],
        )
    )
    db_session.add(
        Integration(
            workspace_id=workspace_id,
            kind="linear",
            config={"team_id": "test-team-uuid"},
            status="ok",
        )
    )
    await db_session.flush()
    return install.id


@pytest.fixture
def _patch_sessionmaker(db_session, monkeypatch):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _bound_session_factory():
        yield db_session

    class _SM:
        def __call__(self):
            return _bound_session_factory()

    monkeypatch.setattr(tracker_poller, "get_sessionmaker", lambda: _SM())


@pytest.mark.asyncio
async def test_overlay_unfreeze_dispatches_pending_stage(
    db_session, seed_workspace, monkeypatch, _patch_sessionmaker
) -> None:
    """Removing ``blocked`` after an overlay skip re-cascades code_review."""
    _, _, workspace = seed_workspace
    install_id = await _seed_linear_install(db_session, workspace.id)
    skip_at = datetime(2026, 6, 12, 11, 15, tzinfo=timezone.utc)
    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            action="agent_run.overlay_frozen_skipped",
            target_kind="ticket",
            target_id="ELS-99",
            payload={
                "fsm_stage": "code_review",
                "matched_labels": ["blocked"],
            },
            created_at=skip_at,
        )
    )
    db_session.add(
        NativeIntegrationSyncState(
            installation_id=install_id,
            binding_id=None,
            sync_kind="tracker_poll",
            cursor={
                "updated_at": "2026-06-12T11:00:00.000Z",
                "states": {"ELS-99": "In Progress"},
                "overlay_labels": {"ELS-99": ["blocked"]},
            },
            status="ready",
        )
    )
    await db_session.commit()

    dispatch_calls: list[dict[str, Any]] = []

    async def _fake_dispatch(session, **kwargs):
        dispatch_calls.append(kwargs)
        return {"dispatched": True}

    monkeypatch.setattr(
        "backend.app.services.tracker_poller.maybe_dispatch",
        _fake_dispatch,
        raising=False,
    )
    # maybe_dispatch is imported inside _write_transition_event
    monkeypatch.setattr(
        "backend.app.services.dispatcher.maybe_dispatch",
        _fake_dispatch,
    )

    async def _fake_fetch(**_kw):
        return [
            _issue(
                "ELS-99",
                "In Progress",
                "2026-06-12T11:32:00.000Z",
                labels=["stage:planning", "stage:validation"],
            )
        ]

    monkeypatch.setattr(tracker_poller, "_fetch_updated_issues", _fake_fetch)

    summary = await tracker_poller.poll_once()
    assert summary["events"] >= 1
    assert len(dispatch_calls) == 1
    assert dispatch_calls[0]["fsm_stage"] == "code_review"
    assert dispatch_calls[0]["trigger_kind"] == "tracker_poll"

    resume_rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "agent_run.overlay_unfreeze_resumed",
                AuditLog.target_id == "ELS-99",
            )
        )
    ).scalars().all()
    assert len(resume_rows) == 1


@pytest.mark.asyncio
async def test_overlay_unfreeze_is_idempotent_after_dispatch(
    db_session, seed_workspace, monkeypatch, _patch_sessionmaker
) -> None:
    """No second dispatch when code_review already fired after the skip."""
    _, _, workspace = seed_workspace
    install_id = await _seed_linear_install(db_session, workspace.id)
    skip_at = datetime(2026, 6, 12, 11, 15, tzinfo=timezone.utc)
    dispatch_at = datetime(2026, 6, 12, 11, 20, tzinfo=timezone.utc)
    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            action="agent_run.overlay_frozen_skipped",
            target_kind="ticket",
            target_id="ELS-99",
            payload={"fsm_stage": "code_review", "matched_labels": ["blocked"]},
            created_at=skip_at,
        )
    )
    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            action="agent_run.dispatch",
            target_kind="ticket",
            target_id="ELS-99",
            payload={"fsm_stage": "code_review"},
            created_at=dispatch_at,
        )
    )
    db_session.add(
        NativeIntegrationSyncState(
            installation_id=install_id,
            binding_id=None,
            sync_kind="tracker_poll",
            cursor={
                "updated_at": "2026-06-12T11:00:00.000Z",
                "states": {"ELS-99": "In Progress"},
                "overlay_labels": {"ELS-99": ["blocked"]},
            },
            status="ready",
        )
    )
    await db_session.commit()

    dispatch_calls: list[dict[str, Any]] = []

    async def _fake_dispatch(session, **kwargs):
        dispatch_calls.append(kwargs)
        return {"dispatched": True}

    monkeypatch.setattr(
        "backend.app.services.dispatcher.maybe_dispatch",
        _fake_dispatch,
    )

    async def _fake_fetch(**_kw):
        return [
            _issue(
                "ELS-99",
                "In Progress",
                "2026-06-12T11:32:00.000Z",
                labels=["stage:validation"],
            )
        ]

    monkeypatch.setattr(tracker_poller, "_fetch_updated_issues", _fake_fetch)

    summary = await tracker_poller.poll_once()
    assert summary["events"] == 0
    assert dispatch_calls == []


@pytest.mark.asyncio
async def test_freeze_label_still_present_skips_resume(
    db_session, seed_workspace, monkeypatch, _patch_sessionmaker
) -> None:
    _, _, workspace = seed_workspace
    install_id = await _seed_linear_install(db_session, workspace.id)
    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            action="agent_run.overlay_frozen_skipped",
            target_kind="ticket",
            target_id="ELS-99",
            payload={"fsm_stage": "code_review", "matched_labels": ["blocked"]},
            created_at=datetime(2026, 6, 12, 11, 15, tzinfo=timezone.utc),
        )
    )
    db_session.add(
        NativeIntegrationSyncState(
            installation_id=install_id,
            binding_id=None,
            sync_kind="tracker_poll",
            cursor={
                "updated_at": "2026-06-12T11:00:00.000Z",
                "states": {"ELS-99": "In Progress"},
                "overlay_labels": {"ELS-99": ["blocked"]},
            },
            status="ready",
        )
    )
    await db_session.commit()

    dispatch_calls: list[dict[str, Any]] = []

    async def _fake_dispatch(session, **kwargs):
        dispatch_calls.append(kwargs)
        return {"dispatched": True}

    monkeypatch.setattr(
        "backend.app.services.dispatcher.maybe_dispatch",
        _fake_dispatch,
    )

    async def _fake_fetch(**_kw):
        return [
            _issue(
                "ELS-99",
                "In Progress",
                "2026-06-12T11:32:00.000Z",
                labels=["blocked", "stage:validation"],
            )
        ]

    monkeypatch.setattr(tracker_poller, "_fetch_updated_issues", _fake_fetch)

    summary = await tracker_poller.poll_once()
    assert summary["events"] == 0
    assert dispatch_calls == []
