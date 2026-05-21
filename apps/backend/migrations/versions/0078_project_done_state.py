"""workspace_project_priorities — add 'done' state + completed_at.

Projects whose every child ticket has completed should drop out of the
operator's live prioritizer (Active / Drafts / Parked) into a collapsed
History section, and the Linear project itself moves to Completed so it
stops cluttering the tracker. That needs a fourth priority state,
``done``, plus a ``completed_at`` timestamp to sort the History section
by recency.

- Replace the state CHECK to allow ``done`` alongside the existing
  ``active`` / ``planning`` / ``parked``.
- Add nullable ``completed_at`` (set when the row auto-completes; NULL
  for every other state).

Revision ID: 0078_project_done_state
Revises: 0076_inbox_headline_not_null
Create Date: 2026-05-21

Note: revision id kept <=32 chars (``alembic_version.version_num`` is
``varchar(32)``).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0078_project_done_state"
down_revision: Union[str, None] = "0076_inbox_headline_not_null"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None

_CK = "ck_workspace_project_priorities_state"
_TABLE = "workspace_project_priorities"


def upgrade() -> None:
    op.drop_constraint(_CK, _TABLE, type_="check")
    op.create_check_constraint(
        _CK,
        _TABLE,
        "state IN ('active', 'planning', 'parked', 'done')",
    )
    op.add_column(
        _TABLE,
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Roll any 'done' rows back to 'parked' before re-tightening the
    # CHECK so the constraint can be re-created.
    op.execute(
        f"UPDATE {_TABLE} SET state = 'parked' WHERE state = 'done'"
    )
    op.drop_column(_TABLE, "completed_at")
    op.drop_constraint(_CK, _TABLE, type_="check")
    op.create_check_constraint(
        _CK,
        _TABLE,
        "state IN ('active', 'planning', 'parked')",
    )
