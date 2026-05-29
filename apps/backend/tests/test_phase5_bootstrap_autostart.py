"""Phase 5 of the FSM event-driven rearchitecture — after the seed PR
merges, automatically run bootstrap-intelligence + dispatch the first
infra ticket so the cascade walks setup autonomously to merge.

These tests pin the helper's decision tree against fakes:

1. No tracker bound — silent return (no audit, no dispatch).
2. Bootstrap probe says ``skipped_no_blueprint`` → audit
   ``wizard.bootstrap_skipped`` + no dispatch.
3. Same for ``skipped_already_ready`` and ``skipped_no_gaps`` (one
   parametrised test).
4. ``bootstrap_generated`` with tickets → epic-generated audit + first
   ticket dispatched via ``maybe_dispatch(trigger_kind="wizard_bootstrap")``
   + first-dispatch audit row.
5. ``bootstrap_generated`` with empty tickets (idempotent reuse path)
   → epic-generated audit only; NO dispatch (no first_ticket_ref).
6. ``run_bootstrap_for_repo`` raises → swallow + log + no audit/dispatch.
7. ``maybe_dispatch`` raises → epic-generated audit fired earlier, but
   first-dispatch audit NOT written (we trust the log + audit history
   to surface the gap).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.api.v1.routes.github_app import (
    _autostart_bootstrap_on_install_merge,
)


class _FakeRepo:
    """``WorkspaceRepo``-shaped enough for the helper. The helper only
    reads ``id`` (audit telemetry); ``full_name`` is touched by the
    fakes only when ``run_bootstrap_for_repo`` is called for real,
    which we patch out in every test."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()
        self.full_name = "acme/widget"
        self.workspace_id = uuid.uuid4()


class _RecordingSession:
    """Captures every ``add()`` so tests can read back audit rows."""

    def __init__(self) -> None:
        self.added: list[Any] = []

    def add(self, row: Any) -> None:
        self.added.append(row)


def _resolved_or_none(kind: str | None = "linear"):
    if kind is None:
        return None
    resolved = MagicMock()
    resolved.kind = kind
    resolved.gateway = MagicMock()
    return resolved


def _patches(*, resolved, bootstrap_result, dispatch_fired=True,
             dispatch_reason="fired", dispatch_raises=False):
    """Stack of patches that mock the helper's external surface."""
    resolve_patch = patch(
        "backend.app.services.tracker_resolver.resolve_for_workspace",
        new=AsyncMock(return_value=resolved),
    )
    if isinstance(bootstrap_result, Exception):
        bootstrap_mock = AsyncMock(side_effect=bootstrap_result)
    else:
        bootstrap_mock = AsyncMock(return_value=bootstrap_result)
    bootstrap_patch = patch(
        "backend.app.services.bootstrap_plan.run_bootstrap_for_repo",
        new=bootstrap_mock,
    )
    if dispatch_raises:
        dispatch_mock = AsyncMock(side_effect=RuntimeError("dispatch crashed"))
    else:
        dispatch_result = MagicMock()
        dispatch_result.fired = dispatch_fired
        dispatch_result.reason = dispatch_reason
        dispatch_mock = AsyncMock(return_value=dispatch_result)
    dispatch_patch = patch(
        "backend.app.services.dispatcher.maybe_dispatch",
        new=dispatch_mock,
    )
    return resolve_patch, bootstrap_patch, dispatch_patch, dispatch_mock


@pytest.mark.asyncio
async def test_silent_noop_when_no_tracker_bound():
    session = _RecordingSession()
    repo = _FakeRepo()
    rp, bp, dp, dm = _patches(
        resolved=None, bootstrap_result={"result": "ignored"},
    )
    with rp, bp, dp:
        await _autostart_bootstrap_on_install_merge(
            session, workspace_id=repo.workspace_id, repo_row=repo,
        )
    # The workspace hasn't wired its tracker yet — Phase 5 has no place
    # to anchor the epic. The wizard sets the tracker, this code path
    # only runs after install PR merge, so this is a defensive guard.
    assert session.added == []
    dm.assert_not_called()


@pytest.mark.parametrize(
    "skip_reason",
    ("skipped_no_blueprint", "skipped_already_ready", "skipped_no_gaps"),
)
@pytest.mark.asyncio
async def test_bootstrap_skip_paths_audit_and_no_dispatch(skip_reason):
    session = _RecordingSession()
    repo = _FakeRepo()
    rp, bp, dp, dm = _patches(
        resolved=_resolved_or_none(),
        bootstrap_result={"result": skip_reason, "detail": "no scaffolding to do"},
    )
    with rp, bp, dp:
        await _autostart_bootstrap_on_install_merge(
            session, workspace_id=repo.workspace_id, repo_row=repo,
        )
    actions = [getattr(r, "action", None) for r in session.added]
    assert actions == ["wizard.bootstrap_skipped"]
    row = session.added[0]
    assert row.payload["reason"] == skip_reason
    assert row.payload["detail"] == "no scaffolding to do"
    dm.assert_not_called()


