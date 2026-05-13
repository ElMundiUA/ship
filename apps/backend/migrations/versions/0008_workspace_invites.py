"""workspace invites for team management (B7)

Revision ID: 0008_workspace_invites
Revises: 0007_workspace_repo_preset
Create Date: 2026-04-20

Adds the ``workspace_invites`` table plus its indexes. Tokens are
stored as SHA-256 hashes (``bytea``) so a DB leak doesn't hand out
access to every pending invite — the plaintext is returned exactly
once to the admin that created the invite and never persisted.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_workspace_invites"
down_revision: Union[str, None] = "0007_workspace_repo_preset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_invites",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "workspace_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "token_hash", sa.LargeBinary(), nullable=False, unique=True
        ),
        sa.Column(
            "invited_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "accepted_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_workspace_invites_workspace_id",
        "workspace_invites",
        ["workspace_id"],
    )
    op.create_index(
        "ix_workspace_invites_email", "workspace_invites", ["email"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workspace_invites_email", table_name="workspace_invites"
    )
    op.drop_index(
        "ix_workspace_invites_workspace_id", table_name="workspace_invites"
    )
    op.drop_table("workspace_invites")
