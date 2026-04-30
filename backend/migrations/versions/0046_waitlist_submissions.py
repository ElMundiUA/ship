"""waitlist_submissions: closed-beta waitlist captures (E08 T05)

Revision ID: 0046_waitlist_submissions
Revises: 0045_platform_invites
Create Date: 2026-04-30

Public waitlist form on landing captures interested users (email, role, tracker,
agent, free-text note). Each submission creates a row in waitlist_submissions;
periodic reviews convert promising submissions into platform_invites.

Schema:

- ``id``                    — UUID primary key
- ``email``                 — invitee email (text, NOT NULL)
- ``role``                  — role from form (text, nullable: PO, Eng manager, Tech lead, Other)
- ``tracker``               — current tracker (text, nullable: Linear, GitHub Issues, Jira, Notion, None/Other)
- ``agent``                 — current agent (text, nullable: Cursor, Claude Code, Codex, Copilot, Other, None)
- ``note``                  — free-text response to "what would Ship help with?" (text, nullable, max 500 chars enforced at API layer)
- ``created_at``            — submission timestamp

Indexes:

- Btree on ``email`` for dedup detection
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0046_waitlist_submissions"
down_revision: Union[str, None] = "0045_platform_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create waitlist_submissions table.
    op.create_table(
        "waitlist_submissions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("tracker", sa.Text(), nullable=True),
        sa.Column("agent", sa.Text(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Index on email for lookups and dedup detection.
    op.create_index(
        "ix_waitlist_submissions_email",
        "waitlist_submissions",
        ["email"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_waitlist_submissions_email",
        table_name="waitlist_submissions",
    )
    op.drop_table("waitlist_submissions")
