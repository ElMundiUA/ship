"""inbox v1 schema (RFC-0010 Plays / Inbox redesign).

Adds the seven tables that back the Inbox v1 system. Purely
additive — the existing ``clarifications`` and ``improvements``
tables are retained so the backfill (``0032_*``) can populate
``inbox_items`` from them and so legacy write paths keep working
during the rollout.

Tables introduced:

- ``member_groups`` — workspace-scoped operational groups
  (distinct from permission roles), e.g. ``secops``, ``oncall``.
- ``member_group_members`` — composite PK join table
  (``group_id``, ``user_id``).
- ``group_assignment_state`` — round-robin pointer per group so
  rotation is honest under contention (1:1 with ``member_groups``).
- ``inbox_routing_rules`` — workspace-level mapping from a Play
  ``handle`` (e.g. ``security_officer``) to a target
  (group / user / strategy) plus assignment strategy.
- ``inbox_items`` — the Inbox itself: clarifications, improvements,
  failures, approvals, exceptions awaiting human disposition.
- ``inbox_item_events`` — append-only audit trail for every
  disposition, reassign, snooze, comment.
- ``run_escalations`` — Run → Inbox linkage so a pipeline run can
  surface "what did this run produce" for the operator.

Schema details (column types, FK ON DELETE behaviour, defaults,
indexes, unique constraints) match
``documentation/internal/inbox-redesign-planning.md`` §4 verbatim.

Revision ID: 0031_inbox_v1
Revises: 0030_policies_prose
Create Date: 2026-04-23

Downgrade drops all seven tables and their named indexes /
constraints in reverse FK-dependency order. Because this revision
is additive only, downgrade is non-destructive to data living in
``clarifications`` / ``improvements`` / ``pipeline_runs``.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0031_inbox_v1"
down_revision: Union[str, None] = "0030_policies_prose"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- member_groups ------------------------------------------------------
    op.create_table(
        "member_groups",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "key",
            name="uq_member_groups_workspace_key",
        ),
    )

    # --- member_group_members ----------------------------------------------
    op.create_table(
        "member_group_members",
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member_groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint(
            "group_id",
            "user_id",
            name="pk_member_group_members",
        ),
    )

    # --- group_assignment_state --------------------------------------------
    op.create_table(
        "group_assignment_state",
        sa.Column(
            "group_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("member_groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "last_assigned_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # --- inbox_routing_rules -----------------------------------------------
    op.create_table(
        "inbox_routing_rules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("handle_key", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_value", sa.String(length=160), nullable=False),
        sa.Column(
            "assignment_strategy", sa.String(length=16), nullable=True
        ),
        sa.Column(
            "strategy_config",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "handle_key",
            name="uq_inbox_routing_rules_workspace_handle",
        ),
    )

    # --- inbox_items -------------------------------------------------------
    op.create_table(
        "inbox_items",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "repo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_repos.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("source_table", sa.String(length=32), nullable=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("play_key", sa.String(length=128), nullable=True),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'new'"),
        ),
        sa.Column(
            "owner_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("intake_handle", sa.String(length=64), nullable=True),
        sa.Column("intake_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "snoozed_until", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "resolved_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "resolved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolution", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_inbox_owner_status",
        "inbox_items",
        ["workspace_id", "owner_user_id", "status"],
    )
    op.create_index(
        "ix_inbox_workspace_status",
        "inbox_items",
        ["workspace_id", "status", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_inbox_repo",
        "inbox_items",
        ["repo_id"],
        postgresql_where=sa.text("repo_id IS NOT NULL"),
    )
    op.create_index(
        "ix_inbox_run",
        "inbox_items",
        ["run_id"],
        postgresql_where=sa.text("run_id IS NOT NULL"),
    )
    op.create_index(
        "ix_inbox_source_lookup",
        "inbox_items",
        ["source_table", "source_id"],
        postgresql_where=sa.text("source_table IS NOT NULL"),
    )

    # --- inbox_item_events -------------------------------------------------
    op.create_table(
        "inbox_item_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inbox_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_inbox_events_item",
        "inbox_item_events",
        ["item_id", sa.text("created_at DESC")],
    )

    # --- run_escalations ---------------------------------------------------
    op.create_table(
        "run_escalations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "inbox_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inbox_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "escalation_reason", sa.String(length=64), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "run_id",
            "inbox_item_id",
            name="uq_run_escalations_run_item",
        ),
    )


def downgrade() -> None:
    # --- run_escalations ---------------------------------------------------
    op.drop_constraint(
        "uq_run_escalations_run_item",
        "run_escalations",
        type_="unique",
    )
    op.drop_table("run_escalations")

    # --- inbox_item_events -------------------------------------------------
    op.drop_index(
        "ix_inbox_events_item", table_name="inbox_item_events"
    )
    op.drop_table("inbox_item_events")

    # --- inbox_items -------------------------------------------------------
    op.drop_index("ix_inbox_source_lookup", table_name="inbox_items")
    op.drop_index("ix_inbox_run", table_name="inbox_items")
    op.drop_index("ix_inbox_repo", table_name="inbox_items")
    op.drop_index("ix_inbox_workspace_status", table_name="inbox_items")
    op.drop_index("ix_inbox_owner_status", table_name="inbox_items")
    op.drop_table("inbox_items")

    # --- inbox_routing_rules -----------------------------------------------
    op.drop_constraint(
        "uq_inbox_routing_rules_workspace_handle",
        "inbox_routing_rules",
        type_="unique",
    )
    op.drop_table("inbox_routing_rules")

    # --- group_assignment_state --------------------------------------------
    op.drop_table("group_assignment_state")

    # --- member_group_members ----------------------------------------------
    op.drop_table("member_group_members")

    # --- member_groups -----------------------------------------------------
    op.drop_constraint(
        "uq_member_groups_workspace_key",
        "member_groups",
        type_="unique",
    )
    op.drop_table("member_groups")
