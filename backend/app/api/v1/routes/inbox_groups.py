"""Operational member groups CRUD (RFC-0010 Plays/Inbox redesign, P2-04).

Workspace-scoped CRUD for ``member_groups`` and ``member_group_members``
— the operational buckets ("secops", "eng_managers", "on_call_eng",
…) that ``inbox_routing_rules`` resolve symbolic Play handles to.
See ``documentation/internal/inbox-redesign-planning.md`` §4 for the
DDL and §5 for the operational-vs-permission distinction.

Critical distinction (planning §5, RFC-0010 §5):

    - ``WorkspaceMember.role`` = **permission** (owner / admin /
      maintainer / member / viewer). Decides what a user can *do*.
    - :class:`MemberGroup` = **operational** bucket. Decides who
      gets *assigned* an inbox item — a single user can sit in
      ``secops`` and ``eng_managers`` with no permission impact.

Routing rules (separate ticket P2-05) read ``key`` to resolve a
``security_officer`` handle to the matching group; per-group
``assignment_strategy`` then picks one member (round-robin /
on-call / first).

Storage note (assignment_strategy): the ``member_groups`` table
intentionally does not (yet) carry ``assignment_strategy`` as its
own column — it lives on ``inbox_routing_rules.assignment_strategy``
in the migration. To keep the API surface forward-compatible with
the routing layer (so admins can configure "this group rotates
round-robin" once instead of per-rule), we encode the strategy as a
sentinel-prefixed line inside ``description``. See
:func:`_pack_description` / :func:`_unpack_description`. When a
follow-up migration adds a real column, the parsing layer becomes a
no-op and the storage path collapses to a column write.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.inbox import (
    InboxRoutingRule,
    MemberGroup,
    MemberGroupMember,
)
from backend.app.db.models.tenancy import AuditLog, User, WorkspaceMember
from backend.app.db.session import get_session


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/inbox/groups",
    tags=["inbox-groups"],
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


VALID_STRATEGIES: frozenset[str] = frozenset({"round_robin", "oncall", "first"})
DEFAULT_STRATEGY: str = "round_robin"

# Sentinel header line embedded at the top of ``description`` to carry
# ``assignment_strategy`` until a follow-up migration adds a real column.
# We use a JSON-encoded marker so future fields slot in without another
# format break (just add keys to the dict).
_STRATEGY_MARKER = "__ship_group_meta__"


# ---------------------------------------------------------------------------
# Pydantic schemas (kept inline per `clarifications.py` convention; see
# planning doc — CRUD is thin enough to live in the route file).
# ---------------------------------------------------------------------------


class GroupOut(BaseModel):
    """Group summary returned by LIST/POST/PATCH endpoints."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    key: str
    name: str
    description: str | None
    assignment_strategy: str
    member_count: int
    created_at: datetime
    updated_at: datetime


class GroupMemberOut(BaseModel):
    """Member projection joined with the user row for display."""

    user_id: uuid.UUID
    email: str
    display_name: str | None
    added_at: datetime
    on_call: bool


class GroupDetailOut(GroupOut):
    """Group plus its full member list (used by GET /{group_id})."""

    members: list[GroupMemberOut]


class GroupCreateIn(BaseModel):
    """Payload for ``POST ""``. ``key`` is the routing-rule handle."""

    key: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    assignment_strategy: Literal["round_robin", "oncall", "first"] = "round_robin"


