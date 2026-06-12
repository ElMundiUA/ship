"""add deployment_events table (per-app activity feed)

Revision ID: 0084_deployment_events
Revises: 0083_deployments_id_default
Create Date: 2026-06-01
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "0084_deployment_events"
down_revision: Union[str, None] = "0083_deployments_id_default"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployment_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["workspace_repos.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deployment_events_app",
        "deployment_events",
        ["workspace_id", "repo_id", "provider", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_events_app", table_name="deployment_events")
    op.drop_table("deployment_events")
