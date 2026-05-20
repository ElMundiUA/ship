"""Unified inbox HTTP surface (RFC-0010 P2-03 — Plays/Inbox redesign).

Workspace-scoped read + lifecycle endpoints over ``inbox_items`` and
``inbox_item_events``. The Console hits this surface to render the
inbox list, draw a single item, and drive it through its
disposition state machine (resolve / dismiss / approve / reject /
answer / accept / retry / acknowledge), plus snooze / unsnooze /
reassign / comment.

State machine (planning §7; matches RFC-0010 §5):

    action       | from status | type required   | result.status | resolution
    -------------|-------------|-----------------|---------------|--------------
    resolve      | new|snoozed | any             | resolved      | <payload> | acknowledged
    dismiss      | new|snoozed | any             | dismissed     | dismissed
    approve      | new         | approval        | resolved      | approved
    reject       | new         | approval        | resolved      | rejected
    answer       | new         | clarification   | resolved      | answered (requires payload.answer)
    accept       | new         | improvement     | resolved      | accepted
    retry        | new         | failure         | resolved      | retried
    acknowledge  | new         | exception       | resolved      | acknowledged

RBAC (mirrors :mod:`workspaces`):

* Reads (``ROLES_READ``) — any workspace member may list/get items
  and read counts.
* Mutations (`disposition` / `snooze` / `unsnooze` / `events`) — the
  assigned owner of the item OR a workspace admin/owner. Plain
  members cannot resolve someone else's item.
* `reassign` — workspace admin/owner only (you cannot transfer
  ownership of your own item to someone else).

Cursor pagination is opaque: ``base64(JSON([created_at_iso, id]))``
sorted by ``(created_at DESC, id DESC)``. Counts in the list
response respect every filter EXCEPT the dimension being aggregated
(so the UI can show "Clarifications (3)" while the user has the
clarification chip toggled off).

This module never opens or commits a transaction — the caller (the
``get_session`` dependency) owns the boundary, so a 4xx mid-route
rolls back the partial mutation cleanly.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_surface import ChatMessage, ChatThread
from backend.app.db.models.inbox import (
    InboxItem,
    InboxItemEvent,
)
from backend.app.db.models.lanes import RoutineRun
from backend.app.db.models.tenancy import AuditLog, User, WorkspaceMember
from backend.app.db.session import get_session
from backend.app.services.inbox.classification import (
    ACTIONABLE_CATEGORIES,
    derive_lane,
)
from backend.app.services.inbox.profiles import INBOX_TYPES
from backend.app.services.inbox.routing import RoutingContext, resolve_handle
from backend.app.services.inbox.side_effects import apply_side_effects


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/inbox",
    tags=["inbox"],
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


INBOX_STATUSES: tuple[str, ...] = ("new", "snoozed", "resolved", "dismissed")
OPEN_STATUSES: tuple[str, ...] = ("new", "snoozed")
INBOX_CATEGORIES: tuple[str, ...] = ("decision_needed", "failure", "attention")
INBOX_LANES: tuple[str, ...] = ("now", "today", "whenever")
INBOX_SORTS: tuple[str, ...] = (
    "created_desc",
    "priority_desc_created_asc",
)

# Type-restricted disposition actions (planning §7).
_TYPE_GATED_ACTIONS: dict[str, str] = {
    "approve": "approval",
    "reject": "approval",
    "answer": "clarification",
    "accept": "improvement",
    "retry": "failure",
    "acknowledge": "exception",
}

# Action → resolution string written to ``inbox_items.resolution``.
_ACTION_RESOLUTION: dict[str, str] = {
    "approve": "approved",
    "reject": "rejected",
    "answer": "answered",
    "accept": "accepted",
    "retry": "retried",
    "acknowledge": "acknowledged",
    "dismiss": "dismissed",
    # ``resolve`` is open-ended; default below.
    "resolve": "acknowledged",
}

# Actions that expect status='new' OR 'snoozed' as the source state.
_RESOLVABLE_FROM_OPEN: frozenset[str] = frozenset({"resolve", "dismiss"})

_SNOOZE_MAX = timedelta(days=30)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class InboxOwnerOut(BaseModel):
    """Owner projection joined from the ``users`` table."""

    user_id: uuid.UUID
    email: str
    display_name: str | None


class InboxItemOut(BaseModel):
    """Inbox row summary (LIST + lifecycle responses)."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    repo_id: uuid.UUID | None
    type: str
    status: str
    title: str
    summary: str | None
    intake_handle: str | None
    intake_reason: str | None
    owner: InboxOwnerOut | None
    play_key: str | None
    run_id: uuid.UUID | None
    created_at: datetime
    due_at: datetime | None
    snoozed_until: datetime | None
    resolved_at: datetime | None
    resolution: str | None
    category: str
    priority: int
    lane: str
    # Inbox Decision UI (ELS-158): server-derived rendering hint so
    # the Console picks pills / checkboxes / ack / freeform without
    # re-implementing the parser. Falls back to ``freeform_only`` on
    # legacy rows.
    resolution_mode: str
    action_item_count: int = 0
    ticket_ref: str | None = None
    fsm_stage: str | None = None


class InboxItemEventOut(BaseModel):
    """Audit-trail row for an inbox item."""

    id: uuid.UUID
    actor_kind: Literal["user", "system", "agent"]
    actor_user_id: uuid.UUID | None
    action: str
    payload: dict
    created_at: datetime


class InboxItemDetail(InboxItemOut):
    """Full item — adds JSONB payload, source pointers, event tail."""

    payload: dict
    events: list[InboxItemEventOut]
    source_table: str | None
    source_id: uuid.UUID | None


class InboxListResponse(BaseModel):
    """Paginated list response with sidebar counts.

    ``counts_by_type`` / ``counts_by_status`` always include every
    known key with a zero default so the UI can render the chip
    list without nullish-coalescing. They respect the same filters
    as ``items`` EXCEPT the dimension being aggregated — see
    :func:`list_inbox`.
    """

    items: list[InboxItemOut]
    total: int
    counts_by_type: dict[str, int]
    counts_by_status: dict[str, int]
    next_cursor: str | None = None


class InboxCountsResponse(BaseModel):
    """Aggregate counts for navigation badges (see :func:`get_counts`)."""

    mine: int
    unassigned: int
    all_open: int
    actionable_new: int
    reports_new: int
    by_type: dict[str, int]
    by_status: dict[str, int]


class InboxDispositionIn(BaseModel):
    """Apply one of the lifecycle actions in :data:`_ACTION_RESOLUTION`."""

    action: Literal[
        "resolve",
        "dismiss",
        "approve",
        "reject",
        "answer",
        "accept",
        "retry",
        "acknowledge",
    ]
    resolution: str | None = None
    answer: str | None = None
    payload: dict = Field(default_factory=dict)


