"""Inbox routing-rules CRUD (RFC-0010 Plays / Inbox redesign, P2-05).

Workspace-scoped admin surface for ``inbox_routing_rules`` — the
mapping table that turns a Play's symbolic ``handle`` (``secops``,
``repo_maintainer``, ``incident_commander``, …) into a concrete
target (a single user, an operational :class:`MemberGroup`, or one
of the built-in resolver strategies named in
``services.inbox.routing._BUILTIN_HANDLES``). The resolver
(``services.inbox.routing.resolve_handle``, P2-06) consults these
rows at intake time; this module is the human side of the equation.

Vocabulary surfaced by ``GET /handles`` (the cleverest endpoint
here — it powers the admin UI's "configuration health" panel):

- ``bound_handles``   — handle has at least one row in
  ``inbox_routing_rules`` for this workspace.
- ``used_handles``    — handle is referenced by an emit rule in the
  shipped profile catalog (``profile_catalog.yaml``); these are the
  handles a Play *might* ask the resolver for at runtime.
- ``orphaned_handles`` — bound but not used; the rule will never
  fire (operator should clean up).
- ``unbound_handles`` — used but not bound; intake will fall back
  to the built-in chain (``workspace_admin → workspace_owner``).

Schema realities worth knowing before reading the handlers:

- The DB enforces ``UNIQUE (workspace_id, handle_key)`` (planning
  §4) — exactly **one** rule per handle per workspace. There is no
  ``priority`` column; ordering is purely by ``created_at``.
- ``target_value`` is a single VARCHAR that means different things
  per ``target_type``: a UUID string for ``user``, a
  ``member_groups.key`` for ``group``, a strategy name for
  ``strategy``. The HTTP surface splits this back into typed
  fields (``target_user_id`` / ``target_group_id`` /
  ``target_strategy``) so admins don't hand-pack the column.

The ``POST /preview`` endpoint is **side-effect free by design**:
``resolve_handle`` may UPSERT ``group_assignment_state`` for
``round_robin`` dispatch, so the preview path wraps the call in a
SAVEPOINT and ROLLBACKs unconditionally. The preview's whole point
is "what would this rule do *today*?" — admins lose trust in the
button the moment it nudges round-robin pointers.

All mutations are admin-only (``ROLES_ADMIN``); reads accept any
workspace member (``ROLES_READ``). Every mutation writes an
``AuditLog`` row (``inbox_routing.create`` / ``.update`` /
``.delete`` / ``.preview``) for retroactive "who changed routing?"
investigations.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import asc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.inbox import InboxRoutingRule, MemberGroup
from backend.app.db.models.tenancy import AuditLog, User, WorkspaceMember
from backend.app.db.session import get_session
from backend.app.services.inbox.profiles import (
    INBOX_TYPES,
    ProfileCatalogError,
    load_profile_catalog,
)
from backend.app.services.inbox.routing import (
    RoutingContext,
    RoutingError,
    resolve_handle,
)


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/inbox/routing",
    tags=["inbox-routing"],
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


TargetType = Literal["user", "group", "strategy"]
AssignmentStrategy = Literal["round_robin", "oncall", "first"]

VALID_TARGET_TYPES: frozenset[str] = frozenset({"user", "group", "strategy"})
VALID_ASSIGNMENT_STRATEGIES: frozenset[str] = frozenset(
    {"round_robin", "oncall", "first"}
)

# Profile-catalog meta-keys that aren't inbox types — must be
# filtered out before iterating per-type rules. Mirrors the
# ``_PROFILE_META_KEYS`` constant in ``services.inbox.profiles`` so
# the orphan-check stays catalog-shape-aware.
_PROFILE_META_KEYS: frozenset[str] = frozenset({"inherits"})

# The "silent" profile is intentionally inert (every type disabled);
# its handles are absent and we skip it explicitly to keep the
# orphan-check signal clean.
_SILENT_PROFILE = "silent"


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class RoutingRuleOut(BaseModel):
    """Routing-rule projection for LIST / POST / PATCH responses.

    The DB stores the dispatch target as a single ``target_value``
    string keyed by ``target_type``; the HTTP surface unpacks it
    into the typed convenience fields so callers don't have to
    sniff the column. Exactly one of ``target_user_id`` /
    ``target_group_id`` / ``target_strategy`` is non-NULL,
    matching the row's ``target_type``.
    """

    id: uuid.UUID
    workspace_id: uuid.UUID
    handle: str
    target_type: TargetType
    target_user_id: uuid.UUID | None
    target_group_id: uuid.UUID | None
    target_strategy: str | None
    assignment_strategy: AssignmentStrategy | None
    strategy_config: dict
    is_enabled: bool
    created_at: datetime
    updated_at: datetime


class RoutingRuleDetailOut(RoutingRuleOut):
    """Rule + a just-in-time resolved-target preview.

    The lookup fields (``target_user_email`` / ``target_group_key``
    / ``target_group_name``) are populated by a single follow-up
    query in ``GET /{rule_id}`` so the admin UI can render the
    target chip without a round trip per rule.
    """

    target_user_email: str | None
    target_group_key: str | None
    target_group_name: str | None


class RoutingRuleCreateIn(BaseModel):
    """Payload for ``POST ""``.

    ``handle`` follows the same character class as
    :class:`MemberGroup.key` (``^[a-z][a-z0-9_]*$``) so admins
    can't accidentally introduce a handle that no Play could
    reference. Cross-field validation (target_type ↔ which target_*
    field is set) lives in the route handler — Pydantic's literal
    types can't express the disjunction on their own.
    """

    handle: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    target_type: TargetType
    target_user_id: uuid.UUID | None = None
    target_group_id: uuid.UUID | None = None
    target_strategy: str | None = Field(default=None, max_length=160)
    assignment_strategy: AssignmentStrategy | None = None
    strategy_config: dict = Field(default_factory=dict)
    is_enabled: bool = True


class RoutingRulePatchIn(BaseModel):
    """Partial update for ``PATCH /{rule_id}``.

    Only fields explicitly present in the payload mutate (Pydantic
    ``exclude_unset`` semantics). The route applies the patch onto
    a logical copy of the row and re-runs the full cross-field
    validator on the *final* state — so a PATCH that flips
    ``target_type`` but forgets to clear the now-irrelevant ID
    fields is rejected up front.
    """

    target_type: TargetType | None = None
    target_user_id: uuid.UUID | None = None
    target_group_id: uuid.UUID | None = None
    target_strategy: str | None = Field(default=None, max_length=160)
    assignment_strategy: AssignmentStrategy | None = None
    strategy_config: dict | None = None
    is_enabled: bool | None = None


class RoutingPreviewIn(BaseModel):
    """Input for the dry-run resolver endpoint.

    ``repo_id`` / ``run_id`` / ``source_row`` mirror the
    :class:`services.inbox.routing.RoutingContext` fields the
    resolver consults for built-in handles (``requested_by`` reads
    ``source_row['requested_by_user_id']`` etc.). Admins use this to
    answer "if this handle fires today, who picks it up?" before
    saving a rule.
    """

    handle: str = Field(min_length=1, max_length=64)
    repo_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    source_row: dict = Field(default_factory=dict)


class RoutingPreviewOut(BaseModel):
    """Result of the dry-run resolver call."""

    handle: str
    resolved_user_id: uuid.UUID | None
    resolved_user_email: str | None
    intake_handle: str
    intake_reason: str


class RoutingHandlesOut(BaseModel):
    """Configuration health summary returned by ``GET /handles``."""

    bound_handles: list[str]
    used_handles: list[str]
    orphaned_handles: list[str]
    unbound_handles: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_cross_fields(
    *,
    target_type: str,
    target_user_id: uuid.UUID | None,
    target_group_id: uuid.UUID | None,
    target_strategy: str | None,
    assignment_strategy: str | None,
) -> None:
    """Enforce target_type ↔ target_* / assignment_strategy invariants.

    The schema enforces NOT NULL on ``target_value`` but doesn't
    encode the per-type shape — this helper does. Raises
    :class:`HTTPException` (422) on the first violation so the
    handler can stay flat.
    """
    if target_type == "user":
        if target_user_id is None:
            raise HTTPException(
                status_code=422,
                detail="target_type='user' requires target_user_id",
            )
        if target_group_id is not None or target_strategy is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "target_type='user' must not set target_group_id "
                    "or target_strategy"
                ),
            )
        if assignment_strategy is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "target_type='user' must not set assignment_strategy "
                    "(strategy is only meaningful for groups)"
                ),
            )
    elif target_type == "group":
        if target_group_id is None:
            raise HTTPException(
                status_code=422,
                detail="target_type='group' requires target_group_id",
            )
        if target_user_id is not None or target_strategy is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "target_type='group' must not set target_user_id "
                    "or target_strategy"
                ),
            )
        # assignment_strategy is OPTIONAL for groups — when omitted
        # the resolver applies the group's default ('first').
    elif target_type == "strategy":
        if not target_strategy:
            raise HTTPException(
                status_code=422,
                detail=(
                    "target_type='strategy' requires target_strategy "
                    "(name of a built-in resolver)"
                ),
            )
        if target_user_id is not None or target_group_id is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "target_type='strategy' must not set target_user_id "
                    "or target_group_id"
                ),
            )
        if assignment_strategy is not None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "target_type='strategy' must not set assignment_strategy "
                    "(strategy is only meaningful for groups)"
                ),
            )
    else:  # pragma: no cover — Literal type rejects this upstream.
        raise HTTPException(
            status_code=422,
            detail=f"unknown target_type={target_type!r}",
        )


async def _validate_target_existence(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    target_type: str,
    target_user_id: uuid.UUID | None,
    target_group_id: uuid.UUID | None,
) -> MemberGroup | None:
    """Workspace-scope the user/group reference; return the group if any.

    The schema does not foreign-key ``target_value`` (it's a
    free-form string), so admins must not be able to point a rule
    at a user from another workspace or a non-existent group.
    Returns the resolved :class:`MemberGroup` for ``group`` rules so
    the caller can reuse it without re-querying.
    """
    if target_type == "user":
        assert target_user_id is not None  # validated above
        member_stmt = select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == target_user_id,
        )
        member = (await session.execute(member_stmt)).scalar_one_or_none()
        if member is None:
            raise HTTPException(
                status_code=422,
                detail="user is not a workspace member",
            )
        return None

    if target_type == "group":
        assert target_group_id is not None  # validated above
        group_stmt = select(MemberGroup).where(
            MemberGroup.id == target_group_id,
            MemberGroup.workspace_id == workspace_id,
        )
        group = (await session.execute(group_stmt)).scalar_one_or_none()
        if group is None:
            raise HTTPException(
                status_code=422,
                detail="group does not exist in this workspace",
            )
        return group

    # ``strategy`` carries no DB-side reference; nothing to check.
    return None


def _pack_target_value(
    *,
    target_type: str,
    target_user_id: uuid.UUID | None,
    target_strategy: str | None,
    group: MemberGroup | None,
) -> str:
    """Collapse the typed inputs into the single ``target_value`` column.

    Mirrors the inverse split done by :func:`_unpack_target_value` and
    by ``services.inbox.routing._resolve_via_rule``: groups are
    looked up by ``key`` not by id, so we persist the key.
    """
    if target_type == "user":
        assert target_user_id is not None
        return str(target_user_id)
    if target_type == "group":
        assert group is not None
        return group.key
    if target_type == "strategy":
        assert target_strategy is not None
        return target_strategy
    # Unreachable: cross-field validator rejects unknown types.
    raise HTTPException(
        status_code=422, detail=f"unknown target_type={target_type!r}"
    )


def _unpack_target_value(
    rule: InboxRoutingRule,
    group_key_to_id: dict[str, uuid.UUID],
) -> tuple[uuid.UUID | None, uuid.UUID | None, str | None]:
    """Inverse of :func:`_pack_target_value` for response shaping.

    Returns ``(target_user_id, target_group_id, target_strategy)``;
    exactly one is non-NULL barring schema corruption (a
    ``target_type='user'`` row whose ``target_value`` won't parse as
    a UUID surfaces as all-None — better than 500ing a list call on
    one bad row).
    """
    if rule.target_type == "user":
        try:
            return uuid.UUID(str(rule.target_value)), None, None
        except (ValueError, AttributeError):
            logger.warning(
                "routing rule %s has target_type='user' but "
                "target_value=%r is not a UUID",
                rule.id,
                rule.target_value,
            )
            return None, None, None
    if rule.target_type == "group":
        gid = group_key_to_id.get(rule.target_value)
        return None, gid, None
    if rule.target_type == "strategy":
        return None, None, rule.target_value
    return None, None, None


async def _load_group_key_index(
    session: AsyncSession, workspace_id: uuid.UUID
) -> dict[str, uuid.UUID]:
    """Return ``{group.key: group.id}`` for every group in the workspace.

    Used by LIST + handles to avoid an N+1 lookup when shaping
    rule responses.
    """
    stmt = select(MemberGroup.key, MemberGroup.id).where(
        MemberGroup.workspace_id == workspace_id
    )
    rows = (await session.execute(stmt)).all()
    return {key: gid for key, gid in rows}


def _to_rule_out(
    rule: InboxRoutingRule, group_key_to_id: dict[str, uuid.UUID]
) -> RoutingRuleOut:
    target_user_id, target_group_id, target_strategy = _unpack_target_value(
        rule, group_key_to_id
    )
    return RoutingRuleOut(
        id=rule.id,
        workspace_id=rule.workspace_id,
        handle=rule.handle_key,
        target_type=rule.target_type,  # type: ignore[arg-type]
        target_user_id=target_user_id,
        target_group_id=target_group_id,
        target_strategy=target_strategy,
        assignment_strategy=rule.assignment_strategy,  # type: ignore[arg-type]
        strategy_config=rule.strategy_config or {},
        is_enabled=rule.is_enabled,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


async def _load_rule(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
) -> InboxRoutingRule:
    """Workspace-scoped rule fetch.

    404s on both "no such id" and "id belongs to another workspace"
    so admins in workspace A can never enumerate workspace B's
    rules by id-fishing.
    """
    stmt = select(InboxRoutingRule).where(
        InboxRoutingRule.id == rule_id,
        InboxRoutingRule.workspace_id == workspace_id,
    )
    rule = (await session.execute(stmt)).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="routing rule not found")
    return rule


def _collect_catalog_handles() -> set[str]:
    """Distinct handles referenced by any enabled emit rule in the catalog.

    Walks every profile (skipping the inert ``silent`` one) and
    every inbox type within it. Inheritance chains don't need
    special-casing here: each profile's body is read directly, so a
    handle declared in a parent profile counts as used regardless
    of whether children override it. This is the catalog-only
    first cut per planning §6 — per-pattern overrides on disk are
    explicitly out of scope for P2-05.
    """
    try:
        catalog = load_profile_catalog()
    except ProfileCatalogError as exc:
        logger.warning(
            "profile catalog failed to load; orphan-check returning "
            "no used handles (catalog error: %s)",
            exc,
        )
        return set()

    handles: set[str] = set()
    for profile_name, body in catalog.items():
        if profile_name == _SILENT_PROFILE:
            continue
        if not isinstance(body, dict):
            continue
        for key, rule in body.items():
            if key in _PROFILE_META_KEYS or key not in INBOX_TYPES:
                continue
            if not isinstance(rule, dict):
                continue
            if not rule.get("enabled"):
                continue
            handle = rule.get("handle")
            if isinstance(handle, str) and handle:
                handles.add(handle)
    return handles


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("", response_model=list[RoutingRuleOut])
async def list_routing_rules(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[RoutingRuleOut]:
    """List every routing rule in this workspace.

    RBAC: ``ROLES_READ`` (any workspace member). Ordering matches
    the resolver's tiebreaker (``handle_key``, then ``created_at``)
    so the list view groups rules per handle and shows the older
    one first within a handle. The resolver also filters by
    ``is_enabled=True`` but the admin surface returns disabled rows
    too — operators need to see them to flip the toggle.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    stmt = (
        select(InboxRoutingRule)
        .where(InboxRoutingRule.workspace_id == workspace_id)
        .order_by(
            asc(InboxRoutingRule.handle_key),
            asc(InboxRoutingRule.created_at),
        )
    )
    rules = list((await session.execute(stmt)).scalars().all())
    group_key_to_id = await _load_group_key_index(session, workspace_id)
    return [_to_rule_out(r, group_key_to_id) for r in rules]


