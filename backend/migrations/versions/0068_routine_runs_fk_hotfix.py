"""Hotfix for 0067: re-target inbox_items + run_escalations FK to routine_runs.

0067 dropped ``pipeline_runs`` CASCADE but didn't re-FK the two
columns that pointed at it (``inbox_items.run_id``,
``run_escalations.run_id``). The columns survive as bare UUIDs and
the application boot crashes at first ORM access because the
SQLAlchemy mapper still resolves ``ForeignKey("pipeline_runs.id")``
against ``Base.metadata``, which no longer contains that table.

This hotfix:

- Wipes the orphaned ``inbox_items.run_id`` / ``run_escalations.run_id``
  values that pointed at rows in the now-defunct ``pipeline_runs``
  (the rows themselves are gone via 0067's CASCADE).
- Re-creates the two foreign keys on ``routine_runs.id`` so future
  data stays consistent.
- Adds the missing ``server_default=gen_random_uuid()`` to
  ``routine_runs.id`` — without it, inserts that don't pass an
  ``id`` explicitly fail with NotNullViolation.

Revision ID: 0068_routine_runs_fk_hotfix
Revises: 0067_routine_runs
Create Date: 2026-05-13
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0068_routine_runs_fk_hotfix"
down_revision: Union[str, None] = "0067_routine_runs"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    # Add server_default on routine_runs.id (idempotent: ALTER COLUMN
    # SET DEFAULT replaces any prior default).
    op.execute(
        "ALTER TABLE routine_runs ALTER COLUMN id SET DEFAULT gen_random_uuid()"
    )

    # Wipe values pointing at the now-defunct pipeline_runs ids. The
    # SET NULL semantic that the original FK would have applied never
    # fired because the FK constraint itself was dropped by 0067's
    # CASCADE.
    op.execute("UPDATE inbox_items SET run_id = NULL")
    # run_escalations.run_id is NOT NULL, so we can't NULL them.
    # 0067's CASCADE on pipeline_runs already deleted every row in
    # run_escalations (the FK was ondelete=CASCADE), so the table
    # should be empty — TRUNCATE is a no-op safety net in case any
    # rows were created via the broken model in the interim.
    op.execute("TRUNCATE run_escalations")

    op.create_foreign_key(
        "fk_inbox_items_run_id_routine_runs",
        "inbox_items",
        "routine_runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_run_escalations_run_id_routine_runs",
        "run_escalations",
        "routine_runs",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_run_escalations_run_id_routine_runs",
        "run_escalations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_inbox_items_run_id_routine_runs",
        "inbox_items",
        type_="foreignkey",
    )
    op.execute("ALTER TABLE routine_runs ALTER COLUMN id DROP DEFAULT")
