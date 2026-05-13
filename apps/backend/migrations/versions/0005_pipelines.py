"""create pipelines, pipeline_runs, pull_requests, workflow_runs (Day 3)

Revision ID: 0005_pipelines
Revises: 0004_workspace_repos
Create Date: 2026-04-19

Day 3 of the pilot lands the dashboard. Four new tables:

- ``pipelines`` — one row per workspace × workflow-kind. Auto-seeded on
  first repo activation; enabled/disabled via the dashboard toggle.
- ``pipeline_runs`` — one row per "run now" / webhook trigger, mostly
  for the dashboard's recent-runs strip in the pilot.
- ``pull_requests`` — write-through cache for the GitHub ``pull_request``
  webhook so the dashboard's "Recent PRs" panel doesn't need a per-render
  GitHub round-trip.
- ``workflow_runs`` — same idea for ``workflow_run`` events.

All four cascade off ``workspaces.id`` so dropping a workspace cleans
the dashboard data with it.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0005_pipelines"
down_revision: Union[str, None] = "0004_workspace_repos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipelines",
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
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("workflow_id", sa.String(length=120), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.String(length=32), nullable=True),
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
            "workspace_id", "kind", name="uq_pipelines_workspace_kind"
        ),
    )
    op.create_index(
        "ix_pipelines_workspace_id", "pipelines", ["workspace_id"]
    )

    op.create_table(
        "pipeline_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "pipeline_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pipelines.id", ondelete="CASCADE"),
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
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
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
    )
    op.create_index(
        "ix_pipeline_runs_pipeline_id_started",
        "pipeline_runs",
        ["pipeline_id", "started_at"],
    )
    op.create_index(
        "ix_pipeline_runs_workspace_id", "pipeline_runs", ["workspace_id"]
    )

    op.create_table(
        "pull_requests",
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
            sa.ForeignKey("workspace_repos.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("external_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.BigInteger(), nullable=False),
        sa.Column("repo_full_name", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "merged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "draft",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("author", sa.String(length=120), nullable=True),
        sa.Column("html_url", sa.String(length=1024), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at_external", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True),
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
            "external_id",
            name="uq_pull_requests_workspace_external",
        ),
    )
    op.create_index(
        "ix_pull_requests_workspace_updated",
        "pull_requests",
        ["workspace_id", "updated_at"],
    )

    op.create_table(
        "workflow_runs",
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
            sa.ForeignKey("workspace_repos.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("external_id", sa.BigInteger(), nullable=False),
        sa.Column("repo_full_name", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("conclusion", sa.String(length=32), nullable=True),
        sa.Column("head_branch", sa.String(length=255), nullable=True),
        sa.Column("head_sha", sa.String(length=40), nullable=True),
        sa.Column("actor", sa.String(length=120), nullable=True),
        sa.Column("html_url", sa.String(length=1024), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
            "external_id",
            name="uq_workflow_runs_workspace_external",
        ),
    )
    op.create_index(
        "ix_workflow_runs_workspace_updated",
        "workflow_runs",
        ["workspace_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_runs_workspace_updated", table_name="workflow_runs"
    )
    op.drop_table("workflow_runs")
    op.drop_index(
        "ix_pull_requests_workspace_updated", table_name="pull_requests"
    )
    op.drop_table("pull_requests")
    op.drop_index("ix_pipeline_runs_workspace_id", table_name="pipeline_runs")
    op.drop_index(
        "ix_pipeline_runs_pipeline_id_started", table_name="pipeline_runs"
    )
    op.drop_table("pipeline_runs")
    op.drop_index("ix_pipelines_workspace_id", table_name="pipelines")
    op.drop_table("pipelines")
