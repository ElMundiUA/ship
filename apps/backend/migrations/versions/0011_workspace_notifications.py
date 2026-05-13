"""workspace notifications — dismissible dashboard banners (A4 + A5)

Revision ID: 0011_workspace_notifications
Revises: 0010_agent_v2
Create Date: 2026-04-20

Adds ``workspace_notifications`` for two new WOW-onboarding banners:

- **A4** "Return-to-Ship after PR merge": on ``pull_request.closed``
  with ``merged=true`` we mint a ``pr_merged`` row so the user who
  just clicked Merge on github.com gets a friendly "back in Ship"
  callout instead of an empty dashboard.
- **A5** "Self-heal auto-trigger on ``workflow_run`` failure": when a
  customer CI run fails we auto-dispatch the ``self_heal`` pipeline
  (if enabled) and mint a ``self_heal_dispatched`` row pointing at
  the healing run. ``self_heal_skipped`` captures the "we saw the
  failure but couldn't heal because the lane is off / not installed"
  case so users aren't left wondering.

Two indexes ship with the table:

- ``ix_workspace_notifications_open`` — partial index on
  ``(workspace_id, created_at)`` ``WHERE dismissed_at IS NULL``. This
  is the dashboard's hot path ("newest 5 open banners") and the
  partial predicate keeps it tiny compared to total volume.
- ``uq_workspace_notifications_dedupe`` — partial unique index on
  ``(workspace_id, dedupe_key)`` ``WHERE dedupe_key IS NOT NULL``.
  Webhook replays are a fact of life; this makes the database
  refuse a second ``pr_merged:<id>`` row at the write site instead
  of trusting every caller to remember a ``SELECT … FOR UPDATE``
  pre-check.

Everything cascades off ``workspaces.id`` so the B6 "disconnect"
flow sweeps banners out with the workspace.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0011_workspace_notifications"
down_revision: Union[str, None] = "0010_agent_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_notifications",
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
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("body", sa.String(length=2048), nullable=True),
        sa.Column("href", sa.String(length=1024), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column(
            "dismissed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    # Hot path: "newest open banners per workspace". Partial predicate
    # shrinks the index to "currently visible" rows only.
    op.create_index(
        "ix_workspace_notifications_open",
        "workspace_notifications",
        ["workspace_id", "created_at"],
        postgresql_where=sa.text("dismissed_at IS NULL"),
    )
    # Webhook replay protection. Partial unique index means rows can
    # exist without a dedupe key (future ad-hoc banners) but any row
    # that carries one enforces (workspace_id, dedupe_key) uniqueness.
    op.create_index(
        "uq_workspace_notifications_dedupe",
        "workspace_notifications",
        ["workspace_id", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_workspace_notifications_dedupe",
        table_name="workspace_notifications",
    )
    op.drop_index(
        "ix_workspace_notifications_open",
        table_name="workspace_notifications",
    )
    op.drop_table("workspace_notifications")
