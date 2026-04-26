"""rename lanes projection to routines.

Revision ID: 0041_rename_lanes_to_routines
Revises: 0040_member_specialists
Create Date: 2026-04-26
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0041_rename_lanes_to_routines"
down_revision: Union[str, None] = "0040_member_specialists"
branch_labels = None
depends_on = None


def _rename_constraint(table: str, candidates: list[str], new_name: str) -> None:
    quoted_candidates = ", ".join(f"'{candidate}'" for candidate in candidates)
    op.execute(
        f"""
        DO $$
        DECLARE
            old_name text;
        BEGIN
            SELECT conname INTO old_name
            FROM pg_constraint
            WHERE conrelid = '{table}'::regclass
              AND conname = ANY(ARRAY[{quoted_candidates}]);
            IF old_name IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE %I RENAME CONSTRAINT %I TO %I',
                    '{table}',
                    old_name,
                    '{new_name}'
                );
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    op.drop_constraint(
        "fk_pipeline_runs_lane_id_lanes",
        "pipeline_runs",
        type_="foreignkey",
    )
    op.drop_index("ix_pipeline_runs_lane_id", table_name="pipeline_runs")
    op.alter_column(
        "pipeline_runs",
        "lane_id",
        new_column_name="routine_id",
        existing_nullable=True,
    )

    op.rename_table("lanes", "routines")
    op.alter_column(
        "routines",
        "lane_id",
        new_column_name="routine_id",
        existing_nullable=False,
    )

    _rename_constraint("routines", ["uq_lanes_repo_lane"], "uq_routines_repo_routine")
    _rename_constraint(
        "routines",
        ["ck_lanes_kind", "ck_lanes_ck_lanes_kind"],
        "ck_routines_ck_routines_kind",
    )
    _rename_constraint(
        "routines",
        ["ck_lanes_origin", "ck_lanes_ck_lanes_origin"],
        "ck_routines_ck_routines_origin",
    )
    _rename_constraint(
        "routines",
        ["fk_lanes_workspace_id_workspaces"],
        "fk_routines_workspace_id_workspaces",
    )
    _rename_constraint(
        "routines",
        ["fk_lanes_repo_id_workspace_repos"],
        "fk_routines_repo_id_workspace_repos",
    )
    op.execute("ALTER INDEX ix_lanes_workspace_id RENAME TO ix_routines_workspace_id")
    op.execute("ALTER INDEX ix_lanes_repo_id RENAME TO ix_routines_repo_id")
    op.execute("ALTER INDEX ix_lanes_repo_origin RENAME TO ix_routines_repo_origin")

    op.create_foreign_key(
        "fk_pipeline_runs_routine_id_routines",
        "pipeline_runs",
        "routines",
        ["routine_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_pipeline_runs_routine_id",
        "pipeline_runs",
        ["routine_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_pipeline_runs_routine_id_routines",
        "pipeline_runs",
        type_="foreignkey",
    )
    op.drop_index("ix_pipeline_runs_routine_id", table_name="pipeline_runs")
    op.alter_column(
        "pipeline_runs",
        "routine_id",
        new_column_name="lane_id",
        existing_nullable=True,
    )

    _rename_constraint("routines", ["uq_routines_repo_routine"], "uq_lanes_repo_lane")
    _rename_constraint(
        "routines",
        ["ck_routines_kind", "ck_routines_ck_routines_kind"],
        "ck_lanes_ck_lanes_kind",
    )
    _rename_constraint(
        "routines",
        ["ck_routines_origin", "ck_routines_ck_routines_origin"],
        "ck_lanes_ck_lanes_origin",
    )
    _rename_constraint(
        "routines",
        ["fk_routines_workspace_id_workspaces"],
        "fk_lanes_workspace_id_workspaces",
    )
    _rename_constraint(
        "routines",
        ["fk_routines_repo_id_workspace_repos"],
        "fk_lanes_repo_id_workspace_repos",
    )
    op.execute("ALTER INDEX ix_routines_workspace_id RENAME TO ix_lanes_workspace_id")
    op.execute("ALTER INDEX ix_routines_repo_id RENAME TO ix_lanes_repo_id")
    op.execute("ALTER INDEX ix_routines_repo_origin RENAME TO ix_lanes_repo_origin")

    op.alter_column(
        "routines",
        "routine_id",
        new_column_name="lane_id",
        existing_nullable=False,
    )
    op.rename_table("routines", "lanes")

    op.create_foreign_key(
        "fk_pipeline_runs_lane_id_lanes",
        "pipeline_runs",
        "lanes",
        ["lane_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_pipeline_runs_lane_id",
        "pipeline_runs",
        ["lane_id"],
    )
