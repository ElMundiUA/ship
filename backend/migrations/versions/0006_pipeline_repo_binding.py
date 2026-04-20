"""bind pipelines to repos + carry callback-token hash on runs (Day 4 Phase 1)

Revision ID: 0006_pipeline_repo_binding
Revises: 0005_pipelines
Create Date: 2026-04-20

Day-4 Phase-1 turns "Run now" into a real GitHub Actions
``workflow_dispatch``. Two schema additions to support that:

- ``pipelines.repo_id`` (nullable FK → ``workspace_repos.id``,
  ``ON DELETE SET NULL``). Auto-seeded pipelines from repo activation
  now remember which repo they fire against, so the dispatcher can
  resolve the install + workflow file without asking the user.
- ``pipeline_runs.run_token_hash`` (nullable string). When we
  ``workflow_dispatch`` we mint a short-lived callback JWT and pass it
  via ``inputs.ship_run_token``; we store the SHA-256 of the token so
  the result-callback endpoint can prove the caller is the dispatch we
  triggered (without persisting the raw token, which a curious actor
  with DB read could otherwise replay).

Both columns are nullable so the migration is backwards-safe — old
stub rows simply have NULL for both fields and the new code paths
treat NULL as "legacy / unbound".
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0006_pipeline_repo_binding"
down_revision: Union[str, None] = "0005_pipelines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pipelines",
        sa.Column(
            "repo_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspace_repos.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_pipelines_repo_id", "pipelines", ["repo_id"]
    )

    op.add_column(
        "pipeline_runs",
        sa.Column(
            "run_token_hash",
            sa.String(length=64),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("pipeline_runs", "run_token_hash")
    op.drop_index("ix_pipelines_repo_id", table_name="pipelines")
    op.drop_column("pipelines", "repo_id")
