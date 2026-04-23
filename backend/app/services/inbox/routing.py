"""Workspace routing resolver — handle → owner ``user_id`` (RFC-0010 P2-06).

Given a symbolic handle declared by a Play (``secops``,
``repo_maintainer``, ``requested_by``…) and the runtime context the
inbox item is being created in (workspace, repo, run, source row),
this service returns the concrete user the item should be assigned
to. The single-owner discipline of Inbox v1 (RFC-0010 §"Single-owner
discipline" / planning §5) means **every** intake call must end with
exactly one assigned ``user_id`` *or* an explicit ``unresolved``
marker — never a list, never silent failure.

Resolution order (planning §6 "Resolver order at intake"):

1. **Workspace routing rule** in ``inbox_routing_rules`` for
   ``(workspace_id, handle)``. Rule kinds: ``user`` (pin), ``group``
   (apply ``assignment_strategy``), ``strategy`` (invoke a built-in
   resolver named by ``target_value``).
2. **Built-in handle defaults** for well-known handles
   (``requested_by``, ``assignee_of_run``, ``repo_maintainer``,
   ``code_owner``) when no workspace rule has been configured.
3. **Mandatory fallback chain** — defaults to
   ``workspace_admin → workspace_owner`` so an item never silently
   loses its owner because the routing config drifted.
4. **``unresolved``** — return ``ResolvedTarget(user_id=None, ...)``;
   the intake service still creates the item, flagged for an admin
   to fix the routing config (RFC-0010 §"Mandatory fallback").

The service is async, side-effect-free except for the ``round_robin``
state-row UPSERT (which lives inside the caller's transaction so
``SELECT … FOR UPDATE`` serialises concurrent picks; planning §7 R3).
No I/O outside the passed ``session``; no profile/catalog imports
(intake → routing, never the other way around).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.inbox import (
    GroupAssignmentState,
    InboxRoutingRule,
    MemberGroup,
    MemberGroupMember,
)
from backend.app.db.models.pipelines import PipelineRun
from backend.app.db.models.tenancy import User, WorkspaceMember

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutingContext:
    """Inputs the resolver needs to dereference a symbolic handle.

    ``source_row`` is an arbitrary dict captured from the legacy row
    being upgraded into an inbox item — used to resolve handles like
    ``requested_by`` that point at a column on the source row.
    """

    workspace_id: uuid.UUID
    repo_id: uuid.UUID | None
    run_id: uuid.UUID | None
    source_row: dict


@dataclass(frozen=True)
class ResolvedTarget:
    """Result of handle resolution.

    ``user_id`` is the assigned owner (``None`` when no resolution
    plus no fallback). ``group_id`` is non-``None`` when the handle
    resolved through an operational group (used by the audit trail
    and round-robin state update). ``intake_handle`` echoes the
    handle that resolved (so the inbox item can record which one
    was used — useful when fallback chains fire).
    ``intake_reason`` is a short human-readable explanation:
    ``rule:user`` / ``group:secops:round_robin`` /
    ``builtin:requested_by`` / ``fallback:workspace_owner`` /
    ``unresolved``.
    """

    user_id: uuid.UUID | None
    group_id: uuid.UUID | None
    intake_handle: str
    intake_reason: str


# ---------------------------------------------------------------------------
# Custom errors
# ---------------------------------------------------------------------------


class RoutingError(RuntimeError):
    """Raised on routing config that violates the schema invariants.

    Most resolution failures should produce
    ``ResolvedTarget(user_id=None, intake_reason='unresolved')``
    rather than raise. ``RoutingError`` is reserved for situations
    we should never hit at runtime — e.g. an
    ``inbox_routing_rules`` row with ``target_type='group'`` whose
    ``target_value`` doesn't match any ``member_groups.key`` in the
    workspace, a ``target_type='user'`` row with a ``target_value``
    that won't parse as a UUID, or a malformed
    ``assignment_strategy``.
    """


# ---------------------------------------------------------------------------
# Built-in handle / strategy / fallback names
# ---------------------------------------------------------------------------


# Handles whose default resolution is a built-in strategy when no
# workspace routing rule overrides them. The same names also serve
# as `target_value` for ``target_type='strategy'`` rules.
_BUILTIN_HANDLES: frozenset[str] = frozenset(
    {
        "requested_by",
        "assignee_of_run",
        "repo_maintainer",
        "code_owner",
    }
)

# Permission roles considered "maintainer-grade" for the
# ``repo_maintainer`` built-in. Order is irrelevant; ``WorkspaceMember``
# rows are filtered with ``role IN (…)`` and then ordered by
# ``created_at`` for determinism.
_REPO_MAINTAINER_ROLES: tuple[str, ...] = ("owner", "admin", "maintainer")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def resolve_handle(
    session: AsyncSession,
    handle: str,
    ctx: RoutingContext,
    *,
    fallback_chain: tuple[str, ...] = ("workspace_admin", "workspace_owner"),
) -> ResolvedTarget:
    """Resolve a symbolic handle into a concrete owner.

    Algorithm (planning §6 "Resolver order at intake"; RFC-0010
    §"Routing model"):

    1. **Workspace routing rule.** Look up ``inbox_routing_rules``
       for ``(workspace_id, handle)`` filtered by ``is_enabled=True``.
       The schema enforces ``UNIQUE(workspace_id, handle_key)`` so at
       most one row matches; ``ORDER BY created_at ASC`` is applied
       defensively (and is the documented tiebreaker if the unique
       constraint is ever relaxed). The rule kinds:

       - ``target_type='user'`` → parse ``target_value`` as a UUID
         and return it (``intake_reason='rule:user'``).
       - ``target_type='group'`` → look up the
         ``member_groups`` row by ``(workspace_id, key=target_value)``
         and delegate to :func:`_pick_from_group` with the rule's
         ``assignment_strategy`` and ``strategy_config``
         (``intake_reason='group:<key>:<strategy>'``).
       - ``target_type='strategy'`` → invoke the built-in named by
         ``target_value`` (``intake_reason='rule:strategy:<name>'``).

       A misconfigured rule (group ``target_value`` that doesn't
       resolve to any group; non-UUID user ``target_value``) raises
       :class:`RoutingError` because that is a config integrity
       violation, not a resolution failure.

    2. **Built-in defaults for well-known handles.** If no rule
       matched and the handle is one of:

       - ``requested_by``     → ``ctx.source_row['requested_by_user_id']``
       - ``assignee_of_run``  → ``PipelineRun.payload['triggered_by_user_id']``
         for ``ctx.run_id``
       - ``repo_maintainer``  → first
         ``WorkspaceMember(role IN ('owner','admin','maintainer'))``
         in ``ctx.workspace_id`` ordered by ``created_at``
       - ``code_owner``       → first ``ctx.source_row['codeowners']``
         entry that maps to a workspace user (``email`` or ``id``)

       …return that user with ``intake_reason='builtin:<handle>'``.
       Other handles fall through to step 3 with no built-in match.

    3. **Fallback chain.** Walk ``fallback_chain`` in order
       (default ``workspace_admin → workspace_owner``):

       - ``workspace_admin`` → first ``WorkspaceMember(role='admin')``
         in ``ctx.workspace_id`` by ``created_at``
       - ``workspace_owner`` → first ``WorkspaceMember(role='owner')``
         in ``ctx.workspace_id`` by ``created_at``

       The first that resolves wins (``intake_reason='fallback:<name>'``);
       a ``DEBUG`` log is emitted when the fallback fires.

    4. **Unresolved.** If everything fails, return
       ``ResolvedTarget(user_id=None, group_id=None,
       intake_handle=handle, intake_reason='unresolved')`` and emit
       a ``WARNING`` log. Per RFC-0010 §"Mandatory fallback" the
       intake service still creates the item but flags it so an
       admin can fix the routing config.
    """
    rule_target = await _resolve_via_rule(session, handle, ctx)
    if rule_target is not None:
        return rule_target

    builtin_user_id = await _builtin_strategy(session, handle, ctx)
    if builtin_user_id is not None:
        return ResolvedTarget(
            user_id=builtin_user_id,
            group_id=None,
            intake_handle=handle,
            intake_reason=f"builtin:{handle}",
        )

    fallback_user_id, fallback_reason = await _walk_fallback(
        session, fallback_chain, ctx
    )
    if fallback_user_id is not None:
        logger.debug(
            "Routing fallback fired for handle=%r workspace=%s reason=%s",
            handle,
            ctx.workspace_id,
            fallback_reason,
        )
        return ResolvedTarget(
            user_id=fallback_user_id,
            group_id=None,
            intake_handle=handle,
            intake_reason=fallback_reason,
        )

    logger.warning(
        "Unresolved routing handle=%r workspace=%s — "
        "intake will create item with no owner",
        handle,
        ctx.workspace_id,
    )
    return ResolvedTarget(
        user_id=None,
        group_id=None,
        intake_handle=handle,
        intake_reason="unresolved",
    )


async def resolve_chain(
    session: AsyncSession,
    handles: list[str],
    ctx: RoutingContext,
    *,
    fallback_chain: tuple[str, ...] = ("workspace_admin", "workspace_owner"),
) -> ResolvedTarget:
    """Try each handle in order; the first that resolves wins.

    Used when a profile's emit rule lists multiple candidate handles
    (rare today; the schema allows it via comma-separated handles).
    Each handle is run through steps 1+2 of :func:`resolve_handle`
    only — the fallback chain is walked **once** at the end so the
    first handle's fallback doesn't mask later candidates.

    If ``handles`` is empty, returns the unresolved sentinel with
    ``intake_handle=''``.
    """
    if not handles:
        return ResolvedTarget(
            user_id=None,
            group_id=None,
            intake_handle="",
            intake_reason="unresolved",
        )

    last_handle = handles[-1]
    for handle in handles:
        rule_target = await _resolve_via_rule(session, handle, ctx)
        if rule_target is not None:
            return rule_target
        builtin_user_id = await _builtin_strategy(session, handle, ctx)
        if builtin_user_id is not None:
            return ResolvedTarget(
                user_id=builtin_user_id,
                group_id=None,
                intake_handle=handle,
                intake_reason=f"builtin:{handle}",
            )

    fallback_user_id, fallback_reason = await _walk_fallback(
        session, fallback_chain, ctx
    )
    if fallback_user_id is not None:
        logger.debug(
            "Routing chain fallback fired handles=%r workspace=%s reason=%s",
            handles,
            ctx.workspace_id,
            fallback_reason,
        )
        return ResolvedTarget(
            user_id=fallback_user_id,
            group_id=None,
            intake_handle=last_handle,
            intake_reason=fallback_reason,
        )

    logger.warning(
        "Unresolved routing chain handles=%r workspace=%s",
        handles,
        ctx.workspace_id,
    )
    return ResolvedTarget(
        user_id=None,
        group_id=None,
        intake_handle=last_handle,
        intake_reason="unresolved",
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _resolve_via_rule(
    session: AsyncSession,
    handle: str,
    ctx: RoutingContext,
) -> ResolvedTarget | None:
    """Look up an enabled routing rule for ``(workspace, handle)``.

    Returns ``None`` when no rule matches so the caller can fall
    through to built-ins. Raises :class:`RoutingError` on rules that
    violate the schema invariants (unparseable user UUID, group key
    pointing at no group, etc.).
    """
    stmt = (
        select(InboxRoutingRule)
        .where(
            InboxRoutingRule.workspace_id == ctx.workspace_id,
            InboxRoutingRule.handle_key == handle,
            InboxRoutingRule.is_enabled.is_(True),
        )
        .order_by(asc(InboxRoutingRule.created_at))
        .limit(1)
    )
    rule = (await session.execute(stmt)).scalar_one_or_none()
    if rule is None:
        return None

    if rule.target_type == "user":
        try:
            user_id = uuid.UUID(str(rule.target_value))
        except (ValueError, AttributeError) as exc:
            raise RoutingError(
                f"routing rule {rule.id} has target_type='user' but "
                f"target_value={rule.target_value!r} is not a UUID"
            ) from exc
        return ResolvedTarget(
            user_id=user_id,
            group_id=None,
            intake_handle=handle,
            intake_reason="rule:user",
        )

    if rule.target_type == "group":
        group_stmt = select(MemberGroup).where(
            MemberGroup.workspace_id == ctx.workspace_id,
            MemberGroup.key == rule.target_value,
        )
        group = (await session.execute(group_stmt)).scalar_one_or_none()
        if group is None:
            raise RoutingError(
                f"routing rule {rule.id} points at group key "
                f"{rule.target_value!r} which does not exist in "
                f"workspace {ctx.workspace_id}"
            )
        strategy = rule.assignment_strategy or "first"
        picked = await _pick_from_group(
            session,
            group,
            strategy=strategy,
            strategy_config=rule.strategy_config or {},
        )
        if picked is None:
            # Empty group → fall through so the fallback chain or
            # higher-level resolve_chain candidate gets a shot.
            return None
        return ResolvedTarget(
            user_id=picked,
            group_id=group.id,
            intake_handle=handle,
            intake_reason=f"group:{group.key}:{strategy}",
        )

    if rule.target_type == "strategy":
        strategy_name = rule.target_value
        # Built-in strategies are keyed by their handle name in this
        # service; any handle registered in _BUILTIN_HANDLES is a
        # valid strategy target. Unknown strategies fall through so
        # an admin's typo doesn't block intake on a fallback path.
        picked = await _builtin_strategy(session, strategy_name, ctx)
        if picked is None:
            return None
        return ResolvedTarget(
            user_id=picked,
            group_id=None,
            intake_handle=handle,
            intake_reason=f"rule:strategy:{strategy_name}",
        )

    raise RoutingError(
        f"routing rule {rule.id} has unknown target_type="
        f"{rule.target_type!r} (expected user|group|strategy)"
    )


async def _pick_from_group(
    session: AsyncSession,
    group: MemberGroup,
    *,
    strategy: str,
    strategy_config: dict | None = None,
) -> uuid.UUID | None:
    """Apply an assignment strategy to a group and return the picked user.

    Strategies:

    - ``first`` — ``MIN(added_at)`` member; deterministic.
    - ``oncall`` — ``v1`` stub. The set of currently on-call user
      ids comes from ``strategy_config['oncall_user_ids']`` (the
      schema doesn't carry per-member ``on_call`` flags). The
      first group member in that set wins; if none match — or
      ``oncall_user_ids`` is unset — falls back to ``first``
      (planning §1: "Group owner picker default = round_robin;
      oncall stubbed for v1, falls through if no schedule").
    - ``round_robin`` — read
      ``group_assignment_state.last_assigned_user_id``, compute the
      next member by ``added_at`` order, UPSERT the new state row.
      Lazy-creates the state row on first use.

    **Concurrency contract.** For ``round_robin`` the state row is
    selected with ``SELECT … FOR UPDATE`` so two concurrent intake
    transactions targeting the same group serialise on the row lock
    instead of racing for the same member (planning §7 R3). The
    SELECT FOR UPDATE is a no-op outside Postgres; in-process tests
    should run against the migrated Postgres test DB.

    Returns ``None`` if the group has no members.
    """
    members_stmt = (
        select(MemberGroupMember)
        .where(MemberGroupMember.group_id == group.id)
        .order_by(asc(MemberGroupMember.added_at))
    )
    members = list((await session.execute(members_stmt)).scalars().all())
    if not members:
        return None

    if strategy == "first":
        return members[0].user_id

    if strategy == "oncall":
        config = strategy_config or {}
        oncall_raw = config.get("oncall_user_ids") or []
        oncall_ids: set[uuid.UUID] = set()
        for raw in oncall_raw:
            try:
                oncall_ids.add(uuid.UUID(str(raw)))
            except (ValueError, AttributeError):
                continue
        for member in members:
            if member.user_id in oncall_ids:
                return member.user_id
        # No configured on-call users in the group — stub fallback to first.
        return members[0].user_id

    if strategy == "round_robin":
        state_stmt = (
            select(GroupAssignmentState)
            .where(GroupAssignmentState.group_id == group.id)
            .with_for_update()
        )
        state = (await session.execute(state_stmt)).scalar_one_or_none()
        if state is None:
            state = GroupAssignmentState(
                group_id=group.id, last_assigned_user_id=None
            )
            session.add(state)
            # Flush so the row exists for any sibling transaction that
            # acquires the FOR UPDATE lock on the next call.
            await session.flush()

        last = state.last_assigned_user_id
        if last is None:
            picked = members[0].user_id
        else:
            ids = [m.user_id for m in members]
            try:
                idx = ids.index(last)
            except ValueError:
                # Last-assigned user has left the group; restart at head.
                picked = members[0].user_id
            else:
                picked = ids[(idx + 1) % len(ids)]

        state.last_assigned_user_id = picked
        await session.flush()
        return picked

    raise RoutingError(
        f"unknown assignment_strategy={strategy!r} for group "
        f"{group.key!r} (expected first|oncall|round_robin)"
    )


async def _builtin_strategy(
    session: AsyncSession,
    handle: str,
    ctx: RoutingContext,
) -> uuid.UUID | None:
    """Resolve one of the built-in handles using ``ctx``.

    Returns ``None`` for handles that have no built-in resolver, so
    callers can transparently fall through to the fallback chain.
    """
    if handle == "requested_by":
        raw = ctx.source_row.get("requested_by_user_id")
        return _coerce_uuid(raw)

    if handle == "assignee_of_run":
        if ctx.run_id is None:
            return None
        run_stmt = select(PipelineRun.payload).where(
            PipelineRun.id == ctx.run_id
        )
        payload = (await session.execute(run_stmt)).scalar_one_or_none()
        if not isinstance(payload, dict):
            return None
        return _coerce_uuid(payload.get("triggered_by_user_id"))

    if handle == "repo_maintainer":
        member_stmt = (
            select(WorkspaceMember.user_id)
            .where(
                WorkspaceMember.workspace_id == ctx.workspace_id,
                WorkspaceMember.role.in_(_REPO_MAINTAINER_ROLES),
            )
            .order_by(asc(WorkspaceMember.created_at))
            .limit(1)
        )
        return (await session.execute(member_stmt)).scalar_one_or_none()

    if handle == "code_owner":
        codeowners = ctx.source_row.get("codeowners") or []
        if not isinstance(codeowners, (list, tuple)):
            return None
        for raw in codeowners:
            picked = await _match_workspace_user(
                session, ctx.workspace_id, raw
            )
            if picked is not None:
                return picked
        return None

    return None


async def _match_workspace_user(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    raw: object,
) -> uuid.UUID | None:
    """Match a free-form codeowner identifier to a workspace user.

    Accepts either a UUID (matched against ``users.id`` filtered to
    members of the workspace) or a string (matched against
    ``users.email``, also workspace-scoped). Returns ``None`` when
    nothing matches.
    """
    candidate_uuid = _coerce_uuid(raw)
    if candidate_uuid is not None:
        member_stmt = select(WorkspaceMember.user_id).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == candidate_uuid,
        )
        return (await session.execute(member_stmt)).scalar_one_or_none()

    if isinstance(raw, str) and raw:
        email_stmt = (
            select(User.id)
            .join(
                WorkspaceMember,
                WorkspaceMember.user_id == User.id,
            )
            .where(
                WorkspaceMember.workspace_id == workspace_id,
                User.email == raw,
            )
            .limit(1)
        )
        return (await session.execute(email_stmt)).scalar_one_or_none()

    return None


async def _walk_fallback(
    session: AsyncSession,
    chain: tuple[str, ...],
    ctx: RoutingContext,
) -> tuple[uuid.UUID | None, str]:
    """Walk the fallback chain; return ``(user_id, intake_reason)``.

    ``intake_reason`` is ``'fallback:<name>'`` on hit and the empty
    string on miss (caller treats that as "unresolved"). Unknown
    chain entries are skipped with a ``WARNING`` so an operator
    typo doesn't take down intake.
    """
    for name in chain:
        if name == "workspace_admin":
            picked = await _first_member_with_role(
                session, ctx.workspace_id, "admin"
            )
        elif name == "workspace_owner":
            picked = await _first_member_with_role(
                session, ctx.workspace_id, "owner"
            )
        else:
            logger.warning(
                "Unknown fallback handler %r in chain %r — skipping",
                name,
                chain,
            )
            continue
        if picked is not None:
            return picked, f"fallback:{name}"
    return None, ""


async def _first_member_with_role(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    role: str,
) -> uuid.UUID | None:
    """Return the earliest-joined ``WorkspaceMember`` with ``role``."""
    stmt = (
        select(WorkspaceMember.user_id)
        .where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.role == role,
        )
        .order_by(asc(WorkspaceMember.created_at))
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _coerce_uuid(raw: object) -> uuid.UUID | None:
    """Best-effort UUID coercion; returns ``None`` on any failure."""
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        return None


__all__ = [
    "ResolvedTarget",
    "RoutingContext",
    "RoutingError",
    "resolve_chain",
    "resolve_handle",
]
