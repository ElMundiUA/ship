"""Tracker poller — E16 event source (ELS-121).

Replaces the cron-driven ``shipctl trigger`` picker with a server-side
loop that diffs Linear's authoritative ticket state every N seconds
and emits internal ``tracker.event.received`` events for the
dispatcher to pick up.

This module is **shadow-mode by default**. With
``SHIP_TRACKER_POLL_FIRE=false`` (the default) the poller writes
audit_log rows but does NOT call the dispatcher; that's the validation
step before we cut over to event-driven dispatch in ELS-122.

Cursor + per-ticket last-seen state live in
``native_integration_sync_states`` keyed by
``(installation_id, sync_kind='tracker_poll')``. The cursor JSONB
holds:

    {
        "updated_at": "2026-05-14T00:00:00Z",
        "states": {"ELS-121": "Backlog", "ELS-122": "In Progress", ...}
    }

On each tick:

1. Fetch issues from Linear with ``updatedAt >= cursor.updated_at`` (a
   small lookback overlap to absorb clock skew).
2. For each issue, compare against ``cursor.states[ticket_ref]``:
   - state changed → write ``audit_log.tracker.event.received`` with
     ``old_state``, ``new_state``, ``updated_at``.
   - state unchanged → silent (we filter on ``updatedAt`` already, so
     ticket-body edits don't flood the dispatcher).
3. Update ``cursor.updated_at = max(updatedAt)`` and rewrite
   ``cursor.states`` with the freshly seen values.

The poller runs single-leader across replicas via the Postgres
advisory lock pattern (:class:`CronLockId.TRACKER_POLL`). A killed
replica releases the lock on disconnect — no orphaned cursors.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.integrations import (
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationSyncState,
)
from backend.app.db.models.tenancy import AuditLog, Integration
from backend.app.db.session import get_sessionmaker
from backend.app.integrations.linear.tracker_adapter import LinearTracker
from backend.app.security.encryption import safe_decrypt
from backend.app.services.cron import CronLockId


log = logging.getLogger(__name__)


SYNC_KIND = "tracker_poll"
# Overlap window — pad the cursor by 60s so a Linear write that
# landed between two ticks isn't missed because of small clock skew
# between Ship and Linear's servers.
CURSOR_OVERLAP_S = 60


# ---------------------------------------------------------------------------
# Linear GraphQL — focused query for the poller
# ---------------------------------------------------------------------------


# We don't reuse ``LinearTracker.list_tickets`` because it sorts and
# truncates; the poller needs the ``updatedAt`` cursor filter exact
# and an unbounded (page-by-page) fetch. Linear's ``IssueFilter``
# accepts ``updatedAt: { gt: $since }`` against the orderBy axis.
_POLL_GQL = """
query ShipTrackerPoll($filter: IssueFilter, $after: String) {
  issues(first: 100, orderBy: updatedAt, filter: $filter, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      identifier
      title
      state { name }
      labels { nodes { name } }
      updatedAt
    }
  }
}
"""


# Stage labels Linear carries are prefixed (``stage:planning``,
# ``stage:dev_implementation``, …). Strip the prefix to recover the
# raw FSM stage id the dispatcher needs.
_STAGE_LABEL_PREFIX = "stage:"


def _extract_fsm_stage(labels: list[str]) -> str | None:
    """Return the bare stage id from a Linear labels list, or ``None``."""
    for label in labels:
        if isinstance(label, str) and label.startswith(_STAGE_LABEL_PREFIX):
            return label[len(_STAGE_LABEL_PREFIX):].strip() or None
    return None


async def _fetch_updated_issues(
    *,
    token: str,
    team_id: str,
    since_iso: str | None,
    client: httpx.AsyncClient,
) -> list[dict[str, Any]]:
    """Fetch every issue updated since ``since_iso`` for ``team_id``.

    Pages through Linear's cursor-based connection. Returns the raw
    GraphQL nodes (already projected — no need to ship the description
    through the poller).
    """
    filter_: dict[str, Any] = {"team": {"id": {"eq": team_id}}}
    if since_iso:
        filter_["updatedAt"] = {"gt": since_iso}

    after: str | None = None
    out: list[dict[str, Any]] = []
    while True:
        resp = await client.post(
            "https://api.linear.app/graphql",
            json={
                "query": _POLL_GQL,
                "variables": {"filter": filter_, "after": after},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body and body["errors"]:
            raise RuntimeError(
                f"Linear poll returned errors: {body['errors']}"
            )
        issues = body.get("data", {}).get("issues") or {}
        out.extend(issues.get("nodes") or [])
        page = issues.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            break
        after = page.get("endCursor")
        if not after:
            break
    return out


# ---------------------------------------------------------------------------
# Per-workspace poll
# ---------------------------------------------------------------------------


async def _load_cursor(
    session: AsyncSession, installation_id: uuid.UUID
) -> tuple[str | None, dict[str, str]]:
    """Return ``(updated_at_iso, last_seen_states)`` for this installation."""
    row = (
        await session.execute(
            select(NativeIntegrationSyncState).where(
                NativeIntegrationSyncState.installation_id == installation_id,
                NativeIntegrationSyncState.sync_kind == SYNC_KIND,
                NativeIntegrationSyncState.binding_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None or not row.cursor:
        return None, {}
    return row.cursor.get("updated_at"), dict(row.cursor.get("states") or {})


async def _save_cursor(
    session: AsyncSession,
    installation_id: uuid.UUID,
    updated_at_iso: str | None,
    states: dict[str, str],
    *,
    status: str = "ready",
    last_error: str | None = None,
) -> None:
    """Upsert the cursor row for this installation.

    ``native_integration_sync_states`` uses partial unique indexes
    (``binding_id IS NULL`` vs ``IS NOT NULL``) instead of a regular
    UniqueConstraint, so we can't lean on ``on_conflict_do_update`` —
    do the upsert by hand inside one transaction.
    """
    payload: dict[str, Any] = {"states": states}
    if updated_at_iso:
        payload["updated_at"] = updated_at_iso
    existing = (
        await session.execute(
            select(NativeIntegrationSyncState).where(
                NativeIntegrationSyncState.installation_id == installation_id,
                NativeIntegrationSyncState.sync_kind == SYNC_KIND,
                NativeIntegrationSyncState.binding_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing is None:
        session.add(
            NativeIntegrationSyncState(
                installation_id=installation_id,
                binding_id=None,
                sync_kind=SYNC_KIND,
                cursor=payload,
                status=status,
                last_synced_at=now,
                last_error=last_error,
            )
        )
    else:
        existing.cursor = payload
        existing.status = status
        existing.last_synced_at = now
        existing.last_error = last_error


async def _write_transition_event(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    old_state: str | None,
    new_state: str,
    updated_at: str | None,
    fsm_stage: str | None,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Insert a ``tracker.event.received`` row in ``audit_log`` and
    hand the event to the dispatcher.

    ``fsm_stage`` is extracted from the ticket's Linear labels
    (``stage:<id>``) so downstream consumers don't need to re-fetch
    the ticket. ``None`` means the ticket has no FSM stage label
    yet — the dispatcher will refuse to fire because there's no
    routine to run.

    The dispatcher is responsible for the shadow-vs-fire decision —
    when ``SHIP_TRACKER_POLL_FIRE`` is off it just writes an
    ``agent_run.dispatch_shadow`` audit row and returns.
    """
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="tracker.event.received",
            target_kind="ticket",
            target_id=ticket_ref,
            payload={
                "tracker": "linear",
                "old_state": old_state,
                "new_state": new_state,
                "updated_at": updated_at,
                "fsm_stage": fsm_stage,
            },
        )
    )
    # Hand off to the dispatcher. Importing inline because dispatcher
    # imports from tracker_poller (lock id) and we want the dep graph
    # one-directional at module-load time. The function itself is
    # idempotent and never raises on shadow mode.
    from backend.app.services.dispatcher import maybe_dispatch

    await maybe_dispatch(
        session,
        workspace_id=workspace_id,
        ticket_ref=ticket_ref,
        trigger_kind="tracker_poll",
        fsm_stage=fsm_stage,
        client=client,
    )


