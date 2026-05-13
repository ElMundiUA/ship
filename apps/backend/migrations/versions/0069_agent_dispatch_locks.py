"""E16 — agent_dispatch_locks + workspaces.max_concurrent_dispatches.

Sets up the storage layer for the event-driven dispatcher
(ELS-121). Adds:

- ``agent_dispatch_locks`` table — per-(workspace_id, key) row that
  represents an in-flight or recently-claimed dispatch. ``claimed_at``
  is when the dispatcher grabbed the slot; ``expires_at`` is when the
  lock self-releases if the agent never reports finish (orphan
  protection, default 60 min from claim). Key strategy is opaque to
  the schema — the dispatcher writes ``ticket:<ticket_ref>`` today
  and may introduce other namespaces later (``daily:<routine_id>``,
  ``project:<anchor>``).

- ``workspaces.max_concurrent_dispatches`` — per-workspace override of
  the global default (``SHIP_DEFAULT_WORKSPACE_DISPATCH_CAP``). NULL
  means "use the global default"; an integer pins the cap.

No data movement and no FKs back into existing tables aside from the
workspace pointer — the lock table is purely working state, safe to
TRUNCATE at any time (the dispatcher rebuilds it from audit_log on
the next poll tick).

Revision ID: 0069_agent_dispatch_locks
Revises: 0068_routine_runs_fk_hotfix
Create Date: 2026-05-14
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0069_agent_dispatch_locks"
down_revision: Union[str, None] = "0068_routine_runs_fk_hotfix"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_dispatch_locks",
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
        # Opaque namespace+id ("ticket:ELS-121", "daily:knowledge_harvest").
        # 256 chars covers any reasonable tracker identifier plus the
        # routine name.
        sa.Column("key", sa.String(256), nullable=False),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Self-release deadline; the dispatcher treats any row with
        # expires_at <= now() as available regardless of whether the
        # agent reported finish. Default TTL is 60 min from claim;
        # callers can override per row when bundles need longer.
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '60 minutes'"),
        ),
        # The workflow_dispatch run that owns this lock, when known.
        # NULL is allowed because daily-tick locks are claimed before
        # the workflow run id is known. Audit-log row id, not
        # GH-action run id (which is observable separately).
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    # Active locks are queried by (workspace_id, key) — uniqueness
    # there is what makes "claim or fail" atomic via INSERT ... ON
    # CONFLICT. Once a row's expires_at passes, the dispatcher's
    # garbage sweep deletes it before re-inserting a fresh row, so the
    # unique constraint is permanent (no partial index gymnastics).
    op.create_index(
        "uq_agent_dispatch_locks_workspace_key",
        "agent_dispatch_locks",
        ["workspace_id", "key"],
        unique=True,
    )
    # Sweep query — find expired rows fast.
    op.create_index(
        "ix_agent_dispatch_locks_expires_at",
        "agent_dispatch_locks",
        ["expires_at"],
    )

    op.add_column(
        "workspaces",
        sa.Column(
            "max_concurrent_dispatches",
            sa.SmallInteger(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("workspaces", "max_concurrent_dispatches")
    op.drop_index(
        "ix_agent_dispatch_locks_expires_at",
        table_name="agent_dispatch_locks",
    )
    op.drop_index(
        "uq_agent_dispatch_locks_workspace_key",
        table_name="agent_dispatch_locks",
    )
    op.drop_table("agent_dispatch_locks")