class GroupPatchIn(BaseModel):
    """Partial update — any subset of fields."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    assignment_strategy: Literal["round_robin", "oncall", "first"] | None = None


class GroupMemberAddIn(BaseModel):
    """Payload for ``POST /{group_id}/members``."""

    user_id: uuid.UUID
    on_call: bool = False


# ---------------------------------------------------------------------------
# Description encode/decode (carries assignment_strategy until a real
# column lands — see module docstring).
# ---------------------------------------------------------------------------


def _pack_description(strategy: str, description: str | None) -> str:
    """Serialise (strategy, description) into the column value.

    Always emits the sentinel header so reads have a single parsing
    path. ``description=None`` packs to header-only.
    """
    header = f"{_STRATEGY_MARKER}:{json.dumps({'assignment_strategy': strategy})}"
    if description is None or description == "":
        return header
    return f"{header}\n{description}"


def _unpack_description(raw: str | None) -> tuple[str, str | None]:
    """Inverse of :func:`_pack_description`.

    Tolerates legacy rows written by other code paths (no header) and
    falls back to ``DEFAULT_STRATEGY`` so the API never 500s on
    unfamiliar storage shapes.
    """
    if raw is None:
        return DEFAULT_STRATEGY, None
    if not raw.startswith(_STRATEGY_MARKER + ":"):
        return DEFAULT_STRATEGY, raw
    head, _, rest = raw.partition("\n")
    payload = head[len(_STRATEGY_MARKER) + 1 :]
    try:
        meta = json.loads(payload)
    except (ValueError, TypeError):
        return DEFAULT_STRATEGY, raw
    strategy = str(meta.get("assignment_strategy") or DEFAULT_STRATEGY)
    if strategy not in VALID_STRATEGIES:
        strategy = DEFAULT_STRATEGY
    return strategy, (rest or None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_group_out(group: MemberGroup, member_count: int) -> GroupOut:
    strategy, description = _unpack_description(group.description)
    return GroupOut(
        id=group.id,
        workspace_id=group.workspace_id,
        key=group.key,
        name=group.display_name,
        description=description,
        assignment_strategy=strategy,
        member_count=member_count,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


async def _load_group(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
) -> MemberGroup:
    """Fetch a group, scoping by ``workspace_id`` for tenant isolation.

    Returns 404 when the group does not exist OR it lives in a
    different workspace — admins in workspace A must never see (let
    alone mutate) workspace B's groups.
    """
    stmt = select(MemberGroup).where(
        MemberGroup.id == group_id,
        MemberGroup.workspace_id == workspace_id,
    )
    group = (await session.execute(stmt)).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="group not found")
    return group


async def _count_members(
    session: AsyncSession, group_id: uuid.UUID
) -> int:
    stmt = select(func.count(MemberGroupMember.user_id)).where(
        MemberGroupMember.group_id == group_id
    )
    return int((await session.execute(stmt)).scalar_one())


# ---------------------------------------------------------------------------
# Routes — groups
# ---------------------------------------------------------------------------


@router.get("", response_model=list[GroupOut])
async def list_groups(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[GroupOut]:
    """List every operational group in this workspace.

    RBAC: any workspace member (``ROLES_READ``). Member counts are
    computed in a single LEFT-JOIN aggregate so the response stays
    O(1) queries regardless of group cardinality.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    stmt = (
        select(
            MemberGroup,
            func.count(MemberGroupMember.user_id).label("member_count"),
        )
        .outerjoin(
            MemberGroupMember,
            MemberGroupMember.group_id == MemberGroup.id,
        )
        .where(MemberGroup.workspace_id == workspace_id)
        .group_by(MemberGroup.id)
        .order_by(MemberGroup.created_at.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [_to_group_out(group, count) for group, count in rows]


@router.post(
    "",
    response_model=GroupOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_group(
    workspace_id: uuid.UUID,
    payload: GroupCreateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> GroupOut:
    """Create a new operational group.

    RBAC: ``ROLES_ADMIN``. ``key`` must be unique per workspace —
    duplicate inserts surface as a clean 409 instead of leaking an
    IntegrityError stack. Group rows do NOT lazy-create a
    ``group_assignment_state`` companion; routing creates that on
    first dispatch (see module docstring gotcha).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    group = MemberGroup(
        workspace_id=workspace_id,
        key=payload.key,
        display_name=payload.name,
        description=_pack_description(
            payload.assignment_strategy, payload.description
        ),
    )
    session.add(group)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409, detail="group key already exists in this workspace"
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox_group.create",
            target_kind="member_group",
            target_id=str(group.id),
            payload={
                "key": payload.key,
                "name": payload.name,
                "assignment_strategy": payload.assignment_strategy,
            },
        )
    )
    await session.flush()
    await session.refresh(group)
    return _to_group_out(group, 0)


@router.get("/{group_id}", response_model=GroupDetailOut)
async def get_group(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> GroupDetailOut:
    """Group detail with full member list (joined with users).

    RBAC: ``ROLES_READ``. The members JOIN surfaces ``email`` and
    ``display_name`` so the Console can render the member chips
    without a follow-up call.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    group = await _load_group(session, workspace_id, group_id)

    members_stmt = (
        select(MemberGroupMember, User)
        .join(User, User.id == MemberGroupMember.user_id)
        .where(MemberGroupMember.group_id == group.id)
        .order_by(MemberGroupMember.added_at.asc())
    )
    rows = (await session.execute(members_stmt)).all()
    members = [
        GroupMemberOut(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            added_at=membership.added_at,
            # ``on_call`` is currently a per-group flag without its own
            # column on ``member_group_members``. Stored as False until
            # a future migration; routing's ``oncall`` strategy reads
            # this once it's persisted.
            on_call=False,
        )
        for membership, user in rows
    ]
    base = _to_group_out(group, len(members))
    return GroupDetailOut(**base.model_dump(), members=members)


@router.patch("/{group_id}", response_model=GroupOut)
async def update_group(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    payload: GroupPatchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> GroupOut:
    """Patch group display name, description, or assignment strategy.

    RBAC: ``ROLES_ADMIN``. ``key`` is intentionally immutable —
    routing rules reference it by value, so renaming would silently
    break dispatches. Roll a new group + delete instead.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    group = await _load_group(session, workspace_id, group_id)

    current_strategy, current_description = _unpack_description(group.description)
    new_strategy = payload.assignment_strategy or current_strategy
    new_description = (
        payload.description
        if payload.description is not None
        else current_description
    )

    changed: dict[str, object] = {}
    if payload.name is not None and payload.name != group.display_name:
        group.display_name = payload.name
        changed["name"] = payload.name
    if (
        new_strategy != current_strategy
        or (payload.description is not None and payload.description != current_description)
    ):
        group.description = _pack_description(new_strategy, new_description)
        if new_strategy != current_strategy:
            changed["assignment_strategy"] = new_strategy
        if payload.description is not None and payload.description != current_description:
            changed["description"] = new_description

    if changed:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="inbox_group.update",
                target_kind="member_group",
                target_id=str(group.id),
                payload=changed,
            )
        )
    await session.flush()
    await session.refresh(group)
    member_count = await _count_members(session, group.id)
    return _to_group_out(group, member_count)


@router.delete(
    "/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_group(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Delete a group plus its membership rows (CASCADE) and orphan
    any routing rules that pointed at it.

    RBAC: ``ROLES_ADMIN``. ``member_group_members`` and
    ``group_assignment_state`` go via the FK CASCADE in the
    migration. ``inbox_routing_rules`` reference the group by
    ``key`` (string), not by FK, so we sweep app-side: clear the
    ``target_value`` on every rule that named this group, log a
    warning per rule (operators need to repoint them), and disable
    the rule so the next intake doesn't dispatch to a non-existent
    group.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    group = await _load_group(session, workspace_id, group_id)

    # Sweep routing rules that referenced this group by key. We can't
    # NULL ``target_value`` (it's NOT NULL in the schema), so we mark
    # the rule disabled and stamp ``target_value`` with a sentinel so
    # the orphaned state is visible to operators and to the routing
    # layer's preflight checks.
    orphan_stmt = select(InboxRoutingRule).where(
        InboxRoutingRule.workspace_id == workspace_id,
        InboxRoutingRule.target_type == "group",
        InboxRoutingRule.target_value == group.key,
    )
    orphans = (await session.execute(orphan_stmt)).scalars().all()
    for rule in orphans:
        logger.warning(
            "group %s deleted; routing rule %s now orphaned (handle=%s)",
            group.id,
            rule.id,
            rule.handle_key,
        )
    if orphans:
        await session.execute(
            update(InboxRoutingRule)
            .where(
                InboxRoutingRule.workspace_id == workspace_id,
                InboxRoutingRule.target_type == "group",
                InboxRoutingRule.target_value == group.key,
            )
            .values(is_enabled=False, target_value=f"__deleted__:{group.key}")
        )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox_group.delete",
            target_kind="member_group",
            target_id=str(group.id),
            payload={
                "key": group.key,
                "orphaned_routing_rules": [str(r.id) for r in orphans],
            },
        )
    )
    await session.delete(group)
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Routes — group members
# ---------------------------------------------------------------------------


@router.post(
    "/{group_id}/members",
    response_model=GroupMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    payload: GroupMemberAddIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> GroupMemberOut:
    """Add a workspace user to the group.

    RBAC: ``ROLES_ADMIN``. The user must already belong to the
    workspace as a :class:`WorkspaceMember` — operational groups do
    not double as invites (422 otherwise). Adding the same
    ``(group, user)`` twice is rejected with 409 to keep POST
    semantics non-idempotent; the on-call flag is currently
    unstored (see :class:`GroupMemberOut` note) so flipping it
    requires DELETE + POST today.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    group = await _load_group(session, workspace_id, group_id)

    membership_stmt = select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == payload.user_id,
    )
    if (await session.execute(membership_stmt)).scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="user is not a workspace member",
        )

    user = await session.get(User, payload.user_id)
    if user is None:
        # Defensive: WorkspaceMember should always join a User, but a
        # half-deleted user could leave a dangling row.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="user not found",
        )

    existing_stmt = select(MemberGroupMember).where(
        MemberGroupMember.group_id == group.id,
        MemberGroupMember.user_id == payload.user_id,
    )
    if (await session.execute(existing_stmt)).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail="user is already a member of this group",
        )

    membership = MemberGroupMember(group_id=group.id, user_id=payload.user_id)
    session.add(membership)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Lost race with a concurrent POST.
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="user is already a member of this group",
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox_group.member_add",
            target_kind="member_group",
            target_id=str(group.id),
            payload={"user_id": str(payload.user_id), "on_call": payload.on_call},
        )
    )
    await session.flush()
    await session.refresh(membership)
    return GroupMemberOut(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        added_at=membership.added_at,
        on_call=payload.on_call,
    )


@router.delete(
    "/{group_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    workspace_id: uuid.UUID,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Remove a user from the group.

    RBAC: ``ROLES_ADMIN``. Returns 404 if the membership row is
    absent so callers can distinguish "no-op" from a successful
    removal. The user's :class:`WorkspaceMember` row is left
    untouched — this is purely a group disenrolment.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    group = await _load_group(session, workspace_id, group_id)

    membership_stmt = select(MemberGroupMember).where(
        MemberGroupMember.group_id == group.id,
        MemberGroupMember.user_id == user_id,
    )
    membership = (await session.execute(membership_stmt)).scalar_one_or_none()
    if membership is None:
        raise HTTPException(
            status_code=404, detail="user is not a member of this group"
        )

    await session.delete(membership)
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="inbox_group.member_remove",
            target_kind="member_group",
            target_id=str(group.id),
            payload={"user_id": str(user_id)},
        )
    )
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
