"""clarifications: tracker projection columns (D13)

Revision ID: 0013_clarifications_tracker
Revises: 0012_repo_secrets
Create Date: 2026-04-20

Extends ``clarifications`` with the projection surface Ship needs to
treat a labelled tracker ticket as the canonical "question awaiting
a human" record. Columns added:

- ``source``             — ``manual`` | ``pipeline`` | ``tracker``.
                           Drives PATCH write-back behaviour.
- ``tracker_provider``   — ``linear`` / ``github_issues`` / ``notion``.
- ``tracker_issue_key``  — vendor-natural id (``ENG-42``, ``owner/repo#123``).
- ``tracker_issue_url``  — UI deep-link.
- ``tracker_comment_id`` — vendor comment id carrying the ``@ship
                           clarification:`` marker.
- ``tracker_synced_at``  — last time the projection touched this row;
                           used by the cron to skip rows it just wrote.

Backfill: every existing row is ``source='manual'``. The
pipeline-ingress endpoint (``POST /clarifications/pipeline``) will
start writing ``source='pipeline'`` on new rows after this ships; no
data fix-up is needed because the old rows predate D13 and their
ownership is indistinguishable anyway.

Indexes:

- ``uq_clarifications_tracker_comment`` — partial unique on
  ``(workspace_id, tracker_provider, tracker_comment_id)`` where
  ``tracker_comment_id IS NOT NULL``. Guards the sync service's
  idempotency: re-running the projection on the same tracker ticket
  must not duplicate the row, even if the cron races with a webhook.
  Partial (not plain) so manual/pipeline rows that carry NULLs aren't
  stuck on a single composite key.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0013_clarifications_tracker"
down_revision: Union[str, None] = "0012_repo_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "clarifications",
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
    )
    op.add_column(
        "clarifications",
        sa.Column("tracker_provider", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "clarifications",
        sa.Column("tracker_issue_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "clarifications",
        sa.Column("tracker_issue_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "clarifications",
        sa.Column("tracker_comment_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "clarifications",
        sa.Column(
            "tracker_synced_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Partial unique: only apply to rows that actually carry a tracker
    # comment id. Manual/pipeline rows (all NULLs) aren't hemmed in.
    op.create_index(
        "uq_clarifications_tracker_comment",
        "clarifications",
        ["workspace_id", "tracker_provider", "tracker_comment_id"],
        unique=True,
        postgresql_where=sa.text("tracker_comment_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_clarifications_tracker_comment", table_name="clarifications"
    )
    op.drop_column("clarifications", "tracker_synced_at")
    op.drop_column("clarifications", "tracker_comment_id")
    op.drop_column("clarifications", "tracker_issue_url")
    op.drop_column("clarifications", "tracker_issue_key")
    op.drop_column("clarifications", "tracker_provider")
    op.drop_column("clarifications", "source")
