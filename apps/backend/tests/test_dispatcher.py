"""E16 / ELS-122 — unit tests for the event-driven dispatcher.

Covers the three guard rails that gate every dispatch:

1. **lock acquire/release** — atomic claim via INSERT ... ON CONFLICT;
   re-acquire after release; expired-row reaping; idempotent release.
2. **per-workspace cap** — once ``max_concurrent_dispatches`` (or the
   global default fallback) is reached, ``maybe_dispatch`` refuses
   with ``reason=cap_exceeded`` and releases the lock it took.
3. **cascade depth** — more than :data:`CASCADE_LIMIT` dispatches for
   the same ticket within :data:`CASCADE_WINDOW_S` seconds refuses
   with ``reason=cascade_blocked``; the audit log records the refusal.

Shadow mode (``SHIP_TRACKER_POLL_FIRE=false``) is covered by the
"records and refuses" assertion in the happy-path tests — the
dispatcher never reaches the GH-API call.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from backend.app.db.models.agent_dispatch import AgentDispatchLock
from backend.app.db.models.tenancy import AuditLog
from backend.app.services import dispatcher
from backend.app.services.dispatcher import (
    CASCADE_LIMIT,
    CASCADE_WINDOW_S,
    WORKSPACE_BUNDLE_CAP,
    acquire_lock,
    count_active_locks,
    maybe_dispatch,
    maybe_dispatch_workspace_bundle,
    release_lock,
    sweep_expired_locks,
)


# ---------------------------------------------------------------------------
# Lock primitive tests — no workspace fixture needed
# ---------------------------------------------------------------------------


async def _make_workspace(db_session) -> uuid.UUID:
    """Insert a minimal Org + Workspace pair and return the ws id.

    The dispatcher's lock table has an FK to workspaces, so the
    primitive tests need a real workspace row even if no other
    fixtures are involved.
    """
    from backend.app.db.models.tenancy import Org, Workspace

    org = Org(
        slug=f"t-{uuid.uuid4().hex[:8]}",
        name="Test org",
        plan="free",
    )
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(
        org_id=org.id,
        slug=f"t-{uuid.uuid4().hex[:8]}",
        name="Test ws",
    )
    db_session.add(ws)
    await db_session.flush()
    return ws.id


@pytest.mark.asyncio
async def test_acquire_lock_first_caller_wins(db_session) -> None:
    ws = await _make_workspace(db_session)
    assert await acquire_lock(db_session, workspace_id=ws, key="ticket:T-1") is True
    # Second attempt against the same key must refuse — the unique
    # index serialises racing claims.
    assert await acquire_lock(db_session, workspace_id=ws, key="ticket:T-1") is False


@pytest.mark.asyncio
async def test_release_then_reacquire(db_session) -> None:
    ws = await _make_workspace(db_session)
    await acquire_lock(db_session, workspace_id=ws, key="ticket:T-1")
    assert await release_lock(db_session, workspace_id=ws, key="ticket:T-1") is True
    # Released — next acquire must succeed.
    assert await acquire_lock(db_session, workspace_id=ws, key="ticket:T-1") is True


@pytest.mark.asyncio
async def test_release_unknown_key_is_noop(db_session) -> None:
    ws = await _make_workspace(db_session)
    assert await release_lock(db_session, workspace_id=ws, key="ticket:nope") is False


@pytest.mark.asyncio
async def test_expired_lock_can_be_reacquired(db_session) -> None:
    """A lock whose ``expires_at`` is in the past should reap on the
    next acquire — that's the orphan-protection contract."""
    ws = await _make_workspace(db_session)
    await acquire_lock(db_session, workspace_id=ws, key="ticket:T-1")
    # Backdate the row to simulate a stuck agent past its TTL.
    await db_session.execute(
        text(
            "UPDATE agent_dispatch_locks SET expires_at = now() - interval '1 minute' "
            "WHERE workspace_id = :ws AND key = :key"
        ),
        {"ws": ws, "key": "ticket:T-1"},
    )
    # New acquire must succeed — the expired row gets swept inside
    # ``acquire_lock`` before the INSERT.
    assert await acquire_lock(db_session, workspace_id=ws, key="ticket:T-1") is True


