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
import re
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
      createdAt
      # Recent comments — used to detect operator answers on tickets
      # carrying ``needs:clarification``. Sorted by Linear's default
      # (createdAt DESC by default on the connection). 20 is enough
      # to cover the most-recent agent question + the answer; older
      # threads don't affect the trigger decision.
      # ``isMe`` + ``botActor`` carry the author identity the
      # agent-vs-human classifier keys on (ELS-250) — Ship writes
      # through the same OAuth identity this poller reads with, so
      # ``isMe: true`` means "our own comment"; app-actor
      # provisioned installs surface as ``botActor`` instead.
      comments(first: 20) {
        nodes {
          id
          body
          createdAt
          user { displayName email isMe }
          botActor { id }
        }
      }
    }
  }
}
"""


# Stage labels Linear carries are prefixed (``stage:planning``,
# ``stage:dev_implementation``, …). Strip the prefix to recover the
# raw FSM stage id the dispatcher needs.
_STAGE_LABEL_PREFIX = "stage:"


def _extract_fsm_stage(labels: list[str], state: str | None = None) -> str | None:
    """Return the bare stage id from a Linear labels list, or ``None``.

    Default-entry fallback: a Linear-native ticket the operator
    files directly often has **no** ``stage:*`` labels at all. If
    the state is ``Todo``, treat that as the implicit entry into
    the SDLC chain (``task_intake`` is the entry stage; the
    dispatcher's _STAGE_TO_ROUTINE maps it to the planning bundle).
    Caught on askslayer/PAC-32..36 2026-05-17: 5 fresh tickets in
    Todo sat with ``dispatch.no_routine`` audit rows for 2+ hours
    because the poller refused to invent a stage for them.

    Terminal-state short-circuit (2026-05-18): a finished chain
    leaves accumulated ``stage:*`` breadcrumbs on the ticket. When
    the auto-merger transitions to Done, the next poll tick sees
    a state change → fires ``tracker.event.received`` → maps to
    fsm_stage via this function. Returning the **first** stage
    label (``planning``) re-dispatched the whole chain — ELS-143
    spun three full SDLC cycles before we caught it. Done /
    Canceled / Duplicate mean "no more work expected"; return None
    so the dispatcher noops.
    """
    if state in ("Done", "Canceled", "Duplicate"):
        return None
    for label in labels:
        if isinstance(label, str) and label.startswith(_STAGE_LABEL_PREFIX):
            return label[len(_STAGE_LABEL_PREFIX):].strip() or None
    if state == "Todo":
        return "task_intake"
    return None


# Agent comments end with ``[Ship SDLC:role-<name>]`` per system.md
# `outcome` rules. Since ELS-250 the marker is only the LEGACY
# fallback — the primary agent-vs-human signal is the structured
# Linear author identity (see :func:`_is_agent_comment`).
_AGENT_COMMENT_MARKER_RE = re.compile(r"\[Ship\s+SDLC:role-[\w-]+\]")


def _is_agent_comment(comment: dict) -> bool:
    """Single source of truth for agent-vs-human on a Linear comment
    (ELS-250). Consumed by the clarification trigger and the
    comment-inbound path so the two can never disagree.

    Identity first, marker second:

    - ``user.isMe`` true → AGENT. Ship writes tracker comments
      through the same OAuth identity the poller reads with, so
      "authored by me" means "authored by the workspace agent".
    - ``botActor`` present → AGENT (app-actor provisioned installs
      surface the OAuth application as the author instead of a user).
    - any OTHER known author → HUMAN, even when the body quotes the
      ``[Ship SDLC:role-…]`` marker (operators paste it when replying
      inline to the agent's question).
    - no author identity at all (legacy rows / older snapshots) →
      fall back to the marker regex.
    """
    if not isinstance(comment, dict):
        return False
    user = comment.get("user")
    if isinstance(user, dict) and user.get("isMe") is True:
        return True
    if comment.get("botActor"):
        return True
    if isinstance(user, dict) and user:
        return False
    body = comment.get("body") or ""
    return bool(_AGENT_COMMENT_MARKER_RE.search(body))


def _operator_answered_clarification(comments: list[dict]) -> bool:
    """Decide whether the operator has replied to the last agent
    clarification question.

    Walks comments newest-first. The first AGENT comment we hit is
    the agent's question (the one that asked). If we see a human
    comment BEFORE the agent comment in time (i.e. AFTER it in the
    newest-first walk's prefix), the operator has answered. Returns
    ``True`` if a fresh operator comment exists since the last agent
    question.
    """
    # Sort newest-first defensively (Linear returns newest-first
    # by default on this connection, but the contract isn't
    # documented in the GraphQL schema we use).
    rows = sorted(
        (c for c in comments if isinstance(c, dict) and c.get("createdAt")),
        key=lambda c: c["createdAt"],
        reverse=True,
    )
    for c in rows:
        if _is_agent_comment(c):
            # Hit the latest agent comment without finding an
            # operator one above it → operator hasn't replied.
            return False
        # Non-agent comment newer than the latest agent comment.
        return True
    return False


async def _clear_clarification_and_dispatch(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ticket_ref: str,
    token: str,
    team_id_for_labels: str | None,
    labels: list[str],
    client: httpx.AsyncClient | None,
) -> None:
    """Strip ``needs:clarification`` from the Linear ticket and emit
    a synthetic ``tracker.event.received`` so the dispatcher
    re-cascades the stage that asked.

    Target stage = the ``fsm_stage`` from the latest
    ``agent_run.finish`` with ``outcome=needs_clarification`` for
    this ticket. NOT the latest stage breadcrumb on the ticket: a
    role that stalls with ``needs_clarification`` never calls
    ``transition()``, so its ``stage:<own>`` breadcrumb never
    gets stamped. Reading breadcrumbs would pick the previous role
    (the one whose transition DID land), not the actual asker.
    Caught on ELS-7 2026-05-17: auto-merger stalled, user
    answered, poller saw labels ``[code_review, dev_implementation,
    validation]``, picked code_review, dispatched reviewer instead
    of auto-merger.
    """
    from backend.app.db.models.tenancy import Integration
    from backend.app.integrations.linear.tracker_adapter import LinearTracker

    latest_clar = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "agent_run.finish",
                AuditLog.payload["ticket_ref"].astext == ticket_ref,
                AuditLog.payload["outcome"].astext == "needs_clarification",
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
    ).scalars().first()
    latest_stage: str | None = None
    if latest_clar and isinstance(latest_clar.payload, dict):
        latest_stage = latest_clar.payload.get("fsm_stage")
    if not latest_stage:
        # Fallback to breadcrumb walk if no recent
        # needs_clarification finish — preserves old behavior for
        # the legacy case where the label appeared without a Ship-
        # side audit row.
        from backend.app.services.linear_provisioner import FSM_STAGE_ORDER
        for stage in reversed(FSM_STAGE_ORDER):
            if f"stage:{stage}" in labels:
                latest_stage = stage
                break

    # Load legacy config for the team_id + label map so we can
    # remove the needs:clarification label.
    legacy = (
        await session.execute(
            select(Integration)
            .where(
                Integration.workspace_id == workspace_id,
                Integration.kind == "linear",
                Integration.repo_id.is_(None),
            )
            .order_by(Integration.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if legacy is None or not legacy.config:
        log.warning(
            "tracker_poll: no legacy linear config for ws=%s, can't "
            "strip needs:clarification on ticket=%s",
            workspace_id, ticket_ref,
        )
        return
    cfg = legacy.config or {}
    clar_label_id = (cfg.get("signal_label_ids") or {}).get(
        "needs_clarification"
    )
    if not clar_label_id:
        log.warning(
            "tracker_poll: signal_label_ids.needs_clarification "
            "missing for ws=%s, can't strip the label on %s",
            workspace_id, ticket_ref,
        )
        return

    tracker = LinearTracker(
        token,
        team_id=cfg.get("team_id"),
        team_key=cfg.get("team_key"),
        state_id_by_name=cfg.get("state_id_by_name") or {},
        label_id_by_stage=cfg.get("label_id_by_stage") or {},
        signal_label_ids=cfg.get("signal_label_ids") or {},
    )
    # Resolve the Linear issue UUID by identifier.
    data = await tracker._gql(
        "query($q:String!){ issue(id:$q){ id } }",
        {"q": ticket_ref},
    )
    issue_id = ((data.get("issue") or {}).get("id"))
    if not issue_id:
        log.warning(
            "tracker_poll: can't resolve issue UUID for %s; "
            "clarification clear skipped",
            ticket_ref,
        )
        return
    await tracker._gql(
        """mutation($id:String!,$input:IssueUpdateInput!){
          issueUpdate(id:$id, input:$input){ success }
        }""",
        {"id": issue_id, "input": {"removedLabelIds": [clar_label_id]}},
    )
    log.info(
        "tracker_poll: cleared needs:clarification on %s "
        "(ws=%s, re-cascading stage=%s)",
        ticket_ref, workspace_id, latest_stage,
    )
    # Reset marker — the refire-cap counter walks audit rows
    # newest-first and treats this row as a "fresh start" (same
    # role as ``outcome=ready_next_step``) so a real bounce →
    # clarify → human answer → retry cycle isn't capped by the
    # prior ``needs_clarification`` finishes. Without this marker
    # the counter sees consecutive non-ready-next-step rows
    # (clarif + clarif + bounce = 3) and refire-caps the NEXT
    # legitimate retry attempt.
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="agent_run.clarification_resolved",
            target_kind="ticket",
            target_id=ticket_ref,
            payload={
                "fsm_stage": latest_stage,
                "trigger": "tracker_poll_comment_scan",
            },
        )
    )
    # Write a synthetic transition event + hand off to dispatcher.
    # ``old_state=new_state`` (no state change), but the
    # ``fsm_stage`` carries the routine the dispatcher should fire.
    await _write_transition_event(
        session,
        workspace_id=workspace_id,
        ticket_ref=ticket_ref,
        old_state=None,
        new_state="In Progress",  # placeholder; dispatcher reads fsm_stage
        updated_at=None,
        fsm_stage=latest_stage,
        client=client,
    )


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
    # Deterministic dispatch order on mass-update batches: when the
    # operator flips a project parked→active, all child tickets'
    # ``updatedAt`` lands within milliseconds, and Linear's batch
    # order is whatever GraphQL returns (effectively undefined for
    # tied timestamps). Sort by ``createdAt`` so PAC-32 fires
    # before PAC-33 — matches the operator's intuition for a
    # sequence of sub-tasks (e.g. Hetzner setup: SSH → Docker →
    # DNS → repos → start). Caught on askslayer/PAC-32..36
    # 2026-05-17. Tickets without ``createdAt`` sort last.
    issues = sorted(
        issues,
        key=lambda i: (i.get("createdAt") or "￿", i.get("identifier") or ""),
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
        fsm_stage = _extract_fsm_stage(labels, state=new_state)
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
        # Clarification-answer detection: if the ticket carries
        # ``needs:clarification`` AND there's a non-agent comment
        # newer than the last agent comment, the operator has
        # answered. Strip the label so the picker stops filtering
        # the ticket out, and fire a synthetic ``tracker.event.
        # received`` so the dispatcher re-cascades whichever stage
        # asked the question. Without this the chain wedges until
        # the operator manually removes the label.
        if "needs:clarification" in labels:
            comment_nodes = (
                (issue.get("comments") or {}).get("nodes") or []
            )
            if _operator_answered_clarification(comment_nodes):
                try:
                    await _clear_clarification_and_dispatch(
                        session,
                        workspace_id=install.workspace_id,
                        ticket_ref=ref,
                        token=token,
                        team_id_for_labels=None,
                        labels=labels,
                        client=client,
                    )
                    events += 1
                except Exception as exc:  # noqa: BLE001 — best-effort
                    log.warning(
                        "tracker_poll: clarification clear failed "
                        "ws=%s ref=%s err=%s",
                        install.workspace_id, ref, exc,
                    )
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
