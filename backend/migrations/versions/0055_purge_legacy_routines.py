"""purge stale Routine rows that aren't in the canonical seven

Companion to ``0054_purge_legacy_pipelines``: where 0054 nuked the
``pipelines`` auto-seeds, this one nukes orphan rows in the
``routines`` table (the SQLAlchemy model is :class:`Lane` /
:class:`Routine`). ``lanes_sync`` is supposed to keep ``routines`` in
lockstep with each repo's ``.ship/config.yml`` — hard-deletes rows
for routines no longer declared — but in practice old ids
(``code_map``, ``tech_debt``, ``daily_standup``, ``flow_release_notes``,
``self_heal``, ``scan_*``) survived the closed-beta canon migrations
because lanes_sync didn't always fire on the wizard-seed PR merge.

Result: even after 0054 cleaned pipelines, the dashboard still
projects legacy LEGACY cards because the projector unions
``routines`` AND ``pipelines`` rows for lane ids.

Closed-beta has no real users yet, so we hard-delete instead of
patching the lanes_sync timing. Removes every row whose ``routine_id``
isn't in the canonical seven (mirrors
:data:`backend.app.services.lane_recipes.DEFAULT_SEED_LANES`).

``pipeline_runs.routine_id`` is ``ON DELETE SET NULL`` so historical
run audit survives — the run row stays, just loses its routine FK.

Revision ID: 0055_purge_legacy_routines
Revises: 0054_purge_legacy_pipelines
Create Date: 2026-05-05
"""

from __future__ import annotations

from typing import Union

from alembic import op
from sqlalchemy import text


revision: str = "0055_purge_legacy_routines"
down_revision: Union[str, None] = "0054_purge_legacy_pipelines"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


# Mirror of :data:`backend.app.services.lane_recipes.DEFAULT_SEED_LANES`
# keys. Hardcoded so this migration's behaviour is frozen at the time
# it ran on each prod DB, independent of future renames in the
# constant.
_CANONICAL_ROUTINE_IDS: tuple[str, ...] = (
    "daily",
    "retro",
    "healthcheck",
    "tech_review",
    "qa_review",
    "security_review",
    "process_review",
)


def upgrade() -> None:
    placeholders = ", ".join(
        f":id_{i}" for i in range(len(_CANONICAL_ROUTINE_IDS))
    )
    params = {
        f"id_{i}": slug for i, slug in enumerate(_CANONICAL_ROUTINE_IDS)
    }
    bind = op.get_bind()
    # ``pipeline_runs.routine_id`` is ON DELETE SET NULL — the runs
    # rows survive the cleanup, they just lose the lane backref.
    bind.execute(
        text(f"DELETE FROM routines WHERE routine_id NOT IN ({placeholders})"),
        params,
    )


def downgrade() -> None:
    # No-op: the deleted rows were stale projections of
    # ``.ship/config.yml`` from a pre-canon era. lanes_sync rebuilds
    # ``routines`` from each repo's current YAML on the next push,
    # so there's nothing meaningful to restore manually.
    pass