class InboxSnoozeIn(BaseModel):
    """Future timestamp (≤ 30 days out) to silence the item until."""

    snoozed_until: datetime


class InboxReassignIn(BaseModel):
    """Either a concrete ``user_id`` or a routing ``handle``.

    Validated server-side: exactly one must be present.
    """

    user_id: uuid.UUID | None = None
    handle: str | None = None


class InboxEventAppendIn(BaseModel):
    """Append a free-text comment event to an item."""

    body: str = Field(min_length=1, max_length=10_000)
    payload: dict = Field(default_factory=dict)


class InboxDecideIn(BaseModel):
    """ELS-159 — operator's pick(s) on an action_items-bearing row.

    Either ``selections`` (subset of ``payload.action_items[].id``)
    OR ``freeform`` (free-text override) must be non-empty. Both
    together is fine — selected actions fire side-effects AND the
    freeform note is appended as a comment on the source ticket.
    """

    selections: list[str] = Field(default_factory=list, max_length=32)
    freeform: str | None = Field(default=None, max_length=10_000)


# ---------------------------------------------------------------------------
# Cursor helpers
# ---------------------------------------------------------------------------


def _encode_cursor(created_at: datetime, item_id: uuid.UUID) -> str:
    """Serialise the (created_at, id) tuple as a URL-safe base64 JSON blob.

    JSON over base64 keeps the cursor inspectable in logs while
    surviving query-string round-trips. ``isoformat`` on a tz-aware
    ``datetime`` round-trips losslessly through
    :func:`datetime.fromisoformat` on Python 3.11+.
    """
    raw = json.dumps([created_at.isoformat(), str(item_id)])
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    """Inverse of :func:`_encode_cursor`. 422s on any decoding error."""
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        ts_str, id_str = json.loads(raw)
        ts = datetime.fromisoformat(ts_str)
        item_id = uuid.UUID(id_str)
    except (ValueError, binascii.Error, json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid cursor",
        ) from exc
    return ts, item_id


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def _zeroed(keys: tuple[str, ...]) -> dict[str, int]:
    return {k: 0 for k in keys}


def _to_owner_out(user: User | None) -> InboxOwnerOut | None:
    if user is None:
        return None
    return InboxOwnerOut(
        user_id=user.id, email=user.email, display_name=user.display_name
    )


def _action_item_count(payload: dict | None) -> int:
    raw = (payload or {}).get("action_items")
    if not isinstance(raw, list):
        return 0
    return len(raw)


def _headline_fields(payload: dict | None) -> tuple[str | None, str | None]:
    """Expose copy-transform fields on list rows without full JSONB payload."""
    p = payload or {}
    ref = p.get("ticket_ref")
    stage = p.get("fsm_stage")
    ticket_ref = ref.strip() if isinstance(ref, str) and ref.strip() else None
    fsm_stage = stage.strip() if isinstance(stage, str) and stage.strip() else None
    return ticket_ref, fsm_stage


def _is_mailbox_checklist_entry(entry: object) -> bool:
    """ELS-146 mailbox checklist row (prompt + primary/secondary choices)."""
    if not isinstance(entry, dict):
        return False
    row_id = entry.get("id")
    prompt = entry.get("prompt")
    primary = entry.get("primary")
    secondary = entry.get("secondary")
    if not isinstance(row_id, str) or not row_id.strip():
        return False
    if not isinstance(prompt, str) or not prompt.strip():
        return False
    for side in (primary, secondary):
        if not isinstance(side, dict):
            return False
        label = side.get("label")
        choice = side.get("choice")
        if not isinstance(label, str) or not label.strip():
            return False
        if not isinstance(choice, str) or not choice.strip():
            return False
    return True


def _mailbox_checklist_entries(payload: dict | None) -> list[dict]:
    raw = (payload or {}).get("action_items")
    if not isinstance(raw, list):
        return []
    return [e for e in raw if _is_mailbox_checklist_entry(e)]


def _mailbox_checklist_only(payload: dict | None) -> bool:
    raw = (payload or {}).get("action_items")
    if not isinstance(raw, list) or len(raw) == 0:
        return False
    return all(_is_mailbox_checklist_entry(e) for e in raw)


def _valid_mailbox_checklist_choice(entry: dict, choice: str) -> bool:
    primary = entry.get("primary") if isinstance(entry.get("primary"), dict) else {}
    secondary = (
        entry.get("secondary") if isinstance(entry.get("secondary"), dict) else {}
    )
    allowed = {
        str(primary.get("choice", "")).strip(),
        str(secondary.get("choice", "")).strip(),
    }
    return choice in allowed


async def _load_runs_by_id(
    session: AsyncSession, run_ids: set[uuid.UUID]
) -> dict[uuid.UUID, RoutineRun]:
    if not run_ids:
        return {}
    stmt = select(RoutineRun).where(RoutineRun.id.in_(run_ids))
    rows = (await session.execute(stmt)).scalars().all()
    return {r.id: r for r in rows}


def _to_item_out(
    item: InboxItem,
    owner: User | None,
    *,
    lane: str,
) -> InboxItemOut:
    from backend.app.services.inbox.classification import (
        derive_resolution_mode as _drm,
    )

    ticket_ref, fsm_stage = _headline_fields(item.payload)
    return InboxItemOut(
        id=item.id,
        workspace_id=item.workspace_id,
        repo_id=item.repo_id,
        type=item.type,
        status=item.status,
        title=item.title,
        summary=item.summary,
        intake_handle=item.intake_handle,
        intake_reason=item.intake_reason,
        owner=_to_owner_out(owner),
        play_key=item.play_key,
        run_id=item.run_id,
        created_at=item.created_at,
        due_at=item.due_at,
        snoozed_until=item.snoozed_until,
        resolved_at=item.resolved_at,
        resolution=item.resolution,
        category=item.category,
        priority=item.priority,
        lane=lane,
        resolution_mode=_drm(item.payload, item_type=item.type),
        action_item_count=_action_item_count(item.payload),
        ticket_ref=ticket_ref,
        fsm_stage=fsm_stage,
    )


async def _project_items_out(
    session: AsyncSession,
    page_rows: list[tuple[InboxItem, User | None]],
) -> list[InboxItemOut]:
    run_ids = {item.run_id for item, _ in page_rows if item.run_id is not None}
    runs = await _load_runs_by_id(session, run_ids)
    out: list[InboxItemOut] = []
    for item, owner in page_rows:
        run = runs.get(item.run_id) if item.run_id else None
        lane = derive_lane(item, run=run)
        out.append(_to_item_out(item, owner, lane=lane))
    return out


def _to_event_out(event: InboxItemEvent) -> InboxItemEventOut:
    actor_kind: Literal["user", "system", "agent"]
    if event.actor_kind in ("user", "system", "agent"):
        actor_kind = event.actor_kind  # type: ignore[assignment]
    else:
        actor_kind = "system"
    return InboxItemEventOut(
        id=event.id,
        actor_kind=actor_kind,
        actor_user_id=event.actor_user_id,
        action=event.action,
        payload=event.payload or {},
        created_at=event.created_at,
    )


