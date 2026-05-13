"""lanes table + pipeline_runs.lane_id FK (RFC-0007 Phase 7)

Revision ID: 0018_lanes
Revises: 0017_distiller_runs
Create Date: 2026-04-21

Phase 7A of RFC-0007 introduces the backend's first view of
customer-declared lanes. Up to Phase 6 the backend only knew about
the four starter ``Pipeline`` rows seeded from
``backend.app.services.starter_workflows``; the customer's own
``.ship/config.yml`` (v2, ``lanes:`` block) was invisible to the
dashboard.

This migration creates the ``lanes`` table as the authoritative
projection of each repo's ``.ship/config.yml`` lane entries:

- one row per ``(repo_id, lane_id)`` pair;
- ``kind`` pinned to ``once | event | schedule`` via a CHECK;
- ``pattern`` records which artifact id the lane delegates to, so
  the Console can link out to ``/catalog/<pattern>`` without a
  second fetch;
- ``cron`` / ``idempotency_key`` copied out of the nested config
  for cheap querying; the untouched raw ``config_blob`` stays as
  JSONB for forward compatibility when the v2 schema grows more
  fields;
- ``synced_at`` + ``sync_source`` (``<ref_sha>:<path>``) give the
  Console a freshness indicator and power the "re-sync now" button
  without touching GitHub on every render;
- ``last_run_at`` / ``last_run_status`` are populated best-effort
  by the ``workflow_run.completed`` webhook when the event's
  ``path`` matches ``.github/workflows/ship-<lane_id>.yml``.

``pipeline_runs`` also gains an optional ``lane_id`` FK so a future
"Run this lane now" button can surface custom lanes in the existing
run detail view. Nothing writes to the column yet — adding it now
keeps the Console contract stable once that button lands.

Downgrade drops both columns / table without touching any existing
``pipelines`` or ``pipeline_runs`` rows.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0018_lanes"
down_revision: Union[str, None] = "0017_distiller_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lanes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
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
            sa.ForeignKey("workspace_repos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lane_id", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("pattern", sa.String(255), nullable=True),
        sa.Column("cron", sa.String(128), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "config_blob",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "last_run_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "last_run_status",
            sa.String(32),
            nullable=True,
        ),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("sync_source", sa.String(255), nullable=True),
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
        sa.UniqueConstraint("repo_id", "lane_id", name="uq_lanes_repo_lane"),
        sa.CheckConstraint(
            "kind IN ('once', 'event', 'schedule')",
            name="ck_lanes_kind",
        ),
    )
    op.create_index(
        "ix_lanes_workspace_id",
        "lanes",
        ["workspace_id"],
    )
    op.create_index(
        "ix_lanes_repo_id",
        "lanes",
        ["repo_id"],
    )

    # Optional back-link from a run to the Lane it was triggered for.
    # Nothing writes to it in Phase 7 — it's reserved for the future
    # "Trigger lane now" Console action that will synthesise a
    # ``PipelineRun`` for custom lanes the same way ``Pipeline`` runs
    # do today.
    op.add_column(
        "pipeline_runs",
        sa.Column(
            "lane_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lanes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_pipeline_runs_lane_id",
        "pipeline_runs",
        ["lane_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_lane_id", table_name="pipeline_runs")
    op.drop_column("pipeline_runs", "lane_id")

    op.drop_index("ix_lanes_repo_id", table_name="lanes")
    op.drop_index("ix_lanes_workspace_id", table_name="lanes")
    op.drop_table("lanes")