@router.get("/handles", response_model=RoutingHandlesOut)
async def list_handles(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RoutingHandlesOut:
    """Configuration-health summary: bound / used / orphaned / unbound.

    RBAC: ``ROLES_READ``. Admin UI uses this to power a "fix your
    routing" panel: orphan rules can be safely deleted; unbound
    handles will fall back to the built-in chain at intake time
    (visible noise but not a failure). The catalog is global to
    every workspace, so ``used_handles`` is workspace-agnostic.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    bound_stmt = (
        select(InboxRoutingRule.handle_key)
        .where(InboxRoutingRule.workspace_id == workspace_id)
        .distinct()
    )
    bound = {h for h in (await session.execute(bound_stmt)).scalars().all() if h}
    used = _collect_catalog_handles()

    return RoutingHandlesOut(
        bound_handles=sorted(bound),
        used_handles=sorted(used),
        orphaned_handles=sorted(bound - used),
        unbound_handles=sorted(used - bound),
    )


@router.post(
    "",
    response_model=RoutingRuleOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_routing_rule(
    workspace_id: uuid.UUID,
    payload: RoutingRuleCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RoutingRuleOut:
    """Create a new routing rule for a handle.

    RBAC: ``ROLES_ADMIN``. Cross-field validation runs first, then
    workspace-scope checks on the user/group reference, then the
    INSERT — duplicate ``(workspace_id, handle)`` surfaces as a
    clean 409 instead of leaking an IntegrityError. The pre-flight
    membership/group lookup keeps the transactional cost of a 422
    minimal (one SELECT, no INSERT).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    _validate_cross_fields(
        target_type=payload.target_type,
        target_user_id=payload.target_user_id,
        target_group_id=payload.target_group_id,
        target_strategy=payload.target_strategy,
        assignment_strategy=payload.assignment_strategy,
    )
    group = await _validate_target_existence(
        session,
        workspace_id,
        target_type=payload.target_type,
        target_user_id=payload.target_user_id,
        target_group_id=payload.target_group_id,
    )

    target_value = _pack_target_value(
        target_type=payload.target_type,
        target_user_id=payload.target_user_id,
        target_strategy=payload.target_strategy,
        group=group,
    )

    rule = InboxRoutingRule(
        workspace_id=workspace_id,
        handle_key=payload.handle,
        target_type=payload.target_type,
        target_value=target_value,
        assignment_strategy=payload.assignment_strategy,
        strategy_config=payload.strategy_config or {},
        is_enabled=payload.is_enabled,
    )
    session.add(rule)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "another rule for this handle already exists in this "
                "workspace; update or delete it first"
            ),
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox_routing.create",
            target_kind="inbox_routing_rule",
            target_id=str(rule.id),
            payload={
                "handle": payload.handle,
                "target_type": payload.target_type,
                "target_value": target_value,
                "assignment_strategy": payload.assignment_strategy,
                "is_enabled": payload.is_enabled,
            },
        )
    )
    await session.flush()
    await session.refresh(rule)
    group_key_to_id = await _load_group_key_index(session, workspace_id)
    return _to_rule_out(rule, group_key_to_id)