# ---------------------------------------------------------------------------
# RBAC + loaders
# ---------------------------------------------------------------------------


async def _load_item(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
) -> InboxItem:
    """Fetch one item, scoped by ``workspace_id`` for tenant isolation.

    Returns 404 when the item lives in a different workspace — the
    URL workspace check is already performed by the caller via
    :func:`_require_membership`, but the item could belong to
    another workspace entirely. Pretending it does not exist keeps
    the cross-tenant probe story clean.
    """
    stmt = select(InboxItem).where(
        InboxItem.id == item_id,
        InboxItem.workspace_id == workspace_id,
    )
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="inbox item not found")
    return item


async def _require_owner_or_admin(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    item: InboxItem,
) -> WorkspaceMember:
    """Enforce: caller is the assigned owner OR a workspace admin/owner.

    Plain members cannot mutate items assigned to a teammate — that
    would let any member dispose of work that isn't theirs. Admins
    keep an override so an inbox item is never permanently stuck
    behind an unavailable owner.
    """
    membership = await _require_membership(
        session, workspace_id, user_id, ROLES_READ
    )
    if item.owner_user_id == user_id:
        return membership
    if membership.role in ROLES_ADMIN:
        return membership
    raise HTTPException(
        status_code=403,
        detail=(
            "must be the assigned owner or workspace admin to apply "
            "a disposition"
        ),
    )


# ---------------------------------------------------------------------------
# Filter-application helper (shared by list + counts)
# ---------------------------------------------------------------------------


def _apply_filters(
    stmt,
    *,
    workspace_id: uuid.UUID,
    auth_user_id: uuid.UUID,
    ownership: str,
    types: list[str] | None,
    categories: list[str] | None,
    statuses: list[str] | None,
    repo_id: uuid.UUID | None,
    play_key: str | None,
):
    """Apply the standard list filters to a SELECT.

    Pass ``types=None`` / ``statuses=None`` to skip the respective
    dimension — used by the counts-by-X aggregators that need the
    OTHER filters but not their own dimension.
    """
    stmt = stmt.where(InboxItem.workspace_id == workspace_id)

    if ownership == "mine":
        stmt = stmt.where(InboxItem.owner_user_id == auth_user_id)
    elif ownership == "unassigned":
        stmt = stmt.where(InboxItem.owner_user_id.is_(None))
    # 'all' applies no ownership filter.

    if types:
        stmt = stmt.where(InboxItem.type.in_(types))
    if categories:
        stmt = stmt.where(InboxItem.category.in_(categories))
    if statuses:
        stmt = stmt.where(InboxItem.status.in_(statuses))
    if repo_id is not None:
        stmt = stmt.where(InboxItem.repo_id == repo_id)
    if play_key is not None:
        stmt = stmt.where(InboxItem.play_key == play_key)

    return stmt


def _normalise_statuses(raw: list[str] | None) -> list[str] | None:
    """Default to open items; ``status=all`` unsets the filter."""
    if raw is None or len(raw) == 0:
        return ["new"]
    if any(v == "all" for v in raw):
        return None
    bad = [v for v in raw if v not in INBOX_STATUSES]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"unknown status filter: {bad!r}; "
                f"expected any of {sorted(INBOX_STATUSES)} or 'all'"
            ),
        )
    return list(raw)


def _normalise_types(raw: list[str] | None) -> list[str] | None:
    if raw is None or len(raw) == 0:
        return None
    bad = [v for v in raw if v not in INBOX_TYPES]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"unknown type filter: {bad!r}; "
                f"expected any of {sorted(INBOX_TYPES)}"
            ),
        )
    return list(raw)


def _normalise_categories(raw: list[str] | None) -> list[str] | None:
    if raw is None or len(raw) == 0:
        return None
    bad = [v for v in raw if v not in INBOX_CATEGORIES]
    if bad:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"unknown category filter: {bad!r}; "
                f"expected any of {sorted(INBOX_CATEGORIES)}"
            ),
        )
    return list(raw)


def _normalise_lane(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    if raw not in INBOX_LANES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"unknown lane filter: {raw!r}; "
                f"expected any of {sorted(INBOX_LANES)}"
            ),
        )
    return raw


def _normalise_sort(raw: str | None) -> str:
    if raw is None or raw == "":
        return "priority_desc_created_asc"
    if raw not in INBOX_SORTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"unknown sort: {raw!r}; expected any of {sorted(INBOX_SORTS)}"
            ),
        )
    return raw


# ---------------------------------------------------------------------------
# LIST + COUNTS
# ---------------------------------------------------------------------------


