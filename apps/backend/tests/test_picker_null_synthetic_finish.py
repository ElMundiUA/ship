"""Bug-A fix — picker null exits write a synthetic agent_run.finish.

When the dispatcher pinned a ticket (workflow_dispatch with
ticket_ref) and the picker then returns ticket=None — orphan,
overlay-frozen, priority-skipped, no eligible row, refire-capped — we
must record a terminal finish so fsm_self_heal can count it and stop
re-firing the same dead pin every tick.

Pre-fix the dispatch fired hourly forever (askslayer/PAC-21 today: 9
days in this loop). Pin the audit shape so a future picker refactor
can't quietly skip the finish write again.

These tests exercise the synthetic-finish helper directly with a
recording session stub — no DB, no Linear.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

from backend.app.api.v1.routes.agent_runs import (
    _write_synthetic_picker_noop_finish,
)


class _RecordingSession:
    """Minimal Session stand-in — captures everything passed to add()."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, row) -> None:  # noqa: ANN001 — duck typing matches Session
        self.added.append(row)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_writes_audit_row_with_expected_shape() -> None:
    session = _RecordingSession()
    ws = uuid.uuid4()
    _run(
        _write_synthetic_picker_noop_finish(
            session,
            workspace_id=ws,
            ticket_ref="PAC-21",
            fsm_stage="auto_merge",
            reason="picker_refire_capped",
            tracker_kind="linear",
        )
    )
    assert len(session.added) == 1
    row = session.added[0]
    assert row.action == "agent_run.finish"
    assert row.target_kind == "agent_run"
    assert row.target_id == f"picker_noop:{ws}:PAC-21:picker_refire_capped"
    payload = row.payload
    assert payload["outcome"] == "noop"
    assert payload["fsm_stage"] == "auto_merge"
    assert payload["ticket_ref"] == "PAC-21"
    assert payload["reason"] == "picker_refire_capped"
    assert payload["synthetic"] is True
    assert payload["source"] == "picker_noop_finish"
    assert payload["tracker_kind"] == "linear"


def test_noop_when_ticket_ref_missing() -> None:
    # Workspace-bundle paths (self-heal / daily-digest) pick without a
    # ticket pin and don't suffer from the leak. The helper must be a
    # no-op there so callers can invoke it unconditionally.
    session = _RecordingSession()
    _run(
        _write_synthetic_picker_noop_finish(
            session,
            workspace_id=uuid.uuid4(),
            ticket_ref="",
            fsm_stage="workspace_self_heal",
            reason="picker_no_rows",
        )
    )
    assert session.added == []


def test_target_id_uniqueness_per_reason() -> None:
    # Same ticket, different skip reason → different target_id so a
    # downstream audit query can tell them apart. Same ticket, same
    # reason → same target_id (idempotent within a tick).
    ws = uuid.uuid4()
    s1, s2, s3 = _RecordingSession(), _RecordingSession(), _RecordingSession()
    _run(_write_synthetic_picker_noop_finish(s1, workspace_id=ws, ticket_ref="X-1", fsm_stage="auto_merge", reason="picker_refire_capped"))
    _run(_write_synthetic_picker_noop_finish(s2, workspace_id=ws, ticket_ref="X-1", fsm_stage="auto_merge", reason="picker_no_rows"))
    _run(_write_synthetic_picker_noop_finish(s3, workspace_id=ws, ticket_ref="X-1", fsm_stage="auto_merge", reason="picker_refire_capped"))
    assert s1.added[0].target_id != s2.added[0].target_id
    assert s1.added[0].target_id == s3.added[0].target_id


def test_distinguishable_from_real_runs_in_audit() -> None:
    # Real agent runs use ``run_<hex>`` style ids; synthetic finishes
    # must NOT collide with that pattern so audit consumers (the
    # dev_not_converging detector + the refire_cap counter) can tell
    # them apart if they need to.
    session = _RecordingSession()
    _run(
        _write_synthetic_picker_noop_finish(
            session,
            workspace_id=uuid.uuid4(),
            ticket_ref="ELS-7",
            fsm_stage="auto_merge",
            reason="picker_overlay_frozen",
        )
    )
    target_id = session.added[0].target_id
    assert target_id.startswith("picker_noop:")
    assert not target_id.startswith("run_")
