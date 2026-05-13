"""workspace project priorities — default state to 'planning' (ELS-82)

When the priority axis landed (0057), the column's default was
``'active'`` to match the pre-state contract: any saved row was
pickable. ELS-82 flips that default to ``'planning'`` so newly
created projects stop bypassing the dashboard's drafting +
decomposition flow. The agent picker (ELS-80) only sees ``active``
projects; new projects now sit in Drafts (``planning``) until the
operator hands them off through the dashboard, decomposition runs,
the row flips to ``parked`` (ELS-81), and the PO promotes to
``active`` manually.

Existing rows are deliberately NOT touched. Live work in ``active``
should keep flowing; an operator who wants to gate an in-flight
project parks it explicitly. Backfilling everyone to ``planning``
would freeze workspaces overnight without warning.

Revision ID: 0062_priority_default_planning
Revises: 0061_knowledge_topic_views
Create Date: 2026-05-06

NOTE: revision id kept ≤ 32 chars on purpose — Alembic's
``alembic_version.version_num`` column is ``VARCHAR(32)`` by default
and a longer id (the original draft was 36 chars,
``0062_priority_state_default_planning``) raises
``StringDataRightTruncation`` during the post-upgrade
``UPDATE alembic_version`` step. The DDL itself runs fine but the
transaction can't commit, alembic raises, ``alembic upgrade head``
exits non-zero, ``deploy/backend/entrypoint.sh`` fails, and the
container crash-loops. Cost us ~40 minutes of prod outage before
the rename. Future migrations: keep revision ids short.
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0062_priority_default_planning"
down_revision: Union[str, None] = "0061_knowledge_topic_views"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


_TABLE = "workspace_project_priorities"


def upgrade() -> None:
    # Default-only migration: alter the server default; existing rows
    # keep their current state. The CHECK constraint already pins the
    # accepted enum at the DB layer, so the new default value remains
    # valid without further work.
    op.alter_column(
        _TABLE,
        "state",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        server_default=sa.text("'planning'"),
    )


def downgrade() -> None:
    op.alter_column(
        _TABLE,
        "state",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        server_default=sa.text("'active'"),
    )