@router.get("", response_model=InboxListResponse)
async def list_inbox(
    workspace_id: uuid.UUID,
    ownership: Literal["mine", "unassigned", "all"] = Query(default="mine"),
    type: list[str] | None = Query(default=None),
    category: list[str] | None = Query(default=None),
    lane: str | None = Query(default=None),
    sort: str | None = Query(default=None),
    status_filter: list[str] | None = Query(default=None, alias="status"),
    repo_id: uuid.UUID | None = Query(default=None),
    play_key: str | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InboxListResponse:
    """Paginated inbox list with type/status/owner filters.

    Sorted by ``(created_at DESC, id DESC)`` for stable cursor
    walks. Owner email/display_name come from a single
    ``LEFT JOIN users`` so unassigned items still appear and there
    is exactly one round-trip per page (mirrors the ``inbox_groups``
    member-count aggregate pattern).

    ``counts_by_type`` and ``counts_by_status`` are computed in two
    additional aggregate queries that share the same filter set
    EXCEPT the dimension being aggregated — so the UI can render
    "Clarifications (3)" even when the user has the clarification
    chip toggled OFF.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    types = _normalise_types(type)
    categories = _normalise_categories(category)
    lane_filter = _normalise_lane(lane)
    sort_key = _normalise_sort(sort)
    statuses = _normalise_statuses(status_filter)

    Owner = User  # alias keeps the join readable.

    base_select = (
        select(InboxItem, Owner)
        .outerjoin(Owner, Owner.id == InboxItem.owner_user_id)
    )
    base_select = _apply_filters(
        base_select,
        workspace_id=workspace_id,
        auth_user_id=auth.user.id,
        ownership=ownership,
        types=types,
        categories=categories,
        statuses=statuses,
        repo_id=repo_id,
        play_key=play_key,
    )

    if sort_key == "priority_desc_created_asc":
        base_select = base_select.order_by(
            InboxItem.priority.desc(),
            InboxItem.created_at.asc(),
            InboxItem.id.asc(),
        )
    else:
        base_select = base_select.order_by(
            InboxItem.created_at.desc(), InboxItem.id.desc()
        )

    fetch_limit = limit + 1
    if lane_filter is not None:
        # Lane is derived from run joins — over-fetch then filter in Python.
        fetch_limit = min(500, max(limit + 1, 100))

    if cursor is not None and sort_key != "priority_desc_created_asc":
        ts, item_id = _decode_cursor(cursor)
        base_select = base_select.where(
            tuple_(InboxItem.created_at, InboxItem.id)
            < tuple_(ts, item_id)
        )

    base_select = base_select.limit(fetch_limit)

    rows = (await session.execute(base_select)).all()
    projected = await _project_items_out(session, rows)
    if lane_filter is not None:
        projected = [i for i in projected if i.lane == lane_filter]
    has_more = len(projected) > limit
    items = projected[:limit]
    page_rows = rows[:limit]
    next_cursor = None
    if has_more and page_rows and sort_key != "priority_desc_created_asc":
        next_cursor = _encode_cursor(page_rows[-1][0].created_at, page_rows[-1][0].id)

    # ``total`` honours every filter (the count of what the user
    # would see if they kept walking). Same predicates as the page
    # query minus the cursor + limit + ordering.
    total_stmt = select(func.count(InboxItem.id))
    total_stmt = _apply_filters(
        total_stmt,
        workspace_id=workspace_id,
        auth_user_id=auth.user.id,
        ownership=ownership,
        types=types,
        categories=categories,
        statuses=statuses,
        repo_id=repo_id,
        play_key=play_key,
    )
    total = int((await session.execute(total_stmt)).scalar_one())
    if lane_filter is not None:
        total = len(items)

    # counts_by_type — drop the type filter so chip counts stay
    # honest while the user is excluding a type.
    by_type_stmt = (
        select(InboxItem.type, func.count(InboxItem.id))
        .group_by(InboxItem.type)
    )
    by_type_stmt = _apply_filters(
        by_type_stmt,
        workspace_id=workspace_id,
        auth_user_id=auth.user.id,
        ownership=ownership,
        types=None,
        categories=categories,
        statuses=statuses,
        repo_id=repo_id,
        play_key=play_key,
    )
    by_type_counts = _zeroed(INBOX_TYPES)
    for type_value, count in (await session.execute(by_type_stmt)).all():
        if type_value in by_type_counts:
            by_type_counts[type_value] = int(count)

    # counts_by_status — drop the status filter for the same reason.
    by_status_stmt = (
        select(InboxItem.status, func.count(InboxItem.id))
        .group_by(InboxItem.status)
    )
    by_status_stmt = _apply_filters(
        by_status_stmt,
        workspace_id=workspace_id,
        auth_user_id=auth.user.id,
        ownership=ownership,
        types=types,
        categories=categories,
        statuses=None,
        repo_id=repo_id,
        play_key=play_key,
    )
    by_status_counts = _zeroed(INBOX_STATUSES)
    for status_value, count in (await session.execute(by_status_stmt)).all():
        if status_value in by_status_counts:
            by_status_counts[status_value] = int(count)

    return InboxListResponse(
        items=items,
        total=total,
        counts_by_type=by_type_counts,
        counts_by_status=by_status_counts,
        next_cursor=next_cursor,
    )


@router.get("/counts", response_model=InboxCountsResponse)
async def get_counts(
    workspace_id: uuid.UUID,
    response: Response,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InboxCountsResponse:
    """Aggregate counts for navigation badges.

    No item rows, no joins — five COUNT/CASE expressions over
    ``inbox_items`` so the navigation poller can hit it on every
    Console screen without measurable cost. ``Cache-Control:
    max-age=10`` lets the browser collapse rapid re-renders into a
    single round-trip.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    response.headers["Cache-Control"] = "max-age=10"

    # Single round-trip: COUNT(CASE WHEN …) for the three buckets.
    bucket_stmt = select(
        func.count(
            case(
                (
                    and_(
                        InboxItem.owner_user_id == auth.user.id,
                        InboxItem.status == "new",
                    ),
                    InboxItem.id,
                )
            )
        ).label("mine"),
        func.count(
            case(
                (
                    and_(
                        InboxItem.owner_user_id.is_(None),
                        InboxItem.status == "new",
                    ),
                    InboxItem.id,
                )
            )
        ).label("unassigned"),
        func.count(
            case(
                (
                    InboxItem.status.in_(OPEN_STATUSES),
                    InboxItem.id,
                )
            )
        ).label("all_open"),
        func.count(
            case(
                (
                    and_(
                        InboxItem.category.in_(tuple(ACTIONABLE_CATEGORIES)),
                        InboxItem.status == "new",
                    ),
                    InboxItem.id,
                )
            )
        ).label("actionable_new"),
        func.count(
            case(
                (
                    and_(
                        InboxItem.category == "attention",
                        InboxItem.type == "report",
                        InboxItem.status == "new",
                    ),
                    InboxItem.id,
                )
            )
        ).label("reports_new"),
    ).where(InboxItem.workspace_id == workspace_id)
    bucket_row = (await session.execute(bucket_stmt)).one()

    by_type = _zeroed(INBOX_TYPES)
    by_type_stmt = (
        select(InboxItem.type, func.count(InboxItem.id))
        .where(
            InboxItem.workspace_id == workspace_id,
            InboxItem.status == "new",
        )
        .group_by(InboxItem.type)
    )
    for type_value, count in (await session.execute(by_type_stmt)).all():
        if type_value in by_type:
            by_type[type_value] = int(count)

    by_status = _zeroed(INBOX_STATUSES)
    by_status_stmt = (
        select(InboxItem.status, func.count(InboxItem.id))
        .where(InboxItem.workspace_id == workspace_id)
        .group_by(InboxItem.status)
    )
    for status_value, count in (await session.execute(by_status_stmt)).all():
        if status_value in by_status:
            by_status[status_value] = int(count)

    return InboxCountsResponse(
        mine=int(bucket_row.mine),
        unassigned=int(bucket_row.unassigned),
        all_open=int(bucket_row.all_open),
        actionable_new=int(bucket_row.actionable_new),
        reports_new=int(bucket_row.reports_new),
        by_type=by_type,
        by_status=by_status,
    )


# ---------------------------------------------------------------------------
# DETAIL
# ---------------------------------------------------------------------------


async def _build_detail(
    session: AsyncSession, item: InboxItem
) -> InboxItemDetail:
    """Hydrate an item into the detail projection.

    Loads the owner user (if any) and the full event tail in
    ``created_at ASC`` order so the UI can render the timeline
    chronologically without sorting client-side.
    """
    owner = (
        await session.get(User, item.owner_user_id)
        if item.owner_user_id is not None
        else None
    )
    events_stmt = (
        select(InboxItemEvent)
        .where(InboxItemEvent.item_id == item.id)
        .order_by(InboxItemEvent.created_at.asc(), InboxItemEvent.id.asc())
    )
    event_rows = (await session.execute(events_stmt)).scalars().all()
    run = await session.get(RoutineRun, item.run_id) if item.run_id else None
    lane = derive_lane(item, run=run)
    base = _to_item_out(item, owner, lane=lane)
    return InboxItemDetail(
        **base.model_dump(),
        payload=item.payload or {},
        source_table=item.source_table,
        source_id=item.source_id,
        events=[_to_event_out(e) for e in event_rows],
    )


@router.get("/{item_id}", response_model=InboxItemDetail)
async def get_item(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InboxItemDetail:
    """Full item detail — payload, owner, and event tail.

    RBAC: any workspace member (``ROLES_READ``). The event tail is
    returned in ``created_at`` ascending order so the Console can
    render the timeline top-to-bottom without sorting client-side.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    item = await _load_item(session, workspace_id, item_id)
    return await _build_detail(session, item)


# ---------------------------------------------------------------------------
# DISPOSITION
# ---------------------------------------------------------------------------


def _validate_disposition(action: str, item: InboxItem, payload: dict) -> str:
    """Validate the (action, status, type) tuple against the state machine.

    Returns the resolved ``resolution`` string to stamp on the
    item. Raises 422 on any state-machine violation. ``resolve``
    accepts a free-form ``resolution`` from the payload (defaults
    to ``acknowledged``); ``answer`` requires ``payload.answer``.
    """
    required_type = _TYPE_GATED_ACTIONS.get(action)
    if required_type is not None and item.type != required_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"action {action!r} is only valid for items of type "
                f"{required_type!r} (this item is type {item.type!r})"
            ),
        )

    if action in _RESOLVABLE_FROM_OPEN:
        if item.status not in OPEN_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"action {action!r} requires status in {list(OPEN_STATUSES)};"
                    f" item is currently {item.status!r}"
                ),
            )
    else:
        if item.status != "new":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"action {action!r} requires status='new'; "
                    f"item is currently {item.status!r}"
                ),
            )

    if action == "answer" and not payload.get("answer"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="action 'answer' requires payload.answer to be set",
        )

    if action == "resolve":
        return str(payload.get("resolution") or _ACTION_RESOLUTION["resolve"])
    return _ACTION_RESOLUTION[action]


async def _apply_mailbox_checklist_pick_if_partial(
    session: AsyncSession,
    item: InboxItem,
    auth: AuthContext,
    payload: dict,
    action: str,
) -> InboxItemDetail | None:
    """Record one checklist answer; resolve only after the last row is answered."""
    action_item_id = str(payload.get("action_item_id") or "").strip()
    choice = str(payload.get("choice") or "").strip()
    if not action_item_id or not choice:
        return None
    if not _mailbox_checklist_only(item.payload):
        return None

    entries = _mailbox_checklist_entries(item.payload)
    by_id = {str(e["id"]).strip(): e for e in entries}
    if action_item_id not in by_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid action_item_id",
        )
    if not _valid_mailbox_checklist_choice(by_id[action_item_id], choice):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="invalid choice",
        )

    base = dict(item.payload or {})
    answers = dict(base.get("checklist_answers") or {})
    answers[action_item_id] = choice
    base["checklist_answers"] = answers

    raw_items = base.get("action_items")
    if not isinstance(raw_items, list):
        raw_items = []
    base["action_items"] = [
        e
        for e in raw_items
        if not (
            isinstance(e, dict)
            and str(e.get("id", "")).strip() == action_item_id
        )
    ]

    merge_extra = {
        k: v
        for k, v in payload.items()
        if k not in ("action_item_id", "choice")
    }
    item.payload = base | merge_extra

    session.add(
        InboxItemEvent(
            item_id=item.id,
            actor_user_id=auth.user.id,
            actor_kind="user",
            action="checklist_answer",
            payload={
                "action_item_id": action_item_id,
                "choice": choice,
            },
        )
    )

    if _mailbox_checklist_entries(item.payload):
        await session.flush()
        await session.refresh(item)
        return await _build_detail(session, item)

    if action != "resolve":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="checklist complete requires action resolve",
        )
    return None


@router.post("/{item_id}/disposition", response_model=InboxItemDetail)
async def post_disposition(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    body: InboxDispositionIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InboxItemDetail:
    """Drive an item through its lifecycle (resolve / dismiss / ...).

    The state-machine table lives in :data:`_TYPE_GATED_ACTIONS` /
    :data:`_ACTION_RESOLUTION` and is enforced by
    :func:`_validate_disposition` (422 on any invalid tuple). On
    success we stamp ``resolved_at`` / ``resolved_by_user_id`` /
    ``resolution`` on the item, MERGE the disposition payload into
    ``inbox_items.payload`` (PEP 584 — never replaces), append a
    ``resolved`` event, mark the matching :class:`RunEscalation`
    rows as resolved (when present), and write an
    ``inbox.disposition.<action>`` audit row.
    """
    item = await _load_item(session, workspace_id, item_id)
    await _require_owner_or_admin(
        session, workspace_id, auth.user.id, item
    )

    payload = body.payload or {}
    if body.answer is not None:
        payload = {**payload, "answer": body.answer}
    if body.resolution is not None:
        payload = {**payload, "resolution": body.resolution}

    partial = await _apply_mailbox_checklist_pick_if_partial(
        session, item, auth, payload, body.action
    )
    if partial is not None:
        return partial

    resolution = _validate_disposition(body.action, item, payload)
    now = datetime.now(timezone.utc)

    # MERGE the disposition payload into the existing JSONB rather
    # than replacing — otherwise a single resolve wipes whatever
    # the intake originally stored (e.g. ``requires_approval``).
    merged_payload = (item.payload or {}) | dict(payload)
    item.payload = merged_payload
    item.status = "resolved" if body.action != "dismiss" else "dismissed"
    item.resolution = resolution
    item.resolved_at = now
    item.resolved_by_user_id = auth.user.id

    session.add(
        InboxItemEvent(
            item_id=item.id,
            actor_user_id=auth.user.id,
            actor_kind="user",
            action="resolved" if item.status == "resolved" else "dismissed",
            payload={
                "disposition": body.action,
                "resolution": resolution,
                **{k: v for k, v in payload.items() if k != "resolution"},
            },
        )
    )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action=f"inbox.disposition.{body.action}",
            target_kind="inbox_item",
            target_id=str(item.id),
            payload={
                "type": item.type,
                "resolution": resolution,
                "from_status": "new",
            },
        )
    )

    # Disposition-specific side-effects (escalation close, legacy
    # writebacks, retry signal, …) live in the dedicated dispatcher
    # so the route stays focused on validation + state transitions.
    # Best-effort: any failure surfaces in ``report.failures`` and
    # never short-circuits the disposition itself.
    report = await apply_side_effects(
        session,
        item=item,
        action=body.action,
        payload=payload,
        actor_user_id=auth.user.id,
    )
    logger.info(
        "inbox disposition side-effects: writebacks=%d, "
        "escalations_closed=%d, retry=%d, failures=%d",
        len(report.legacy_writebacks),
        len(report.escalations_closed),
        len(report.retry_requests_recorded),
        len(report.failures),
    )
    for failure in report.failures:
        logger.warning(
            "inbox disposition side-effect FAILED (item=%s, action=%s): "
            "kind=%s error=%s",
            item.id,
            body.action,
            failure.get("kind"),
            failure.get("error"),
        )

    await session.flush()
    await session.refresh(item)
    return await _build_detail(session, item)


# ---------------------------------------------------------------------------
# DECIDE — Inbox Decision UI (ELS-159)
# ---------------------------------------------------------------------------


@router.post("/{item_id}/decide", response_model=InboxItemDetail)
async def post_decide(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    body: InboxDecideIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InboxItemDetail:
    """Operator's structured pick on a row carrying ``action_items``.

    Each selection id is matched against
    ``inbox_items.payload.action_items[].id``. Side-effects route by
    the matched item's ``kind``:

    - ``kind=choice``  → post a Linear comment with ``label`` on the
      source ticket and strip ``needs:clarification`` if present.
    - ``kind=checkbox`` → create a new Linear ticket via the bound
      tracker (``title=label``, ``body=hint``,
      ``project_id=target_project_id`` falling back to the source
      ticket's project).
    - ``kind=ack`` → no side-effect; just mark resolved.

    A non-empty ``freeform`` is appended as a comment on the source
    ticket (in addition to any selected actions). Empty selections +
    non-empty freeform mirrors the legacy quick-reply path.

    **Idempotency:** if the item is already ``resolved`` / ``dismissed``,
    return the current detail with HTTP 200 and write a no-op event
    (``action='resolved'``, ``payload.is_replay=True``). Operator
    double-clicks do not create duplicate tickets.
    """
    from backend.app.services.inbox.classification import parse_action_items

    item = await _load_item(session, workspace_id, item_id)
    await _require_owner_or_admin(
        session, workspace_id, auth.user.id, item
    )

    selections = list(body.selections or [])
    freeform = (body.freeform or "").strip() or None
    if not selections and not freeform:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "decide_empty",
                "message": (
                    "Provide at least one selection id or a freeform note."
                ),
            },
        )

    # Idempotent replay: caller already resolved this row. Echo back.
    if item.status in ("resolved", "dismissed"):
        session.add(
            InboxItemEvent(
                item_id=item.id,
                actor_user_id=auth.user.id,
                actor_kind="user",
                action="resolved",
                payload={
                    "selections": selections,
                    "freeform": freeform,
                    "is_replay": True,
                },
            )
        )
        await session.flush()
        return await _build_detail(session, item)

    parsed = parse_action_items(item.payload)
    by_id = {ai.id: ai for ai in parsed}
    unknown = [sid for sid in selections if sid not in by_id]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "decide_unknown_selection",
                "message": (
                    f"Selection ids not in this item's action_items: {unknown}"
                ),
                "known": list(by_id.keys()),
            },
        )

    # Resolve the bound tracker once — side-effects per kind reuse it.
    from backend.app.core.config import get_settings as _gs
    from backend.app.integrations.gateway.tracker import TicketRef as _TR
    from backend.app.services.tracker_resolver import (
        resolve_for_workspace as _resolve,
    )

    resolved_tracker = await _resolve(
        session=session, settings=_gs(), workspace_id=workspace_id
    )
    source_ticket_ref: str | None = (
        (item.payload or {}).get("ticket_ref")
        if isinstance(item.payload, dict)
        else None
    )

    side_effects: list[dict[str, Any]] = []
    created_ticket_refs: list[str] = []

    for sid in selections:
        ai = by_id[sid]
        try:
            # NEW (2026-05-20): server-side executors. Action items
            # with an ``executor`` field bypass the legacy "comment on
            # Linear" path and trigger real GH / Linear / dispatcher
            # operations. Operator wanted "press button → Ship does
            # it" instead of "press button → comment posted, now go
            # manually finish the work elsewhere".
            if ai.executor:
                from backend.app.services.inbox.action_executors import (
                    run_action_executor,
                )
                result = await run_action_executor(
                    session,
                    workspace_id=workspace_id,
                    item=item,
                    executor=ai.executor,
                    freeform=freeform,
                )
                side_effects.append(
                    {"id": sid, "executor": ai.executor, **result}
                )
                continue
            if ai.kind == "choice":
                if resolved_tracker is None or not source_ticket_ref:
                    side_effects.append(
                        {"id": sid, "kind": "choice", "result": "skipped_no_tracker"}
                    )
                    continue
                target = _TR(
                    kind=resolved_tracker.kind,
                    workspace_hint=None,
                    id=source_ticket_ref,
                )
                comment_body = f"**Answer:** {ai.label}"
                if ai.hint:
                    comment_body += f"\n\n{ai.hint}"
                await resolved_tracker.gateway.comment(
                    target, body=comment_body
                )
                # Strip needs:clarification if present (best-effort).
                try:
                    await resolved_tracker.gateway.remove_label(
                        target, "needs:clarification"
                    )
                except Exception:  # noqa: BLE001
                    pass
                side_effects.append(
                    {"id": sid, "kind": "choice", "result": "comment_posted"}
                )
            elif ai.kind == "checkbox":
                if resolved_tracker is None:
                    side_effects.append(
                        {"id": sid, "kind": "checkbox", "result": "skipped_no_tracker"}
                    )
                    continue
                create_fn = getattr(
                    resolved_tracker.gateway, "create_ticket", None
                )
                if create_fn is None:
                    side_effects.append(
                        {"id": sid, "kind": "checkbox", "result": "skipped_no_create"}
                    )
                    continue
                pid = ai.target_project_id
                if not pid and source_ticket_ref:
                    snap_fn = getattr(
                        resolved_tracker.gateway,
                        "get_ticket_snapshot",
                        None,
                    )
                    if snap_fn is not None:
                        snap = await snap_fn(
                            _TR(
                                kind=resolved_tracker.kind,
                                workspace_hint=None,
                                id=source_ticket_ref,
                            )
                        )
                        if snap:
                            pid = snap.get("project_id")
                created = await create_fn(
                    title=ai.label,
                    body=ai.hint or "",
                    project_id=pid,
                )
                ref_str = (
                    created.display_id
                    if hasattr(created, "display_id")
                    else getattr(created, "id", None)
                )
                if ref_str:
                    created_ticket_refs.append(str(ref_str))
                side_effects.append(
                    {
                        "id": sid,
                        "kind": "checkbox",
                        "result": "ticket_created",
                        "ticket_ref": ref_str,
                    }
                )
            elif ai.kind == "ack":
                side_effects.append({"id": sid, "kind": "ack", "result": "acknowledged"})
        except Exception as exc:  # noqa: BLE001 — log + continue
            logger.warning(
                "inbox.decide side-effect failed item=%s selection=%s: %s",
                item.id, sid, exc,
            )
            side_effects.append(
                {"id": sid, "kind": ai.kind, "result": "error", "error": str(exc)[:200]}
            )

    # Freeform note — append as a Linear comment on the source ticket.
    if freeform:
        if resolved_tracker is not None and source_ticket_ref:
            try:
                await resolved_tracker.gateway.comment(
                    _TR(
                        kind=resolved_tracker.kind,
                        workspace_hint=None,
                        id=source_ticket_ref,
                    ),
                    body=freeform,
                )
                side_effects.append({"kind": "freeform", "result": "comment_posted"})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "inbox.decide freeform comment failed item=%s: %s",
                    item.id, exc,
                )
                side_effects.append(
                    {"kind": "freeform", "result": "error", "error": str(exc)[:200]}
                )
        else:
            side_effects.append({"kind": "freeform", "result": "stored_only"})

    # Mark resolved. Resolution value derives from item type so existing
    # consumers (status filter, badge color) keep working.
    resolution = "answered"
    if item.type in ("failure", "blocker", "exception"):
        resolution = "acknowledged"
    elif item.type == "improvement":
        resolution = "accepted"
    elif item.type == "approval":
        resolution = "approved"

    now = datetime.now(timezone.utc)
    item.status = "resolved"
    item.resolution = resolution
    item.resolved_at = now
    item.resolved_by_user_id = auth.user.id
    # Drop the action_items from payload — they were one-shot.
    if isinstance(item.payload, dict) and "action_items" in item.payload:
        new_payload = dict(item.payload)
        new_payload.pop("action_items", None)
        new_payload["decided"] = {
            "selections": selections,
            "freeform": freeform,
            "side_effects": side_effects,
            "created_ticket_refs": created_ticket_refs,
        }
        item.payload = new_payload

    session.add(
        InboxItemEvent(
            item_id=item.id,
            actor_user_id=auth.user.id,
            actor_kind="user",
            action="resolved",
            payload={
                "via": "decide",
                "selections": selections,
                "freeform": freeform,
                "side_effects": side_effects,
                "created_ticket_refs": created_ticket_refs,
            },
        )
    )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox.decide",
            target_kind="inbox_item",
            target_id=str(item.id),
            payload={
                "type": item.type,
                "resolution": resolution,
                "selections": selections,
                "created_ticket_refs": created_ticket_refs,
            },
        )
    )
    await session.flush()
    await session.refresh(item)
    return await _build_detail(session, item)


# ---------------------------------------------------------------------------
# SNOOZE / UNSNOOZE
# ---------------------------------------------------------------------------


@router.post("/{item_id}/snooze", response_model=InboxItemDetail)
async def snooze_item(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    body: InboxSnoozeIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InboxItemDetail:
    """Silence the item until ``snoozed_until`` (≤ 30 days out).

    Idempotent: snoozing an already-snoozed item just shifts the
    deadline (extend or shorten) — the status stays ``snoozed``
    and a fresh event is appended so the audit trail still records
    the change. Past timestamps and snoozes longer than 30 days
    are rejected with 422 so a misconfigured client cannot park
    work indefinitely.
    """
    item = await _load_item(session, workspace_id, item_id)
    await _require_owner_or_admin(
        session, workspace_id, auth.user.id, item
    )

    now = datetime.now(timezone.utc)
    snooze_until = body.snoozed_until
    if snooze_until.tzinfo is None:
        snooze_until = snooze_until.replace(tzinfo=timezone.utc)

    if snooze_until <= now:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="snoozed_until must be in the future",
        )
    if snooze_until - now > _SNOOZE_MAX:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="snooze cap is 30 days; reassign or dismiss instead",
        )
    if item.status not in OPEN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"can only snooze items in status {list(OPEN_STATUSES)}; "
                f"item is currently {item.status!r}"
            ),
        )

    item.status = "snoozed"
    item.snoozed_until = snooze_until

    session.add(
        InboxItemEvent(
            item_id=item.id,
            actor_user_id=auth.user.id,
            actor_kind="user",
            action="snoozed",
            payload={"snoozed_until": snooze_until.isoformat()},
        )
    )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox.snooze",
            target_kind="inbox_item",
            target_id=str(item.id),
            payload={"snoozed_until": snooze_until.isoformat()},
        )
    )

    await session.flush()
    await session.refresh(item)
    return await _build_detail(session, item)


@router.post("/{item_id}/unsnooze", response_model=InboxItemDetail)
async def unsnooze_item(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InboxItemDetail:
    """Wake a snoozed item — only valid from ``snoozed`` (422 otherwise).

    Clears ``snoozed_until`` and flips the status back to ``new``.
    A fresh ``unsnoozed`` event is appended so the timeline shows
    the wake-up explicitly even when the wake was operator-driven
    rather than the snooze-expiry sweeper.
    """
    item = await _load_item(session, workspace_id, item_id)
    await _require_owner_or_admin(
        session, workspace_id, auth.user.id, item
    )

    if item.status != "snoozed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"unsnooze requires status='snoozed'; "
                f"item is currently {item.status!r}"
            ),
        )

    item.status = "new"
    item.snoozed_until = None

    session.add(
        InboxItemEvent(
            item_id=item.id,
            actor_user_id=auth.user.id,
            actor_kind="user",
            action="unsnoozed",
            payload={},
        )
    )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox.unsnooze",
            target_kind="inbox_item",
            target_id=str(item.id),
            payload={},
        )
    )

    await session.flush()
    await session.refresh(item)
    return await _build_detail(session, item)


# ---------------------------------------------------------------------------
# REASSIGN
# ---------------------------------------------------------------------------


@router.post("/{item_id}/reassign", response_model=InboxItemDetail)
async def reassign_item(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    body: InboxReassignIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InboxItemDetail:
    """Move an item to a new owner (admin-only).

    Two modes:

    * ``user_id`` — pin the item to a specific workspace member.
      ``intake_handle`` is cleared and ``intake_reason`` set to
      ``manual:admin`` so the audit trail reflects the override.
    * ``handle`` — re-resolve via the routing service. The
      resulting ``user_id`` / ``intake_handle`` / ``intake_reason``
      are written verbatim. An unresolved handle (resolver returns
      ``user_id=None``) becomes a 422 — admins should not reassign
      via a handle that has no active route.

    Exactly one of the two fields must be present; both 422.
    """
    await _require_membership(
        session, workspace_id, auth.user.id, ROLES_ADMIN
    )
    if (body.user_id is None) == (body.handle is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="exactly one of user_id / handle must be provided",
        )

    item = await _load_item(session, workspace_id, item_id)
    old_owner = item.owner_user_id

    if body.user_id is not None:
        membership_stmt = select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == body.user_id,
        )
        if (await session.execute(membership_stmt)).scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="target user is not a workspace member",
            )
        new_owner = body.user_id
        new_handle: str | None = None
        new_reason = "manual:admin"
    else:
        ctx = RoutingContext(
            workspace_id=workspace_id,
            repo_id=item.repo_id,
            run_id=item.run_id,
            source_row={},
        )
        resolved = await resolve_handle(session, body.handle or "", ctx)
        if resolved.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="handle resolved to no user",
            )
        new_owner = resolved.user_id
        new_handle = resolved.intake_handle
        new_reason = resolved.intake_reason

    item.owner_user_id = new_owner
    item.intake_handle = new_handle
    item.intake_reason = new_reason

    event_payload = {
        "old_owner_user_id": str(old_owner) if old_owner else None,
        "new_owner_user_id": str(new_owner),
        "intake_reason": new_reason,
    }
    if body.handle is not None:
        event_payload["handle"] = body.handle

    session.add(
        InboxItemEvent(
            item_id=item.id,
            actor_user_id=auth.user.id,
            actor_kind="user",
            action="reassigned",
            payload=event_payload,
        )
    )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox.reassign",
            target_kind="inbox_item",
            target_id=str(item.id),
            payload=event_payload,
        )
    )

    await session.flush()
    await session.refresh(item)
    return await _build_detail(session, item)


# ---------------------------------------------------------------------------
# COMMENT EVENTS
# ---------------------------------------------------------------------------


@router.post(
    "/{item_id}/events",
    response_model=InboxItemEventOut,
    status_code=status.HTTP_201_CREATED,
)
async def append_event(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    body: InboxEventAppendIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InboxItemEventOut:
    """Append a comment-style ``commented`` event to an item.

    RBAC: same as disposition — the assigned owner OR a workspace
    admin/owner. Comments live alongside lifecycle events on the
    timeline (``InboxItemDetail.events``) sorted by ``created_at``
    ascending.
    """
    item = await _load_item(session, workspace_id, item_id)
    await _require_owner_or_admin(
        session, workspace_id, auth.user.id, item
    )

    event = InboxItemEvent(
        item_id=item.id,
        actor_user_id=auth.user.id,
        actor_kind="user",
        action="commented",
        payload={"body": body.body, **dict(body.payload or {})},
    )
    session.add(event)
    await session.flush()
    await session.refresh(event)
    return _to_event_out(event)


class InboxDiscussOut(BaseModel):
    """Response of the discuss-with-Navigator endpoint."""

    thread_id: uuid.UUID
    inbox_item_id: uuid.UUID


@router.post("/{item_id}/discuss", response_model=InboxDiscussOut)
async def discuss_with_navigator(
    workspace_id: uuid.UUID,
    item_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InboxDiscussOut:
    """Open a fresh Navigator chat thread seeded with this inbox item's context.

    Used by the "Discuss with Navigator" button on a clarification (or
    blocker) row. Archives the user's current active thread, opens a
    new one titled around the linked ticket, and drops a ``system``
    message with the ticket snapshot + the agent's question so the
    Navigator has full context from message zero. The user's first
    message in the new thread is whatever they type — the Navigator
    just answers from a prepared context.

    Returns the new thread id; the Console redirects the browser to
    ``/chat?from_inbox=<item_id>`` so the chat surface can render a
    "linked clarification" banner with a back-link.
    """
    item = await _load_item(session, workspace_id, item_id)
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    # Archive the caller's current active thread (mirror /chat/active/new
    # without packing into a bucket — operator chose to switch context
    # explicitly, so the previous thread shouldn't auto-pack).
    cur_stmt = select(ChatThread).where(
        ChatThread.workspace_id == workspace_id,
        ChatThread.created_by_user_id == auth.user.id,
        ChatThread.status == "active",
    )
    for cur in (await session.execute(cur_stmt)).scalars().all():
        cur.status = "archived"

    # Compose the system seed message. Pulls everything we have on the
    # source ticket + the agent's question/blocker — keeps the LLM
    # well-grounded so it can immediately help the operator draft a
    # response without the operator having to re-paste anything.
    payload = dict(item.payload or {})
    snap = payload.get("source_ticket") or {}
    ref = (
        snap.get("ticket_ref")
        or payload.get("ticket_ref")
        or "(unknown ticket)"
    )
    title = snap.get("title") or "(no title)"
    description = (snap.get("description") or "").strip()
    state = snap.get("state") or ""
    labels = snap.get("labels") or []

    seed_lines: list[str] = []
    seed_lines.append(
        "You are helping the operator answer an inbox item posted by a Ship "
        f"agent. Item type: **{item.type}**. Source ticket: **{ref}**."
    )
    seed_lines.append("")
    seed_lines.append(f"## Source ticket {ref}")
    seed_lines.append(f"- **Title:** {title}")
    if state:
        seed_lines.append(f"- **State:** {state}")
    if labels:
        seed_lines.append(f"- **Labels:** {', '.join(labels)}")
    if snap.get("url"):
        seed_lines.append(f"- **URL:** {snap['url']}")
    if description:
        seed_lines.append("")
        seed_lines.append("### Ticket description")
        seed_lines.append(description)

    if item.summary:
        seed_lines.append("")
        seed_lines.append(f"## Agent's {item.type}")
        seed_lines.append(item.summary)

    seed_lines.append("")
    seed_lines.append(
        "Help the operator draft a clear answer. When the operator confirms "
        "the wording, summarise the agreed answer at the end of your reply "
        "so they can copy it back into the tracker. Be concise and ask "
        "follow-ups one at a time."
    )

    fresh = ChatThread(
        workspace_id=workspace_id,
        created_by_user_id=auth.user.id,
        title=f"Re: {item.type} {ref}"[:512],
        status="active",
        last_user_activity_at=datetime.now(timezone.utc),
    )
    session.add(fresh)
    await session.flush()
    await session.refresh(fresh)

    seed = ChatMessage(
        thread_id=fresh.id,
        role="system",
        author_user_id=None,
        body="\n".join(seed_lines),
        meta={
            "kind": "inbox_discuss_seed",
            "inbox_item_id": str(item.id),
            "inbox_type": item.type,
            "ticket_ref": payload.get("ticket_ref"),
        },
    )
    session.add(seed)
    await session.flush()

    # Audit row so operators can trace which inbox items spawned which
    # navigator threads (paired with the eventual close-from-navigator
    # action that resolves the inbox row from inside the chat surface).
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox.discuss_with_navigator",
            target_kind="inbox_item",
            target_id=str(item.id),
            payload={
                "thread_id": str(fresh.id),
                "inbox_type": item.type,
                "ticket_ref": payload.get("ticket_ref"),
            },
        )
    )

    return InboxDiscussOut(thread_id=fresh.id, inbox_item_id=item.id)


__all__ = ["router"]