@pytest.mark.asyncio
async def test_bootstrap_generated_with_first_ticket_dispatches():
    session = _RecordingSession()
    repo = _FakeRepo()
    rp, bp, dp, dm = _patches(
        resolved=_resolved_or_none(),
        bootstrap_result={
            "result": "bootstrap_generated",
            "project_url": "https://linear.app/acme/project/bootstrap",
            "project_native_id": "proj-9",
            "ticket_count": 4,
            "first_ticket_ref": "ACME-101",
        },
    )
    with rp, bp, dp:
        await _autostart_bootstrap_on_install_merge(
            session, workspace_id=repo.workspace_id, repo_row=repo,
        )
    actions = [getattr(r, "action", None) for r in session.added]
    assert actions == [
        "wizard.bootstrap_epic_generated",
        "wizard.bootstrap_first_dispatch",
    ]
    epic = session.added[0]
    assert epic.payload["project_url"] == "https://linear.app/acme/project/bootstrap"
    assert epic.payload["ticket_count"] == 4
    assert epic.payload["first_ticket_ref"] == "ACME-101"
    dispatch = session.added[1]
    assert dispatch.target_id == "ACME-101"
    assert dispatch.payload["fired"] is True
    assert dispatch.payload["reason"] == "fired"
    # Direct check: maybe_dispatch called with our trigger_kind +
    # fsm_stage so future audit consumers can filter Phase 5 dispatches
    # off the cascade chain.
    call = dm.await_args
    assert call.kwargs["ticket_ref"] == "ACME-101"
    assert call.kwargs["trigger_kind"] == "wizard_bootstrap"
    assert call.kwargs["fsm_stage"] == "planning"


@pytest.mark.asyncio
async def test_bootstrap_generated_idempotent_reuse_skips_dispatch():
    # Re-running bootstrap on a repo that already has an OPEN epic
    # reuses the project (no fresh tickets minted). The helper records
    # the epic-generated audit but does NOT dispatch — there's no
    # ``first_ticket_ref`` to hand to maybe_dispatch, and we don't want
    # to second-guess what the operator is already working on.
    session = _RecordingSession()
    repo = _FakeRepo()
    rp, bp, dp, dm = _patches(
        resolved=_resolved_or_none(),
        bootstrap_result={
            "result": "bootstrap_generated",
            "project_url": "https://linear.app/acme/project/bootstrap",
            "project_native_id": "proj-9",
            "ticket_count": 0,
            "first_ticket_ref": None,
        },
    )
    with rp, bp, dp:
        await _autostart_bootstrap_on_install_merge(
            session, workspace_id=repo.workspace_id, repo_row=repo,
        )
    actions = [getattr(r, "action", None) for r in session.added]
    assert actions == ["wizard.bootstrap_epic_generated"]
    dm.assert_not_called()


@pytest.mark.asyncio
async def test_run_bootstrap_for_repo_raising_is_swallowed():
    # A tracker hiccup mid-bootstrap must not fail the webhook ack.
    # We log it, write nothing to audit_log, and let the operator
    # retry by hand (or wait for the next install PR redelivery —
    # GH retries failed webhooks for 8 hours).
    session = _RecordingSession()
    repo = _FakeRepo()
    rp, bp, dp, dm = _patches(
        resolved=_resolved_or_none(),
        bootstrap_result=RuntimeError("tracker 500"),
    )
    with rp, bp, dp:
        await _autostart_bootstrap_on_install_merge(
            session, workspace_id=repo.workspace_id, repo_row=repo,
        )
    assert session.added == []
    dm.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_dispatch_raising_still_records_epic_audit():
    # The epic exists in Linear by the time dispatch is attempted —
    # ``generate_bootstrap_plan`` already wrote the project + tickets.
    # If dispatch crashes (cap exceeded race, transient lock contention),
    # the epic audit row stays so the operator can see what was minted
    # before the failure. The dispatch row only fires on success — its
    # absence is the diagnostic.
    session = _RecordingSession()
    repo = _FakeRepo()
    rp, bp, dp, dm = _patches(
        resolved=_resolved_or_none(),
        bootstrap_result={
            "result": "bootstrap_generated",
            "project_url": "https://linear.app/acme/project/bootstrap",
            "project_native_id": "proj-9",
            "ticket_count": 4,
            "first_ticket_ref": "ACME-101",
        },
        dispatch_raises=True,
    )
    with rp, bp, dp:
        await _autostart_bootstrap_on_install_merge(
            session, workspace_id=repo.workspace_id, repo_row=repo,
        )
    actions = [getattr(r, "action", None) for r in session.added]
    assert actions == ["wizard.bootstrap_epic_generated"]
    # The dispatch crash is in the logs; no first_dispatch audit row.
