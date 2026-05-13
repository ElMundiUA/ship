"""lanes.origin — provenance for synthetic lane sync (Wave 8b P5-07).

Adds an ``origin`` column to ``lanes`` so the wizard's
:func:`backend.app.services.synthetic_lane_sync.synthetic_lane_sync`
helper can insert rows BEFORE the seed PR merges, and the real
post-merge :func:`backend.app.services.lanes_sync.sync_lanes_for_repo`
can later reconcile them without clobbering operator edits.

Three legal values:

- ``'merged'`` — the row reflects what the latest ``.ship/config.yml``
  on the default branch declares. Default for every existing row
  (those Lanes only exist because a merge sync wrote them).
- ``'wizard_seed_synthetic'`` — the wizard wrote the row from
  :data:`backend.app.services.lane_recipes.DEFAULT_BUNDLE` *before*
  the seed PR even opened. Lets the Inbox / Coverage / Automations
  surfaces light up immediately on a freshly-activated repo instead
  of staying empty until the operator merges.
- ``'manual'`` — placeholder for the future "Add lane" admin flow;
  not written by anything in P5-07 but reserved so a follow-up
  doesn't need a second migration.

Reconciliation contract (handled in code, not in this migration):

- A real-merge sync upgrades ``'wizard_seed_synthetic'`` → ``'merged'``
  in place when the merged config still references the same
  ``(repo_id, lane_id)`` and the same ``kind``. No row replacement,
  no FK churn — preserves ``last_run_at`` / ``last_run_status``
  across the transition.
- When the merged config diverges (operator edited the PR, deleted
  a lane, or renamed it) the syncer falls back to its existing
  add/update/remove path. Synthetic rows for now-absent lanes get
  hard-deleted along with any other stale rows.

Backfill is straightforward: every existing Lane row was written by
the post-merge syncer, so the SQL default ``'merged'`` matches reality
without a separate UPDATE.

Revision ID: 0035_lane_origin
Revises: 0034_repo_intel
Create Date: 2026-04-24

Downgrade drops the column; safe because every consumer treats the
absence as "this Lane is the merged truth", which is the historical
behaviour.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0035_lane_origin"
down_revision: Union[str, None] = "0034_repo_intel"
branch_labels = None
depends_on = None


# Vocabulary kept in lockstep with
# :data:`backend.app.db.models.lanes._LANE_ORIGIN_VALUES`. The CHECK
# is enforced in-DB so a future code path can't slip in a typo and
# silently break the post-merge reconciliation contract.
_VALID_ORIGIN_VALUES: tuple[str, ...] = (
    "merged",
    "wizard_seed_synthetic",
    "manual",
)


def upgrade() -> None:
    op.add_column(
        "lanes",
        sa.Column(
            "origin",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'merged'"),
        ),
    )
    # CHECK on the column instead of an enum type: keeps the values
    # editable from app code without a follow-up DDL migration when
    # the catalogue grows (``'manual'`` for the admin-add flow,
    # eventually ``'imported'`` for migration-from-other-tooling).
    op.create_check_constraint(
        "ck_lanes_origin",
        "lanes",
        "origin IN ("
        + ", ".join(f"'{v}'" for v in _VALID_ORIGIN_VALUES)
        + ")",
    )
    # Lookup index for the post-merge reconciler — it filters
    # ``WHERE repo_id = ? AND origin = 'wizard_seed_synthetic'`` to
    # decide which rows to promote vs. leave alone.
    op.create_index(
        "ix_lanes_repo_origin",
        "lanes",
        ["repo_id", "origin"],
    )


def downgrade() -> None:
    op.drop_index("ix_lanes_repo_origin", table_name="lanes")
    op.drop_constraint("ck_lanes_origin", "lanes", type_="check")
    op.drop_column("lanes", "origin")
