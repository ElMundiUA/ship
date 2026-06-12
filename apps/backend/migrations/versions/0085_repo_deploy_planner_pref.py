"""add per-repo deploy-planner preference (provider + model)

Revision ID: 0085_repo_deploy_planner_pref
Revises: 0084_deployment_events
Create Date: 2026-06-02
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa

from alembic import op


revision: str = "0085_repo_deploy_planner_pref"
down_revision: Union[str, None] = "0084_deployment_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_repos",
        sa.Column("deploy_planner_provider", sa.String(32), nullable=True),
    )
    op.add_column(
        "workspace_repos",
        sa.Column("deploy_planner_model", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_repos", "deploy_planner_model")
    op.drop_column("workspace_repos", "deploy_planner_provider")
