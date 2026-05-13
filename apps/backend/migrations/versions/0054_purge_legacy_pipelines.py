"""purge stale Pipeline rows that aren't in the canonical seven routines

The ``pipelines`` table was auto-seeded on first repo activation in
the pre-canon era — rows pinned to legacy lane ids (``code_map``,
``tech_debt``, ``daily_standup``, ``flow_release_notes``, ``pr_review``,
``self_heal``, ``scan_*``) that no recent ``.ship/config.yml`` declares.
``lanes_sync`` keeps :class:`Lane` (the ``routines`` table) in lockstep
with the YAML and hard-deletes orphans, but it doesn't touch
``Pipeline`` rows. The dashboard's process projector unioned both
tables, so legacy ids leaked onto the canvas as "LEGACY" cards the
operator couldn't get rid of.

Pre-launch cleanup: delete every ``Pipeline`` row whose ``lane_id``
isn't in the canonical seven routine ids the seed bundle currently
emits (matches :data:`backend.app.services.lane_recipes.DEFAULT_SEED_LANES`).
``pipeline_runs.pipeline_id`` cascades on delete, so historical run
rows for legacy lanes go too — acceptable because those lanes never
actually fired against the new canon.

Revision ID: 0054_purge_legacy_pipelines
Revises: 0053_consolidate_buckets
Create Date: 2026-05-04
"""

from __future__ import annotations

from typing import Union

from alembic import op
from sqlalchemy import text


revision: str = "0054_purge_legacy_pipelines"
down_revision: Union[str, None] = "0053_consolidate_buckets"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


# Canonical routine ids from
# :data:`backend.app.services.lane_recipes.DEFAULT_SEED_LANES`.
# Hard-coded here (rather than imported) so the migration stays
# stable if the constant moves or grows entries — alembic state is
# a snapshot in time.
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
    # ``pipeline_runs.pipeline_id`` is ``ON DELETE CASCADE`` so child
    # rows disappear with the parent — no separate cleanup needed.
    placeholders = ", ".join(
        f":id_{i}" for i in range(len(_CANONICAL_ROUTINE_IDS))
    )
    params = {
        f"id_{i}": slug for i, slug in enumerate(_CANONICAL_ROUTINE_IDS)
    }
    bind = op.get_bind()
    bind.execute(
        text(f"DELETE FROM pipelines WHERE lane_id NOT IN ({placeholders})"),
        params,
    )


def downgrade() -> None:
    # No-op: the deleted rows were stale auto-seeds with no semantic
    # value. The dashboard re-creates Pipeline rows on demand when
    # the operator triggers a routine; there's nothing to restore.
    pass
