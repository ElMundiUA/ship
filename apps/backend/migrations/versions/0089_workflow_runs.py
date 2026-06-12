"""workflow_runs + workflow_step_runs — durable workflow state (W8.7)

Thesis 8: bounded deterministic fan-outs. ``workflow_step_runs``
carries the UNIQUE (workflow_run_id, step_id, attempt) idempotency
key the dispatch gate (W8.2) relies on. Forward revision off 0088
(chained off `alembic heads` at implement time per the ticket note —
the pre-assigned 0088 collided with telegram_pending_actions).

Revision ID: 0089_workflow_runs
Revises: 0088_telegram_pending_actions
Create Date: 2026-06-12
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "0089_workflow_runs"
down_revision: Union[str, None] = "0088_telegram_pending_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_workflow_runs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("spec_name", sa.String(120), nullable=False),
        sa.Column(
            "spec_version",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'1'"),
        ),
        sa.Column(
            "inputs",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("trigger_kind", sa.String(16), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("output", JSONB(), nullable=True),
        sa.Column("triggered_by", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_agent_workflow_runs_ws_status",
        "agent_workflow_runs",
        ["workspace_id", "status"],
    )

    op.create_table(
        "agent_workflow_step_runs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workflow_run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_workflow_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_id", sa.String(120), nullable=False),
        sa.Column(
            "attempt", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("agent_provider", sa.String(32), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("output", JSONB(), nullable=True),
        sa.Column("lock_key", sa.String(255), nullable=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "workflow_run_id",
            "step_id",
            "attempt",
            name="uq_agent_workflow_step_attempt",
        ),
    )
    op.create_index(
        "ix_agent_workflow_step_runs_run", "agent_workflow_step_runs", ["workflow_run_id"]
    )
    op.create_index(
        "ix_agent_workflow_step_runs_ci_run", "agent_workflow_step_runs", ["run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_workflow_step_runs_ci_run", table_name="agent_workflow_step_runs")
    op.drop_index("ix_agent_workflow_step_runs_run", table_name="agent_workflow_step_runs")
    op.drop_table("agent_workflow_step_runs")
    op.drop_index(
        "ix_agent_workflow_runs_ws_status", table_name="agent_workflow_runs"
    )
    op.drop_table("agent_workflow_runs")
