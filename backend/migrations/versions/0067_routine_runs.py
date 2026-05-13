"""Replace ``pipelines`` + ``pipeline_runs`` with ``routine_runs``.

The Pipeline concept retired in the deep-dig cleanup: no production
code wrote ``pipelines`` rows after the Phase-2.4 collapse, and the
``PipelineRun.pipeline_id`` FK kept Run-history coupled to a model
nobody else read or wrote. Both tables drop here; ``routine_runs``
takes their place with a clean FK to ``routines.id`` (the surviving
declared-routine table).

Per the user's "no old workspaces" call, we drop both old tables
without preserving rows. Downgrade is a no-op because the producer
side of ``pipelines`` has no implementation any more — recreating
an empty table buys nothing.

Revision ID: 0067_routine_runs
Revises: 0066_drop_fleet_lanes
Create Date: 2026-05-13
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0067_routine_runs"
down_revision: Union[str, None] = "0066_drop_fleet_lanes"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    # FK first (FK from pipeline_runs.pipeline_id → pipelines.id). The
    # CASCADE on the table drop also tears down FKs that other tables
    # pointed at ``pipeline_runs.id`` — namely clarifications + improvements
    # — but the *columns* on those tables survive. Null them so the values
    # don't reference a dead table; we re-FK them to ``routine_runs.id``
    # under the same column name below.
    op.execute("UPDATE clarifications SET pipeline_run_id = NULL")
    op.execute("UPDATE improvements SET pipeline_run_id = NULL")

    op.execute("DROP TABLE IF EXISTS pipeline_runs CASCADE")
    op.execute("DROP TABLE IF EXISTS pipelines CASCADE")

    op.create_table(
        "routine_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "routine_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("routines.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", sa.String(length=1024), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "outcome",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("run_token_hash", sa.String(length=64), nullable=True),
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
    )
    op.create_index(
        "ix_routine_runs_routine_started",
        "routine_runs",
        ["routine_id", "started_at"],
    )
    op.create_index(
        "ix_routine_runs_workspace_id",
        "routine_runs",
        ["workspace_id"],
    )

    # Rename clarifications.pipeline_run_id → clarifications.routine_run_id
    # and re-FK to routine_runs.id. Same for improvements.
    op.alter_column(
        "clarifications", "pipeline_run_id", new_column_name="routine_run_id"
    )
    op.alter_column(
        "improvements", "pipeline_run_id", new_column_name="routine_run_id"
    )
    op.create_foreign_key(
        "fk_clarifications_routine_run_id_routine_runs",
        "clarifications",
        "routine_runs",
        ["routine_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_improvements_routine_run_id_routine_runs",
        "improvements",
        "routine_runs",
        ["routine_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_improvements_routine_run_id_routine_runs",
        "improvements",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_clarifications_routine_run_id_routine_runs",
        "clarifications",
        type_="foreignkey",
    )
    op.alter_column(
        "improvements", "routine_run_id", new_column_name="pipeline_run_id"
    )
    op.alter_column(
        "clarifications", "routine_run_id", new_column_name="pipeline_run_id"
    )
    op.drop_index("ix_routine_runs_workspace_id", table_name="routine_runs")
    op.drop_index("ix_routine_runs_routine_started", table_name="routine_runs")
    op.drop_table("routine_runs")
    # Pipelines + pipeline_runs tables are not recreated — see module
    # docstring. Restore from backup if you need them.
