"""agent_requests — Phase 3 ad-hoc agent run ledger.

Revision ID: 0021_agent_requests
Revises: 0020_repo_bundle_version
Create Date: 2026-04-22

Introduces the ``agent_requests`` table that backs the Console's
``/requests`` surface. One row per ``POST
/v1/workspaces/{ws}/repos/{id}/requests`` call — rows are lightweight
dispatch receipts, updated by the ``shipctl callback`` emitted at the
end of ``adhoc-agent-run.yml``.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0021_agent_requests"
down_revision: Union[str, None] = "0020_repo_bundle_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_requests",
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
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("agent_slug", sa.String(64), nullable=False),
        sa.Column("context_ref", sa.String(1024), nullable=True),
        sa.Column("prompt", sa.String(4096), nullable=False),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'dispatched'"),
        ),
        sa.Column("summary", sa.String(1024), nullable=True),
        sa.Column("gh_workflow_run_id", sa.BigInteger(), nullable=True),
        sa.Column("gh_html_url", sa.String(1024), nullable=True),
        sa.Column("run_token_hash", sa.String(64), nullable=True),
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
    )
    op.create_index(
        "ix_agent_requests_workspace_created",
        "agent_requests",
        ["workspace_id", "created_at"],
    )
    op.create_index(
        "ix_agent_requests_repo_created",
        "agent_requests",
        ["repo_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_requests_repo_created", table_name="agent_requests"
    )
    op.drop_index(
        "ix_agent_requests_workspace_created", table_name="agent_requests"
    )
    op.drop_table("agent_requests")