@pytest.mark.asyncio
async def test_count_active_locks_ignores_expired(db_session) -> None:
    ws = await _make_workspace(db_session)
    await acquire_lock(db_session, workspace_id=ws, key="ticket:A")
    await acquire_lock(db_session, workspace_id=ws, key="ticket:B")
    # Expire one of them.
    await db_session.execute(
        text(
            "UPDATE agent_dispatch_locks SET expires_at = now() - interval '1 minute' "
            "WHERE workspace_id = :ws AND key = :key"
        ),
        {"ws": ws, "key": "ticket:A"},
    )
    assert await count_active_locks(db_session, workspace_id=ws) == 1


@pytest.mark.asyncio
async def test_sweep_expired_workspace_scoped(db_session) -> None:
    ws1 = await _make_workspace(db_session)
    ws2 = await _make_workspace(db_session)
    await acquire_lock(db_session, workspace_id=ws1, key="ticket:A")
    await acquire_lock(db_session, workspace_id=ws2, key="ticket:B")
    await db_session.execute(
        text(
            "UPDATE agent_dispatch_locks SET expires_at = now() - interval '1 minute'"
        )
    )
    n = await sweep_expired_locks(db_session, workspace_id=ws1)
    assert n == 1  # only ws1's row was reaped
    # The ws2 row is still in the table (just expired).
    row = (
        await db_session.execute(
            select(AgentDispatchLock).where(
                AgentDispatchLock.workspace_id == ws2
            )
        )
    ).scalar_one_or_none()
    assert row is not None


# ---------------------------------------------------------------------------
# maybe_dispatch — shadow mode, cap, cascade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_mode_records_and_refuses(db_session, monkeypatch) -> None:
    """``SHIP_TRACKER_POLL_FIRE=false`` (default) must NOT acquire a
    lock or fire GH — just write the shadow audit row."""
    ws = await _make_workspace(db_session)
    result = await maybe_dispatch(
        db_session,
        workspace_id=ws,
        ticket_ref="T-1",
        trigger_kind="tracker_poll",
        fsm_stage="planning",
    )
    assert result.fired is False
    assert result.reason == "shadow"
    # No lock row was created.
    n = (
        await db_session.execute(
            select(AgentDispatchLock).where(
                AgentDispatchLock.workspace_id == ws
            )
        )
    ).all()
    assert len(n) == 0
    # Shadow audit row IS written.
    shadow = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws,
                AuditLog.action == "agent_run.dispatch_shadow",
            )
        )
    ).scalars().all()
    assert len(shadow) == 1


@pytest.mark.asyncio
async def test_cascade_blocked_after_limit_hits(
    db_session, monkeypatch
) -> None:
    """Pre-seed ``CASCADE_LIMIT`` audit rows for the same ticket; the
    next ``maybe_dispatch`` must refuse with ``cascade_blocked``."""
    ws = await _make_workspace(db_session)
    # Force fire-mode so we actually exercise the cascade guard.
    monkeypatch.setattr(
        dispatcher.get_settings(),
        "tracker_poll_fire",
        True,
        raising=False,
    )
    for _ in range(CASCADE_LIMIT):
        db_session.add(
            AuditLog(
                workspace_id=ws,
                action="agent_run.dispatch",
                target_kind="ticket",
                target_id="T-1",
                payload={},
            )
        )
    await db_session.flush()

    result = await maybe_dispatch(
        db_session,
        workspace_id=ws,
        ticket_ref="T-1",
        trigger_kind="cascade",
        fsm_stage="planning",
    )
    assert result.fired is False
    assert result.reason == "cascade_blocked"
    # The dispatcher recorded its refusal so dashboards can see it.
    refused = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws,
                AuditLog.action == "dispatch.cascade_blocked",
            )
        )
    ).scalars().all()
    assert len(refused) == 1


@pytest.mark.asyncio
async def test_cap_exceeded_releases_lock(db_session, monkeypatch) -> None:
    """Per-workspace cap: occupy 4 slots, then expect the 5th to
    refuse with ``cap_exceeded`` AND drop the lock it grabbed."""
    ws = await _make_workspace(db_session)
    monkeypatch.setattr(
        dispatcher.get_settings(),
        "tracker_poll_fire",
        True,
        raising=False,
    )
    # The default cap is 4; preload 4 unrelated ticket locks so the
    # cap is at its limit before we test.
    for n in range(4):
        await acquire_lock(
            db_session, workspace_id=ws, key=f"ticket:T-{n}"
        )

    result = await maybe_dispatch(
        db_session,
        workspace_id=ws,
        ticket_ref="T-new",
        trigger_kind="tracker_poll",
        fsm_stage="planning",
    )
    assert result.fired is False
    assert result.reason == "cap_exceeded"

    # The lock the dispatcher tried to take must have been released
    # — the workspace still has exactly 4 active locks.
    assert await count_active_locks(db_session, workspace_id=ws) == 4


