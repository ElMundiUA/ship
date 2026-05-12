"""Drop ``fleet_lanes`` + ``fleet_lane_exceptions`` — orphan after deep-dig.

The FleetLane / FleetLaneException ORM models had readers only in
``routes/pipelines.py:1119`` (fleet-scope subquery) and zero writers
in production. The deep-dig audit confirmed the tables hold whatever
historical rows the workspace already accumulated, but ``shipctl``,
the console, and the chat agent all stopped consuming them after
``automations_list`` / ``plays_*`` were retired in phase 1a.

This commit removes the ORM models + the only fleet-scope SQL site.
Migration drops the tables (no production traffic to preserve per
the user's explicit "no old workspaces" call).

Reversible by recreating the schema — but the producer side
(workspace-level mirror-lane rules) has no implementation any more,
so a downgrade would land empty tables. Operators restoring from
backup must run their own DDL.

Revision ID: 0066_drop_fleet_lanes
Revises: 0065_drop_methodology_chunks
Create Date: 2026-05-13
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0066_drop_fleet_lanes"
down_revision: Union[str, None] = "0065_drop_methodology_chunks"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    # Exception table has the FK on fleet_lanes.id — drop it first.
    op.execute("DROP TABLE IF EXISTS fleet_lane_exceptions")
    op.execute("DROP TABLE IF EXISTS fleet_lanes")


def downgrade() -> None:
    # Intentionally a no-op. The producer side of FleetLane rules was
    # never wired in production code; a recreated empty table buys
    # nothing.
    pass
