"""platform_invites: invite-only closed-beta table (E08)

Revision ID: 0045_platform_invites
Revises: 0044_methodology_chunks
Create Date: 2026-04-30

Platform-wide invite tokens for closed-beta onboarding. Each invite is a
one-time-use token sent via email (external channel) that creates a new
``users`` row and optionally grants platform-wide admin status.

Schema:

- ``id``                    — UUID primary key
- ``email``                 — invitee email (text)
- ``token_hash``            — SHA-256 hash of the plaintext token (unique)
- ``created_by_user_id``    — platform admin who created the invite (FK users.id)
- ``created_at``            — creation timestamp
- ``expires_at``            — hard expiry deadline (410 Gone after)
- ``accepted_at``           — when invitee successfully logged in and accepted
- ``accepted_by_user_id``   — the user record created from this invite
- ``revoked_at``            — when the creating admin revoked the invite
- ``note``                  — optional admin note about the invite

Indexes:

- Btree on ``email`` (non-unique — same email can be re-invited after expiry/revoke)
- Unique on ``token_hash`` (plaintext never stored, only hash)
- Btree on ``(accepted_at IS NULL, expires_at)`` for "open invites" queries
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0045_platform_invites"
down_revision: Union[str, None] = "0044_methodology_chunks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add is_platform_admin column to users table.
    op.add_column(
        "users",
        sa.Column(
            "is_platform_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # Create platform_invites table.
    op.create_table(
        "platform_invites",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.Text(), nullable=False),
        # NB: ``unique=True`` here would auto-generate a constraint with the
        # same name as the explicit ``op.create_index`` below (because the
        # naming convention in ``db/base.py`` produces ``uq_<table>_<col>``).
        # Keep the column plain and own uniqueness via the named index.
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_by_user_id",
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
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "accepted_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
    )

    # Index on email (non-unique).
    op.create_index(
        "ix_platform_invites_email",
        "platform_invites",
        ["email"],
    )

    # Unique index on token_hash.
    op.create_index(
        "uq_platform_invites_token_hash",
        "platform_invites",
        ["token_hash"],
        unique=True,
    )

    # Partial index for "open invites" query: (accepted_at IS NULL, expires_at).
    op.execute(
        "CREATE INDEX ix_platform_invites_open "
        "ON platform_invites (accepted_at IS NULL DESC, expires_at) "
        "WHERE accepted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_platform_invites_open")
    op.drop_index(
        "uq_platform_invites_token_hash",
        table_name="platform_invites",
    )
    op.drop_index(
        "ix_platform_invites_email",
        table_name="platform_invites",
    )
    op.drop_table("platform_invites")
    op.drop_column("users", "is_platform_admin")