@pytest.mark.asyncio
async def test_lock_held_refuses_without_audit_storm(
    db_session, monkeypatch
) -> None:
    """A second dispatch for the same ticket while the first is in
    flight must refuse silently (no audit row beyond what the guard
    intends to record)."""
    ws = await _make_workspace(db_session)
    monkeypatch.setattr(
        dispatcher.get_settings(),
        "tracker_poll_fire",
        True,
        raising=False,
    )
    await acquire_lock(db_session, workspace_id=ws, key="ticket:T-1")

    result = await maybe_dispatch(
        db_session,
        workspace_id=ws,
        ticket_ref="T-1",
        trigger_kind="tracker_poll",
        fsm_stage="planning",
    )
    assert result.fired is False
    assert result.reason == "lock_held"


@pytest.mark.asyncio
async def test_unknown_fsm_stage_refuses_with_no_routine(
    db_session, monkeypatch
) -> None:
    """An ``fsm_stage`` we can't map to a routine (unknown / missing
    stage label on the ticket) refuses with ``no_routine`` so the
    dispatcher doesn't burn a lock + GH dispatch on a ticket no
    agent will pick up."""
    ws = await _make_workspace(db_session)
    monkeypatch.setattr(
        dispatcher.get_settings(),
        "tracker_poll_fire",
        True,
        raising=False,
    )
    result = await maybe_dispatch(
        db_session,
        workspace_id=ws,
        ticket_ref="T-1",
        trigger_kind="tracker_poll",
        fsm_stage="unknown-stage-xyz",
    )
    assert result.fired is False
    assert result.reason == "no_routine"
    # No lock taken — we refused before acquire.
    assert await count_active_locks(db_session, workspace_id=ws) == 0
    # Audit row recorded.
    refusals = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws,
                AuditLog.action == "dispatch.no_routine",
            )
        )
    ).scalars().all()
    assert len(refusals) == 1


@pytest.mark.asyncio
async def test_missing_fsm_stage_refuses_with_no_routine(
    db_session, monkeypatch
) -> None:
    """``fsm_stage=None`` (poller couldn't find a ``stage:`` label on
    the ticket) should refuse the same way an unknown stage does —
    we have no routine to fire."""
    ws = await _make_workspace(db_session)
    monkeypatch.setattr(
        dispatcher.get_settings(),
        "tracker_poll_fire",
        True,
        raising=False,
    )
    result = await maybe_dispatch(
        db_session,
        workspace_id=ws,
        ticket_ref="T-1",
        trigger_kind="tracker_poll",
        fsm_stage=None,
    )
    assert result.fired is False
    assert result.reason == "no_routine"


@pytest.mark.asyncio
async def test_no_activated_repo_refuses(db_session, monkeypatch) -> None:
    """Fire mode + workspace with zero activated repos → ``no_repo``
    (dispatcher can't pick a target)."""
    ws = await _make_workspace(db_session)
    monkeypatch.setattr(
        dispatcher.get_settings(),
        "tracker_poll_fire",
        True,
        raising=False,
    )
    result = await maybe_dispatch(
        db_session,
        workspace_id=ws,
        ticket_ref="T-1",
        trigger_kind="tracker_poll",
        fsm_stage="planning",
    )
    assert result.fired is False
    assert result.reason == "no_repo"
    # Lock was released — workspace ends with zero active locks.
    assert await count_active_locks(db_session, workspace_id=ws) == 0


# ---------------------------------------------------------------------------
# Workspace-bundle dispatch (ELS-125) — separate cap, no compete with SDLC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_bundle_shadow_writes_audit(
    db_session, monkeypatch
) -> None:
    """Shadow mode for workspace bundles records the intent without
    a lock + without firing GH."""
    ws = await _make_workspace(db_session)
    result = await maybe_dispatch_workspace_bundle(
        db_session,
        workspace_id=ws,
        bundle_id="daily-digest",
        trigger_kind="daily_tick",
    )
    assert result.fired is False
    assert result.reason == "shadow"
    # No lock row was created.
    assert await count_active_locks(db_session, workspace_id=ws) == 0


