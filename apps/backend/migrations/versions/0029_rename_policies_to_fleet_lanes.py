"""Rename workspace_policies → fleet_lanes (free up name for prose policies).

The existing ``workspace_policies`` / ``workspace_policy_exceptions``
tables hold *mirror-lane policies* — workspace-level rules that say
"pattern X runs as lane Y with cadence Z on every activated repo
unless excepted". That feature is staying, but the name "policy" is
being repurposed for free-text standing rules ("Always work via PR",
"Never commit secrets") that get injected into agent instructions at
runtime. To avoid two unrelated meanings of "policy" colliding in
the API/UI/codebase, we rename the existing concept to **Fleet
lanes** (chosen for symmetry with the existing "Fleet requests"
section in the Console).

This migration is a pure rename:

- ``workspace_policies`` → ``fleet_lanes``
- ``workspace_policy_exceptions`` → ``fleet_lane_exceptions``
- ``workspace_policy_exceptions.policy_id`` → ``fleet_lane_id``
- All indexes / unique constraints follow the same naming.

A subsequent migration (0030) creates a brand-new ``workspace_policies``
table for the prose-rule feature.

Revision ID: 0029_rename_to_fleet_lanes
Revises: 0028_promotion_candidates
Create Date: 2026-04-23
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0029_rename_to_fleet_lanes"
down_revision: Union[str, None] = "0028_promotion_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old indexes/constraints first so the rename doesn't
    # try to recreate them under the new table name automatically
    # (Postgres will keep the table-level constraint names attached
    # to the renamed table, but the explicit drop+create keeps the
    # final names canonical).
    op.drop_index(
        "ix_workspace_policy_exceptions_policy_id",
        table_name="workspace_policy_exceptions",
    )
    op.drop_index(
        "ix_workspace_policies_workspace_id",
        table_name="workspace_policies",
    )
    op.drop_constraint(
        "uq_workspace_policy_exceptions_repo",
        "workspace_policy_exceptions",
        type_="unique",
    )
    op.drop_constraint(
        "uq_workspace_policies_lane",
        "workspace_policies",
        type_="unique",
    )

    # Rename tables.
    op.rename_table("workspace_policies", "fleet_lanes")
    op.rename_table(
        "workspace_policy_exceptions",
        "fleet_lane_exceptions",
    )

    # ``op.rename_table`` keeps the PK / FK constraint names from the
    # old table, so a follow-up migration that creates a brand-new
    # ``workspace_policies`` table (RFC: prose policies, 0030) would
    # collide on ``pk_workspace_policies``. Rename them explicitly to
    # keep the SQLAlchemy naming convention canonical and free up the
    # old names for the new table.
    op.execute(
        "ALTER TABLE fleet_lanes "
        "RENAME CONSTRAINT pk_workspace_policies TO pk_fleet_lanes"
    )
    op.execute(
        "ALTER TABLE fleet_lanes "
        "RENAME CONSTRAINT "
        "fk_workspace_policies_workspace_id_workspaces "
        "TO fk_fleet_lanes_workspace_id_workspaces"
    )
    op.execute(
        "ALTER TABLE fleet_lane_exceptions "
        "RENAME CONSTRAINT pk_workspace_policy_exceptions "
        "TO pk_fleet_lane_exceptions"
    )
    op.execute(
        "ALTER TABLE fleet_lane_exceptions "
        "RENAME CONSTRAINT "
        "fk_workspace_policy_exceptions_policy_id_workspace_policies "
        "TO fk_fleet_lane_exceptions_fleet_lane_id_fleet_lanes"
    )
    op.execute(
        "ALTER TABLE fleet_lane_exceptions "
        "RENAME CONSTRAINT "
        "fk_workspace_policy_exceptions_repo_id_workspace_repos "
        "TO fk_fleet_lane_exceptions_repo_id_workspace_repos"
    )

    # Rename the FK column on the exceptions table.
    op.alter_column(
        "fleet_lane_exceptions",
        "policy_id",
        new_column_name="fleet_lane_id",
    )

    # Recreate constraints/indexes under the new names.
    op.create_unique_constraint(
        "uq_fleet_lanes_lane",
        "fleet_lanes",
        ["workspace_id", "lane_id"],
    )
    op.create_index(
        "ix_fleet_lanes_workspace_id",
        "fleet_lanes",
        ["workspace_id"],
    )
    op.create_unique_constraint(
        "uq_fleet_lane_exceptions_repo",
        "fleet_lane_exceptions",
        ["fleet_lane_id", "repo_id"],
    )
    op.create_index(
        "ix_fleet_lane_exceptions_fleet_lane_id",
        "fleet_lane_exceptions",
        ["fleet_lane_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_fleet_lane_exceptions_fleet_lane_id",
        table_name="fleet_lane_exceptions",
    )
    op.drop_constraint(
        "uq_fleet_lane_exceptions_repo",
        "fleet_lane_exceptions",
        type_="unique",
    )
    op.drop_index(
        "ix_fleet_lanes_workspace_id",
        table_name="fleet_lanes",
    )
    op.drop_constraint(
        "uq_fleet_lanes_lane",
        "fleet_lanes",
        type_="unique",
    )

    op.alter_column(
        "fleet_lane_exceptions",
        "fleet_lane_id",
        new_column_name="policy_id",
    )

    op.rename_table("fleet_lane_exceptions", "workspace_policy_exceptions")
    op.rename_table("fleet_lanes", "workspace_policies")

    op.create_unique_constraint(
        "uq_workspace_policies_lane",
        "workspace_policies",
        ["workspace_id", "lane_id"],
    )
    op.create_index(
        "ix_workspace_policies_workspace_id",
        "workspace_policies",
        ["workspace_id"],
    )
    op.create_unique_constraint(
        "uq_workspace_policy_exceptions_repo",
        "workspace_policy_exceptions",
        ["policy_id", "repo_id"],
    )
    op.create_index(
        "ix_workspace_policy_exceptions_policy_id",
        "workspace_policy_exceptions",
        ["policy_id"],
    )
