"""per-repo secrets (B10)

Revision ID: 0012_repo_secrets
Revises: 0011_workspace_notifications
Create Date: 2026-04-20

Adds ``repo_secrets`` — Ship-managed Actions secrets mirrored to
GitHub on write so ``schedule:``, ``push:``, and
``workflow_dispatch:`` triggered workflows all read them the same
way as user-managed repo secrets (``${{ secrets.FOO }}``).

Threat model: a stolen DB dump is useless without ``ENCRYPTION_KEY``.
A compromised Ship backend is already game over (it has the
Installation token for every wired repo); B10 doesn't try to defend
against that class of breach. What it *does* defend against is
rogue read-only access to the database, which is the realistic
escape path for a hosted service with pg backups on S3.

Indexes:

- ``uq_repo_secrets_repo_id_name`` mirrors GitHub's unique-per-name
  rule so we can treat name collisions as "upsert" without a
  separate lookup.
- ``ix_repo_secrets_repo_id_created_at`` is the list-UI hot path
  (newest first, scoped to one repo).

Cascades: delete on ``workspace_repos`` / ``workspaces`` sweeps the
row. ``users`` sets NULL (same convention as :class:`AuditLog`) so
"who created it" is lost but the secret record survives operator
offboarding.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0012_repo_secrets"
down_revision: Union[str, None] = "0011_workspace_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "repo_secrets",
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
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("masked_hint", sa.String(length=8), nullable=True),
        sa.Column("description", sa.String(length=512), nullable=True),
        sa.Column("github_key_id", sa.String(length=64), nullable=True),
        sa.Column(
            "sync_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("sync_error", sa.String(length=2048), nullable=True),
        sa.Column(
            "last_synced_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
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
    # Mirror GitHub's one-slot-per-name rule so our upserts line up
    # with the upstream's idempotency semantics.
    op.create_index(
        "uq_repo_secrets_repo_id_name",
        "repo_secrets",
        ["repo_id", "name"],
        unique=True,
    )
    # List-UI hot path: "show me every secret on this repo, newest
    # first". A plain btree is fine; secret counts per repo stay in
    # the single digits for any realistic pilot tenant.
    op.create_index(
        "ix_repo_secrets_repo_id_created_at",
        "repo_secrets",
        ["repo_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_repo_secrets_repo_id_created_at", table_name="repo_secrets"
    )
    op.drop_index("uq_repo_secrets_repo_id_name", table_name="repo_secrets")
    op.drop_table("repo_secrets")