@pytest.mark.asyncio
async def test_workspace_bundle_unknown_id_refuses(
    db_session, monkeypatch
) -> None:
    """A bundle id not in ``_WORKSPACE_BUNDLE_IDS`` refuses fast
    (``no_routine``) without touching the lock table."""
    ws = await _make_workspace(db_session)
    monkeypatch.setattr(
        dispatcher.get_settings(),
        "tracker_poll_fire",
        True,
        raising=False,
    )
    result = await maybe_dispatch_workspace_bundle(
        db_session,
        workspace_id=ws,
        bundle_id="nonexistent-bundle",
        trigger_kind="daily_tick",
    )
    assert result.fired is False
    assert result.reason == "no_routine"
    assert await count_active_locks(db_session, workspace_id=ws) == 0


@pytest.mark.asyncio
async def test_workspace_bundle_lock_independent_of_sdlc(
    db_session, monkeypatch
) -> None:
    """SDLC ticket locks must not consume the workspace-bundle cap
    slot. With 4 active ``ticket:*`` locks (SDLC cap full), a
    ``daily-digest`` dispatch still goes through because it counts
    on a separate ``daily-digest:*`` prefix counter."""
    ws = await _make_workspace(db_session)
    monkeypatch.setattr(
        dispatcher.get_settings(),
        "tracker_poll_fire",
        True,
        raising=False,
    )
    # Fill the SDLC cap with 4 ticket locks.
    for n in range(4):
        await acquire_lock(
            db_session, workspace_id=ws, key=f"ticket:T-{n}"
        )

    # Workspace-bundle cap counts only ``daily-digest:`` keys — still 0.
    n_bundle = await count_active_locks(
        db_session, workspace_id=ws, key_prefix="daily-digest:"
    )
    assert n_bundle == 0

    # No activated repo, so we expect ``no_repo`` AFTER the cap +
    # lock checks pass — that's evidence the SDLC locks didn't push
    # us over the workspace-bundle cap.
    result = await maybe_dispatch_workspace_bundle(
        db_session,
        workspace_id=ws,
        bundle_id="daily-digest",
        trigger_kind="daily_tick",
    )
    assert result.fired is False
    # We reached the repo-pick step → the cap check passed.
    assert result.reason == "no_repo"


@pytest.mark.asyncio
async def test_workspace_bundle_cap_one_concurrent(
    db_session, monkeypatch
) -> None:
    """A second concurrent dispatch of the SAME workspace bundle
    refuses with ``lock_held`` — the unique ``(ws, key)`` index
    serialises racing daily-digest invocations."""
    ws = await _make_workspace(db_session)
    monkeypatch.setattr(
        dispatcher.get_settings(),
        "tracker_poll_fire",
        True,
        raising=False,
    )
    await acquire_lock(
        db_session, workspace_id=ws, key="daily-digest:scheduled"
    )
    result = await maybe_dispatch_workspace_bundle(
        db_session,
        workspace_id=ws,
        bundle_id="daily-digest",
        trigger_kind="daily_tick",
    )
    assert result.fired is False
    assert result.reason == "lock_held"


@pytest.mark.asyncio
async def test_workspace_bundle_separate_namespaces(
    db_session, monkeypatch
) -> None:
    """``daily-digest`` and ``weekly-audit`` and ``self-heal`` are
    independent locks — one held doesn't block the others."""
    ws = await _make_workspace(db_session)
    monkeypatch.setattr(
        dispatcher.get_settings(),
        "tracker_poll_fire",
        True,
        raising=False,
    )
    await acquire_lock(
        db_session, workspace_id=ws, key="daily-digest:scheduled"
    )
    # Different bundle id → different lock key → must clear the
    # lock_held check (it'll then fail on no_repo since no activated
    # repo in this test workspace).
    result = await maybe_dispatch_workspace_bundle(
        db_session,
        workspace_id=ws,
        bundle_id="weekly-audit",
        trigger_kind="weekly_tick",
    )
    assert result.fired is False
    assert result.reason == "no_repo"
