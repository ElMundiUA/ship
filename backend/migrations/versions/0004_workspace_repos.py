"""create workspace_repos for the Day-2 picker flow

Revision ID: 0004_workspace_repos
Revises: 0003_github_installations
Create Date: 2026-04-19

Persists the user's repo activations from the WOW-onboarding picker.
Each row is a workspace's snapshot of a vendor repository (currently
GitHub via App installation; ``provider`` leaves room for ``gitlab`` /
``ado`` later). We de-dupe on ``(workspace_id, provider, external_id)``
so a vendor-side rename of the repo doesn't double-activate.

The ``installation_id`` FK points at our internal ``github_installations``
UUID, not at GitHub's numeric installation_id. Cascading delete kicks in
when the user uninstalls the App (the installation row is removed first,
then the workspace_repo rows go with it).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0004_workspace_repos"
down_revision: Union[str, None] = "0003_github_installations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_repos",
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
            "installation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("github_installations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.BigInteger(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column(
            "default_branch",
            sa.String(length=255),
            nullable=False,
            server_default=sa.text("'main'"),
        ),
        sa.Column(
            "private",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("html_url", sa.String(length=1024), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
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
            "provider",
            "external_id",
            name="uq_workspace_repos_external",
        ),
    )
    op.create_index(
        "ix_workspace_repos_workspace_id",
        "workspace_repos",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_repos_installation_id",
        "workspace_repos",
        ["installation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_repos_installation_id", table_name="workspace_repos"
    )
    op.drop_index(
        "ix_workspace_repos_workspace_id", table_name="workspace_repos"
    )
    op.drop_table("workspace_repos")
