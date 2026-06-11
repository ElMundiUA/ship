"""StateProjector (ELS-229): one-directional, lock-free, flag-gated.

Pins: flag-off path is the original direct gateway.transition call
(byte-identical, including raised exceptions); flag-on funnels through
project_ticket_state; a tracker 5xx yields an errored report and
provably never touches agent_dispatch_locks; re-projection is
idempotent at the seam; the module never imports lock primitives.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from backend.app.db.models.agent_dispatch import AgentDispatchLock
from backend.app.services.state_projector import (
    project_ticket_state,
    transition_via_projector,
)


class _Gateway:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple] = []

    async def transition(self, ref, *, to_state, from_state=None):
        self.calls.append((ref.id, to_state, from_state))
        if self.fail:
            raise RuntimeError("tracker 502")


def _ref():
    return SimpleNamespace(id="ELS-42", kind="linear")


def _settings(on: bool):
    return SimpleNamespace(state_projector_unified=on)


@pytest.mark.asyncio
async def test_flag_off_calls_gateway_directly_and_raises(db_session) -> None:
    gw = _Gateway(fail=True)
    with pytest.raises(RuntimeError, match="tracker 502"):
        await transition_via_projector(
            db_session,
            settings=_settings(False),
            workspace_id=uuid.uuid4(),
            gateway=gw,
            ref=_ref(),
            to_state="In Progress",
        )
    assert gw.calls == [("ELS-42", "In Progress", None)]


@pytest.mark.asyncio
async def test_flag_on_same_error_semantics(db_session) -> None:
    """Routes keep their behavior: failures re-raise either way."""
    gw = _Gateway(fail=True)
    with pytest.raises(RuntimeError, match="tracker 502"):
        await transition_via_projector(
            db_session,
            settings=_settings(True),
            workspace_id=uuid.uuid4(),
            gateway=gw,
            ref=_ref(),
            to_state="In Progress",
            from_state="planning",
        )
    assert gw.calls == [("ELS-42", "In Progress", "planning")]


@pytest.mark.asyncio
async def test_tracker_5xx_errored_report_zero_lock_mutation(
    db_session, seed_workspace
) -> None:
    _, _, ws = seed_workspace
    def _count():
        return db_session.scalar(
            select(func.count(AgentDispatchLock.id)).where(
                AgentDispatchLock.workspace_id == ws.id
            )
        )
    before = await _count()
    report = await project_ticket_state(
        db_session,
        workspace_id=ws.id,
        gateway=_Gateway(fail=True),
        ref=_ref(),
        to_state="Review",
    )
    assert report.ok is False
    assert isinstance(report.error, RuntimeError)
    await db_session.flush()
    after = await _count()
    assert after == before == 0


@pytest.mark.asyncio
async def test_reprojection_is_idempotent_at_the_seam(db_session) -> None:
    gw = _Gateway()
    for _ in range(2):
        report = await project_ticket_state(
            db_session,
            workspace_id=uuid.uuid4(),
            gateway=gw,
            ref=_ref(),
            to_state="Review",
        )
        assert report.ok
    # Same call twice — same args both times; no state accumulates in
    # the projector (the adapter's same-state write is a no-op
    # server-side; label adds are by-name deduped in the adapter).
    assert gw.calls == [("ELS-42", "Review", None)] * 2


def test_projector_never_imports_lock_primitives() -> None:
    src = (
        Path(__file__).resolve().parents[1]
        / "app" / "services" / "state_projector.py"
    ).read_text()
    tree = ast.parse(src)
    forbidden = {
        "acquire_lock", "release_lock", "count_active_locks",
        "sweep_expired_locks", "_count_recent_dispatches", "maybe_dispatch",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name not in forbidden
        if isinstance(node, ast.Call):
            name = (
                node.func.id if isinstance(node.func, ast.Name)
                else node.func.attr if isinstance(node.func, ast.Attribute)
                else None
            )
            assert name not in forbidden, f"projector calls {name}()"


def test_no_direct_gateway_transitions_left_in_routes() -> None:
    """Every FSM→tracker write in agent_runs.py + project_state_sync
    goes through the projector shim."""
    base = Path(__file__).resolve().parents[1] / "app"
    for rel in (
        "api/v1/routes/agent_runs.py",
        "services/agent/project_state_sync.py",
    ):
        src = (base / rel).read_text()
        assert "gateway.transition(" not in src.replace(
            "transition_via_projector", ""
        ), f"{rel} still calls gateway.transition directly"
