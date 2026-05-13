"""chat_threads.archived_at — bookkeeping for the idle-thread sweeper (Wave C).

The Wave C cron (:func:`backend.app.workers.jobs.archive_chat_threads
.archive_idle_chat_threads`) flips ``status`` from ``active`` to
``archived`` on threads with no user activity for >7 days. We record
*when* that flip happened in a dedicated column rather than reusing
``updated_at`` so:

- The console's "Archived" view can sort / display the auto-archive
  moment without inferring it from ``updated_at`` (which any future
  unrelated mutation would clobber).
- The cron can stay idempotent — a second run that picks the same
  row up does ``WHERE archived_at IS NULL`` and is a no-op for
  already-archived rows.

Backfill: existing ``status='archived'`` rows (legacy "user pressed
new conversation") leave ``archived_at`` NULL by design. Those were
human-archived, not sweeper-archived, and reporting "we don't know
when" is the honest answer.

Revision ID: 0036_chat_threads_archived_at
Revises: 0035_lane_origin
Create Date: 2026-04-24
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0036_chat_threads_archived_at"
down_revision: Union[str, None] = "0035_lane_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_threads",
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("chat_threads", "archived_at")