async def _poll_installation(
    session: AsyncSession,
    install: NativeIntegrationInstallation,
    client: httpx.AsyncClient,
) -> tuple[int, int]:
    """Returns ``(events_emitted, issues_seen)``."""
    # Load fresh access token.
    cred = (
        await session.execute(
            select(NativeIntegrationCredential)
            .where(
                NativeIntegrationCredential.installation_id == install.id,
                NativeIntegrationCredential.kind == "access_token",
                NativeIntegrationCredential.revoked_at.is_(None),
            )
            .order_by(NativeIntegrationCredential.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if cred is None:
        log.warning(
            "tracker_poll: installation %s has no live access_token; "
            "skipping",
            install.id,
        )
        return 0, 0
    token = safe_decrypt(cred.secret_ciphertext)
    if not token:
        log.warning(
            "tracker_poll: installation %s access_token decrypted to "
            "empty; skipping",
            install.id,
        )
        return 0, 0

    # ``native_integration_installations.config`` doesn't carry
    # ``team_id`` in current prod data; it lives on the legacy
    # ``integrations.config`` row for the same workspace. Look it up
    # there. Once we backfill ``team_id`` into the native config (a
    # separate cleanup ticket) this fallback can go.
    team_id = (install.config or {}).get("team_id")
    if not team_id:
        legacy = (
            await session.execute(
                select(Integration)
                .where(
                    Integration.workspace_id == install.workspace_id,
                    Integration.kind == "linear",
                )
                .order_by(Integration.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if legacy and legacy.config:
            team_id = legacy.config.get("team_id")
    if not team_id:
        log.warning(
            "tracker_poll: installation %s has no team_id (native or "
            "legacy); skipping",
            install.id,
        )
        return 0, 0

    cursor_iso, last_states = await _load_cursor(session, install.id)

    issues = await _fetch_updated_issues(
        token=token, team_id=team_id, since_iso=cursor_iso, client=client
    )

    events = 0
    max_updated_at = cursor_iso
    for issue in issues:
        ref = issue.get("identifier")
        new_state = (issue.get("state") or {}).get("name")
        updated_at = issue.get("updatedAt")
        if not ref or not new_state:
            continue
        label_nodes = (issue.get("labels") or {}).get("nodes") or []
        labels = [
            n.get("name") for n in label_nodes
            if isinstance(n, dict) and n.get("name")
        ]
        fsm_stage = _extract_fsm_stage(labels)
        old_state = last_states.get(ref)
        if old_state != new_state:
            await _write_transition_event(
                session,
                workspace_id=install.workspace_id,
                ticket_ref=ref,
                old_state=old_state,
                new_state=new_state,
                updated_at=updated_at,
                fsm_stage=fsm_stage,
                client=client,
            )
            events += 1
        last_states[ref] = new_state
        if updated_at and (
            max_updated_at is None or updated_at > max_updated_at
        ):
            max_updated_at = updated_at

    # Pad the next cursor by ``CURSOR_OVERLAP_S`` so a write that lands
    # in the same second as our fetch isn't missed by the strict ``gt``
    # filter. The diff against ``last_states`` makes the overlap
    # idempotent — we won't re-emit transitions we already saw.
    next_cursor: str | None = max_updated_at
    if next_cursor:
        try:
            dt = datetime.fromisoformat(next_cursor.replace("Z", "+00:00"))
            dt = dt - _td_seconds(CURSOR_OVERLAP_S)
            next_cursor = dt.isoformat()
        except Exception:  # noqa: BLE001 — best-effort, keep raw
            pass

    await _save_cursor(
        session, install.id, next_cursor, last_states, status="ready"
    )
    return events, len(issues)


def _td_seconds(seconds: int):
    from datetime import timedelta

    return timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Public entry point — runs under the cron advisory lock
# ---------------------------------------------------------------------------


# This module deliberately doesn't decorate with ``@cron_with_lock``
# because the lifespan-driven loop wants finer control over the
# per-installation transaction boundary (one bad installation
# shouldn't abort the rest of the tick). We acquire the advisory
# lock manually at the top of the tick.


async def poll_once() -> dict[str, int]:
    """Run one poll tick across every Linear installation.

    Returns a summary dict ``{events: N, issues: M, installs: K,
    errors: E}`` — useful for both logs and the future
    ``/internal/dispatcher/stats`` endpoint.
    """
    sm = get_sessionmaker()
    summary = {"events": 0, "issues": 0, "installs": 0, "errors": 0}
    async with sm() as session:
        # Leader election — only one replica per tick.
        got = (
            await session.execute(
                text("SELECT pg_try_advisory_lock(:k)"),
                {"k": int(CronLockId.TRACKER_POLL)},
            )
        ).scalar_one()
        if not got:
            log.debug("tracker_poll: another replica holds the lock; skip")
            return summary
        try:
            installs = (
                (
                    await session.execute(
                        select(NativeIntegrationInstallation).where(
                            NativeIntegrationInstallation.provider == "linear",
                            NativeIntegrationInstallation.status == "ready",
                        )
                    )
                )
                .scalars()
                .all()
            )
            summary["installs"] = len(installs)

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0)
            ) as client:
                for install in installs:
                    try:
                        events, issues = await _poll_installation(
                            session, install, client
                        )
                        summary["events"] += events
                        summary["issues"] += issues
                        # Commit per-installation so one slow / failing
                        # tenant doesn't block the rest of the tick.
                        await session.commit()
                    except Exception as exc:  # noqa: BLE001
                        await session.rollback()
                        summary["errors"] += 1
                        log.exception(
                            "tracker_poll: install %s failed: %s",
                            install.id,
                            exc,
                        )
                        # Record the failure in sync_state so operators
                        # can see "this tenant is stuck" in the dashboard.
                        try:
                            cursor_iso, last_states = await _load_cursor(
                                session, install.id
                            )
                            await _save_cursor(
                                session,
                                install.id,
                                cursor_iso,
                                last_states,
                                status="error",
                                last_error=str(exc)[:1000],
                            )
                            await session.commit()
                        except Exception:  # noqa: BLE001
                            await session.rollback()
        finally:
            try:
                await session.execute(
                    text("SELECT pg_advisory_unlock(:k)"),
                    {"k": int(CronLockId.TRACKER_POLL)},
                )
            except Exception:  # noqa: BLE001
                pass

    settings = get_settings()
    log.info(
        "tracker_poll: tick done %s fire=%s",
        summary,
        settings.tracker_poll_fire,
    )
    return summary


# ---------------------------------------------------------------------------
# Lifespan-driven loop
# ---------------------------------------------------------------------------


_TASK: asyncio.Task[None] | None = None


async def _loop() -> None:
    settings = get_settings()
    interval = max(15, int(settings.tracker_poll_interval_s))
    log.info("tracker_poll: loop starting (interval=%ss)", interval)
    while True:
        try:
            await poll_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("tracker_poll: unexpected error; will retry")
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise


def start_tracker_poller() -> asyncio.Task[None] | None:
    """Start the poll loop as a background asyncio task.

    Idempotent — calling twice in one process returns the existing
    task. Returns ``None`` when the loop is disabled (interval ≤ 0,
    which lets ops kill the poller in an incident without redeploying).
    """
    global _TASK
    if _TASK is not None and not _TASK.done():
        return _TASK
    settings = get_settings()
    if settings.tracker_poll_interval_s <= 0:
        log.info("tracker_poll: disabled (interval=0)")
        return None
    _TASK = asyncio.create_task(_loop(), name="ship.tracker_poller")
    return _TASK


async def stop_tracker_poller() -> None:
    global _TASK
    if _TASK is None:
        return
    if not _TASK.done():
        _TASK.cancel()
        try:
            await _TASK
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _TASK = None
