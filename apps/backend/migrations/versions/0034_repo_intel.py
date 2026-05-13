"""repo_intel — per-repo harvested intelligence (Wave 8a P5-03).

Adds the ``repo_intel`` table that the onboarding wizard's harvest
job (``backend.app.services.repo_intel.harvest_repo_intel``) writes
into. One row per harvest, versioned per repo, with at most one
``is_current=TRUE`` per repo (enforced by the partial unique index).

Why a separate table rather than a column on ``workspace_repos``:

- Versioning is per-row, not per-cell. We want the *next* harvest to
  insert a fresh row and demote the previous one — keeping history
  navigable when the heuristic changes or the repo's shape drifts.
- ``workspace_repos`` is touched on every webhook event; adding
  fat JSONB blobs to it would balloon the row size and churn the
  table for callers that don't care about intel.
- The structured payload columns (``languages``, ``frameworks``,
  ``entry_points``, …) document a *shape* the agent reads; isolating
  them in their own table makes the consumer surface obvious.

Backfill: none. Existing repos get their first row when the wizard
re-runs (or a manual refresh fires). Until then,
``get_current_intel`` returns ``None`` and callers are expected to
treat that as "not yet harvested" rather than an error.

Revision ID: 0034_repo_intel
Revises: 0033_pipeline_run_outcome
Create Date: 2026-04-24

Downgrade drops the table outright. The column is purely additive
and never written from any pre-existing pipeline.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0034_repo_intel"
down_revision: Union[str, None] = "0033_pipeline_run_outcome"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repo_intel",
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
            sa.ForeignKey("workspace_repos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "languages",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "frameworks",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "package_managers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "entry_points",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "structure",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "commit_style",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "visual_tokens",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "harvested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("harvested_by", sa.String(length=64), nullable=True),
        sa.Column("harvest_duration_ms", sa.Integer(), nullable=True),
        sa.Column("harvest_error", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "repo_id", "version", name="uq_repo_intel_repo_version"
        ),
    )
    op.create_index(
        "ix_repo_intel_workspace_id", "repo_intel", ["workspace_id"]
    )
    op.create_index("ix_repo_intel_repo_id", "repo_intel", ["repo_id"])
    # Partial unique index: at most one current row per repo. Lives
    # only in the migration because SQLAlchemy can't express ``WHERE
    # is_current = TRUE`` portably.
    op.create_index(
        "idx_repo_intel_repo_current",
        "repo_intel",
        ["repo_id"],
        unique=True,
        postgresql_where=sa.text("is_current = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("idx_repo_intel_repo_current", table_name="repo_intel")
    op.drop_index("ix_repo_intel_repo_id", table_name="repo_intel")
    op.drop_index("ix_repo_intel_workspace_id", table_name="repo_intel")
    op.drop_table("repo_intel")
