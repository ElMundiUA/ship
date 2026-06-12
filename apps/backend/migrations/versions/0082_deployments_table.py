"""add deployments table

Revision ID: 0082_deployments_table
Revises: 0081_native_provider_do
Create Date: 2026-05-29
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "0082_deployments_table"
down_revision: Union[str, None] = "0081_native_provider_do"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("repo_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("status_detail", sa.String(64), nullable=True),
        sa.Column(
            "plan",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "provider_ref",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("live_url", sa.String(1024), nullable=True),
        sa.Column("healthy", sa.Boolean(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["repo_id"], ["workspace_repos.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deployments_workspace_id", "deployments", ["workspace_id"]
    )
    op.create_index(
        "ix_deployments_repo_id", "deployments", ["repo_id"]
    )
    op.create_index(
        "ix_deployments_workspace_repo",
        "deployments",
        ["workspace_id", "repo_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_deployments_workspace_repo", table_name="deployments")
    op.drop_index("ix_deployments_repo_id", table_name="deployments")
    op.drop_index("ix_deployments_workspace_id", table_name="deployments")
    op.drop_table("deployments")
