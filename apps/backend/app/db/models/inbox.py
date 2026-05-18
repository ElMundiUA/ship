"""Inbox v1 schema (RFC-0010 — Plays / Inbox redesign).

Seven workspace-scoped tables that back the unified Inbox surface
in the Console. Everything cascades on workspace delete (B6's
disconnect / off-board story wipes them automatically); inbox
items additionally cascade their event tail and any run
escalations that point at them.

- :class:`MemberGroup` — operational group inside a workspace
  (e.g. ``secops``, ``oncall``). Distinct from permission roles;
  groups are the assignment unit Plays target via routing rules.
- :class:`MemberGroupMember` — composite-PK join row binding a
  user into a group.
- :class:`GroupAssignmentState` — round-robin pointer (1:1 with
  :class:`MemberGroup`) so rotation stays honest under contention.
- :class:`InboxRoutingRule` — workspace-level mapping from a Play
  ``handle`` (e.g. ``security_officer``) to a target
  (group / user / strategy) plus the assignment strategy.
- :class:`InboxItem` — the Inbox row itself: clarifications,
  improvements, failures, approvals, exceptions awaiting human
  disposition. Holds a *weak* reference to the source row in
  ``clarifications`` / ``improvements`` so deletes upstream don't
  drag the inbox row with them.
- :class:`InboxItemEvent` — append-only audit trail (created,
  assigned, snoozed, resolved, commented…). Cascades with the
  parent item.
- :class:`RunEscalation` — Run → Inbox linkage so a pipeline run
  can answer "what did this run kick over to a human?".

Schema details (column types, FK ON DELETE behaviour, defaults,
indexes, unique constraints) match the migration
``0031_inbox_v1`` and ``documentation/internal/inbox-redesign-planning.md``
§4 verbatim.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Interval,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701 — shared column helper.
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


# ---------------------------------------------------------------------------
# Member groups
# ---------------------------------------------------------------------------


class MemberGroup(Base):
    """One operational group inside a workspace.

    Groups are the assignment unit Plays target via
    :class:`InboxRoutingRule` (e.g. a ``security_officer`` handle
    can route to the ``secops`` group). They are intentionally
    separate from workspace permission roles — a user can sit in
    multiple groups, and groups carry no implicit permissions.

    ``key`` is the stable machine identifier (``secops``,
    ``oncall``); ``display_name`` is the human-friendly label
    rendered in the Console. Uniqueness is enforced per workspace.
    """

    __tablename__ = "member_groups"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "key",
            name="uq_member_groups_workspace_key",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(length=64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(length=160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


class MemberGroupMember(Base):
    """Composite-PK join row binding a user into a :class:`MemberGroup`.

    Both sides cascade on delete: removing a group wipes its
    membership rows, and de-provisioning a user removes them from
    every group they belonged to. There is no surrogate ``id`` —
    ``(group_id, user_id)`` is the natural key and the primary key.
    """

    __tablename__ = "member_group_members"
    __table_args__ = ()

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member_groups.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class GroupAssignmentState(Base):
    """Round-robin pointer for a :class:`MemberGroup` (1:1).

    The router uses ``last_assigned_user_id`` to honestly rotate
    new inbox items across the group's members: pick the member
    after the last one (sorted by some stable key), then update
    this row in the same transaction. Without this state two
    concurrent assigns could pick the same member.

    ``group_id`` is both PK and FK — there is at most one state
    row per group. ``last_assigned_user_id`` uses ``SET NULL`` on
    user delete so a departed teammate doesn't break rotation;
    the next pick simply starts from the head of the list.
    """

    __tablename__ = "group_assignment_state"
    __table_args__ = ()

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("member_groups.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    last_assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


# ---------------------------------------------------------------------------
# Routing rules
# ---------------------------------------------------------------------------


class InboxRoutingRule(Base):
    """Workspace-level mapping from a Play handle to an assignment target.

    A ``handle_key`` (e.g. ``security_officer``, ``ml_reviewer``)
    is what a Play declares it needs; the rule decides who picks
    it up. Uniqueness is enforced per ``(workspace_id, handle_key)``
    — each handle has exactly one active mapping per workspace.

    ``target_type`` selects the dispatch shape:

    - ``group`` — ``target_value`` is a ``member_groups.key``;
      ``assignment_strategy`` decides which member.
    - ``user`` — ``target_value`` is a ``users.id`` /
      e-mail-style identifier; the rule pins assignments to one
      person.
    - ``strategy`` — ``target_value`` names a higher-level
      strategy resolved by the router (e.g. ``self``).

    ``assignment_strategy`` is only meaningful when
    ``target_type='group'``:

    - ``round_robin`` — rotate via :class:`GroupAssignmentState`.
    - ``oncall`` — use the integration-derived current on-call.
    - ``first`` — always pick the alphabetically-first member.
    - ``NULL`` — no strategy needed (user / strategy targets).

    ``strategy_config`` is a free-form JSONB bag for strategy
    parameters (timezone windows, fallback lists, etc.).
    """

    __tablename__ = "inbox_routing_rules"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "handle_key",
            name="uq_inbox_routing_rules_workspace_handle",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    handle_key: Mapped[str] = mapped_column(String(length=64), nullable=False)
    # group | user | strategy
    target_type: Mapped[str] = mapped_column(String(length=16), nullable=False)
    target_value: Mapped[str] = mapped_column(String(length=160), nullable=False)
    # round_robin | oncall | first | NULL
    assignment_strategy: Mapped[str | None] = mapped_column(
        String(length=16), nullable=True
    )
    strategy_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()


# ---------------------------------------------------------------------------
# Inbox items
# ---------------------------------------------------------------------------


class InboxItem(Base):
    """One row in the unified Inbox awaiting human disposition.

    The Inbox is the single surface a human reviews; every
    upstream queue (clarifications, improvements, failed runs,
    approval gates, exception sweeps) lands here as an
    :class:`InboxItem`. The original row in
    ``clarifications`` / ``improvements`` is referenced via
    ``source_table`` + ``source_id`` as a **weak** FK — there is
    no DB-level constraint, so deletes upstream do not cascade
    here (planning §4: "weak FK; preserved on source delete").

    ``type`` records what the item is:

    - ``clarification`` — projected from ``clarifications``.
    - ``improvement`` — projected from ``improvements``.
    - ``failure`` — a pipeline run failed and needs eyes.
    - ``approval`` — a Play paused for explicit human approval.
    - ``exception`` — caught/escalated via the exception sweeper.

    ``status`` lifecycle:

    - ``new`` (initial)
    - ``snoozed`` (``snoozed_until`` is set; the item disappears
      from default views until the timer fires)
    - ``resolved`` (``resolved_at`` + ``resolved_by_user_id`` +
      ``resolution`` are set)
    - ``dismissed`` (closed without action; ``resolution='dismissed'``)

    ``resolution`` records *how* the item was closed and is one
    of ``answered`` / ``approved`` / ``rejected`` / ``accepted`` /
    ``dismissed`` / ``retried`` / ``acknowledged`` / ``auto_recovered``
    / ``stale`` (backfill-only values; enforced in app, not DB CHECK).

    ``category`` is the v2 triage bucket (CHECK-constrained):
    ``decision_needed`` / ``attention`` / ``failure`` / ``dismiss_silently``.
    Lower ``priority`` means more urgent (10 beats 50).

    FK behaviour:

    - ``workspace_id`` cascades on workspace delete.
    - ``repo_id`` / ``run_id`` / ``owner_user_id`` /
      ``resolved_by_user_id`` use ``SET NULL`` so deleting a repo,
      run or user leaves the audit trail intact.
    - ``source_id`` carries no FK constraint at all (see above).
    """

    __tablename__ = "inbox_items"
    __table_args__ = (
        Index(
            "ix_inbox_owner_status",
            "workspace_id",
            "owner_user_id",
            "status",
        ),
        Index(
            "ix_inbox_workspace_status",
            "workspace_id",
            "status",
            text("created_at DESC"),
        ),
        Index(
            "ix_inbox_repo",
            "repo_id",
            postgresql_where=text("repo_id IS NOT NULL"),
        ),
        Index(
            "ix_inbox_run",
            "run_id",
            postgresql_where=text("run_id IS NOT NULL"),
        ),
        Index(
            "ix_inbox_source_lookup",
            "source_table",
            "source_id",
            postgresql_where=text("source_table IS NOT NULL"),
        ),
        Index(
            "ix_inbox_workspace_category_status",
            "workspace_id",
            "category",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="SET NULL"),
        nullable=True,
    )
    # clarification | improvement | failure | approval | exception
    type: Mapped[str] = mapped_column(String(length=16), nullable=False)
    # clarifications | improvements | NULL
    source_table: Mapped[str | None] = mapped_column(
        String(length=32), nullable=True
    )
    # Weak FK — no DB-level constraint; deletes upstream do not cascade here.
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    play_key: Mapped[str | None] = mapped_column(String(length=128), nullable=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("routine_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(length=255), nullable=False)
    headline: Mapped[str] = mapped_column(String(length=80), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # new | snoozed | resolved | dismissed
    status: Mapped[str] = mapped_column(
        String(length=16), nullable=False, server_default=text("'new'")
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    intake_handle: Mapped[str | None] = mapped_column(
        String(length=64), nullable=True
    )
    intake_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = _ts_created()
    due_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    snoozed_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # answered | approved | rejected | accepted | dismissed | retried |
    # acknowledged | auto_recovered | stale
    resolution: Mapped[str | None] = mapped_column(
        String(length=32), nullable=True
    )
    # decision_needed | attention | failure | dismiss_silently
    category: Mapped[str] = mapped_column(
        String(length=16),
        nullable=False,
        server_default=text("'attention'"),
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        server_default=text("50"),
    )
    auto_resolvable: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
    )
    stale_after: Mapped[timedelta | None] = mapped_column(Interval, nullable=True)
    headline: Mapped[str | None] = mapped_column(String(length=80), nullable=True)


class InboxItemEvent(Base):
    """Append-only audit row for every disposition on an :class:`InboxItem`.

    Cascades with the parent item — when an Inbox row is hard
    deleted (rare; usually rows just transition to ``resolved``)
    its event tail goes too. ``actor_user_id`` uses ``SET NULL``
    so a departed teammate's actions remain visible in the trail.

    ``actor_kind`` records who took the action:

    - ``user`` — interactive Console user.
    - ``system`` — Ship background worker (snooze expiry,
      resolution sweepers, etc.).
    - ``agent`` — autonomous agent action (auto-acknowledge,
      auto-retry).

    ``action`` is one of ``created`` / ``assigned`` /
    ``reassigned`` / ``snoozed`` / ``unsnoozed`` / ``resolved`` /
    ``dismissed`` / ``commented`` / ``action_item_decided``.
    """

    __tablename__ = "inbox_item_events"
    __table_args__ = (
        Index(
            "ix_inbox_events_item",
            "item_id",
            text("created_at DESC"),
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inbox_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # user | system | agent
    actor_kind: Mapped[str] = mapped_column(String(length=16), nullable=False)
    # created | assigned | reassigned | snoozed | unsnoozed | resolved |
    # dismissed | commented | action_item_decided
    action: Mapped[str] = mapped_column(String(length=32), nullable=False)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    created_at: Mapped[datetime] = _ts_created()


# ---------------------------------------------------------------------------
# Run → Inbox linkage
# ---------------------------------------------------------------------------


@event.listens_for(InboxItem, "before_insert")
def _inbox_item_ensure_headline(
    _mapper: object, _connection: object, target: InboxItem
) -> None:
    """Backstop: derive ``headline`` when a caller omitted it (tests, legacy)."""
    if target.headline:
        return
    from backend.app.services.inbox.headline import derive_headline

    target.headline = derive_headline(
        summary=target.summary,
        title=target.title or "Inbox item",
    )


class RunEscalation(Base):
    """Pipeline run → :class:`InboxItem` linkage row.

    Lets a run answer "what did I kick over to a human?" and lets
    the Inbox UI back-link to the originating run. Uniqueness is
    enforced per ``(run_id, inbox_item_id)`` so re-emitting the
    same escalation from an idempotent worker is safe.

    Both FKs cascade on delete: dropping a run or its inbox item
    removes the linkage row.

    ``escalation_reason`` is a coarse machine-readable bucket:
    ``play_failed_repeatedly`` / ``requires_approval`` /
    ``etc.`` — the human-readable narrative lives in the linked
    :class:`InboxItem` itself.

    ``resolved_at`` / ``resolution`` / ``resolved_by_user_id`` are set
    when the linked inbox item is dispositioned (or by the taxonomy v2
    backfill for auto-closed blockers).
    """

    __tablename__ = "run_escalations"
    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "inbox_item_id",
            name="uq_run_escalations_run_item",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("routine_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    inbox_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("inbox_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    # play_failed_repeatedly | requires_approval | etc.
    escalation_reason: Mapped[str] = mapped_column(
        String(length=64), nullable=False
    )

    created_at: Mapped[datetime] = _ts_created()
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution: Mapped[str | None] = mapped_column(
        String(length=32), nullable=True
    )
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


__all__ = [
    "GroupAssignmentState",
    "InboxItem",
    "InboxItemEvent",
    "InboxRoutingRule",
    "MemberGroup",
    "MemberGroupMember",
    "RunEscalation",
]