@router.get("/{rule_id}", response_model=RoutingRuleDetailOut)
async def get_routing_rule(
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RoutingRuleDetailOut:
    """Routing-rule detail with a just-in-time resolved-target preview.

    RBAC: ``ROLES_READ``. Performs at most one extra SELECT (user
    email or group row) to populate the human-readable target
    fields the Console renders next to the rule. A rule whose
    ``target_value`` no longer points at a real user/group surfaces
    with the lookup fields nulled — the rule itself is still
    visible so admins can repoint or delete it.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    rule = await _load_rule(session, workspace_id, rule_id)
    group_key_to_id = await _load_group_key_index(session, workspace_id)
    base = _to_rule_out(rule, group_key_to_id)

    target_user_email: str | None = None
    target_group_key: str | None = None
    target_group_name: str | None = None

    if rule.target_type == "user" and base.target_user_id is not None:
        user = await session.get(User, base.target_user_id)
        target_user_email = user.email if user is not None else None
    elif rule.target_type == "group":
        group_stmt = select(MemberGroup).where(
            MemberGroup.workspace_id == workspace_id,
            MemberGroup.key == rule.target_value,
        )
        group = (await session.execute(group_stmt)).scalar_one_or_none()
        if group is not None:
            target_group_key = group.key
            target_group_name = group.display_name

    return RoutingRuleDetailOut(
        **base.model_dump(),
        target_user_email=target_user_email,
        target_group_key=target_group_key,
        target_group_name=target_group_name,
    )


@router.patch("/{rule_id}", response_model=RoutingRuleOut)
async def update_routing_rule(
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
    payload: RoutingRulePatchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RoutingRuleOut:
    """Partial update for a routing rule.

    RBAC: ``ROLES_ADMIN``. Only fields explicitly present in the
    payload mutate; the cross-field validator runs on the *final*
    state (current row + patch deltas) so a PATCH that flips
    ``target_type`` without setting the new target_* field is
    rejected before any write. ``handle`` is immutable — admins
    delete + recreate to rename, mirroring the
    :class:`MemberGroup.key` discipline.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    rule = await _load_rule(session, workspace_id, rule_id)

    delta = payload.model_dump(exclude_unset=True)

    # Project the current row into the same logical shape as the
    # input payload so we can re-validate the *post-patch* state.
    current_user_id, current_group_id, current_strategy_name = _unpack_target_value(
        rule, await _load_group_key_index(session, workspace_id)
    )

    next_target_type = delta.get("target_type", rule.target_type)
    # When target_type doesn't change, default each target_* slot to
    # the row's current projection so an admin can patch just one
    # field (e.g. assignment_strategy) without re-stating the rest.
    if "target_type" in delta:
        # Type change wipes implied defaults — admins MUST restate
        # the relevant target_* field for the new type.
        next_user_id = delta.get("target_user_id")
        next_group_id = delta.get("target_group_id")
        next_strategy_name = delta.get("target_strategy")
    else:
        next_user_id = delta.get("target_user_id", current_user_id)
        next_group_id = delta.get("target_group_id", current_group_id)
        next_strategy_name = delta.get("target_strategy", current_strategy_name)

    next_assignment_strategy = delta.get(
        "assignment_strategy", rule.assignment_strategy
    )

    _validate_cross_fields(
        target_type=next_target_type,
        target_user_id=next_user_id,
        target_group_id=next_group_id,
        target_strategy=next_strategy_name,
        assignment_strategy=next_assignment_strategy,
    )
    group = await _validate_target_existence(
        session,
        workspace_id,
        target_type=next_target_type,
        target_user_id=next_user_id,
        target_group_id=next_group_id,
    )
    new_target_value = _pack_target_value(
        target_type=next_target_type,
        target_user_id=next_user_id,
        target_strategy=next_strategy_name,
        group=group,
    )

    changed: dict[str, object] = {}
    if next_target_type != rule.target_type:
        rule.target_type = next_target_type
        changed["target_type"] = next_target_type
    if new_target_value != rule.target_value:
        rule.target_value = new_target_value
        changed["target_value"] = new_target_value
    if next_assignment_strategy != rule.assignment_strategy:
        rule.assignment_strategy = next_assignment_strategy
        changed["assignment_strategy"] = next_assignment_strategy
    if "strategy_config" in delta:
        new_cfg = delta["strategy_config"] or {}
        if new_cfg != (rule.strategy_config or {}):
            rule.strategy_config = new_cfg
            changed["strategy_config"] = new_cfg
    if "is_enabled" in delta and delta["is_enabled"] != rule.is_enabled:
        rule.is_enabled = bool(delta["is_enabled"])
        changed["is_enabled"] = rule.is_enabled

    if changed:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="inbox_routing.update",
                target_kind="inbox_routing_rule",
                target_id=str(rule.id),
                payload=changed,
            )
        )
    await session.flush()
    await session.refresh(rule)
    group_key_to_id = await _load_group_key_index(session, workspace_id)
    return _to_rule_out(rule, group_key_to_id)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_routing_rule(
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Hard-delete a routing rule.

    RBAC: ``ROLES_ADMIN``. The next intake for the now-orphaned
    handle falls through to the built-in chain, so DELETE is a
    soft-reset to defaults rather than a destructive op. The audit
    row preserves the prior target for forensic recovery.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    rule = await _load_rule(session, workspace_id, rule_id)

    snapshot = {
        "handle": rule.handle_key,
        "target_type": rule.target_type,
        "target_value": rule.target_value,
        "assignment_strategy": rule.assignment_strategy,
        "is_enabled": rule.is_enabled,
    }
    await session.delete(rule)
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox_routing.delete",
            target_kind="inbox_routing_rule",
            target_id=str(rule_id),
            payload=snapshot,
        )
    )
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/preview", response_model=RoutingPreviewOut)
async def preview_routing(
    workspace_id: uuid.UUID,
    payload: RoutingPreviewIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RoutingPreviewOut:
    """Dry-run :func:`resolve_handle` without persisting any side effects.

    RBAC: ``ROLES_ADMIN``. ``round_robin`` group dispatch normally
    UPSERTs ``group_assignment_state`` to advance the rotation
    pointer; the preview wraps the resolver call in a SAVEPOINT
    and ROLLBACKs unconditionally so admins can poke "what would
    this do?" without nudging future assignments. The audit log
    still records the preview (no data mutation, but an admin
    trying combinations is interesting forensic context).

    Resolver errors (misconfigured rule that points at a missing
    group, etc.) surface as 422 — preview is a debugging tool, so
    showing the operator the exact failure is more useful than
    catching it.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    ctx = RoutingContext(
        workspace_id=workspace_id,
        repo_id=payload.repo_id,
        run_id=payload.run_id,
        source_row=payload.source_row or {},
    )
    sp = await session.begin_nested()
    try:
        try:
            resolved = await resolve_handle(session, payload.handle, ctx)
        except RoutingError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        # Side-effect free contract: roll back any group_assignment_state
        # writes the resolver might have made on the round_robin path.
        await sp.rollback()

    resolved_email: str | None = None
    if resolved.user_id is not None:
        user = await session.get(User, resolved.user_id)
        resolved_email = user.email if user is not None else None

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox_routing.preview",
            target_kind="inbox_routing_rule",
            target_id=None,
            payload={
                "handle": payload.handle,
                "resolved_user_id": (
                    str(resolved.user_id) if resolved.user_id else None
                ),
                "intake_handle": resolved.intake_handle,
                "intake_reason": resolved.intake_reason,
            },
        )
    )
    await session.flush()
    return RoutingPreviewOut(
        handle=payload.handle,
        resolved_user_id=resolved.user_id,
        resolved_user_email=resolved_email,
        intake_handle=resolved.intake_handle,
        intake_reason=resolved.intake_reason,
    )


__all__ = ["router"]
