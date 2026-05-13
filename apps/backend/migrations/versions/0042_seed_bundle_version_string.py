"""store seed bundle versions as product versions.

Revision ID: 0042_seed_bundle_version_string
Revises: 0041_rename_lanes_to_routines
Create Date: 2026-04-26
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0042_seed_bundle_version_string"
down_revision: Union[str, None] = "0041_rename_lanes_to_routines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "workspace_repos",
        "installed_bundle_version",
        type_=sa.String(length=16),
        existing_type=sa.Integer(),
        existing_nullable=True,
        postgresql_using=(
            "CASE "
            "WHEN installed_bundle_version IS NULL THEN NULL "
            "ELSE '0.' || installed_bundle_version::text "
            "END"
        ),
    )


def downgrade() -> None:
    op.alter_column(
        "workspace_repos",
        "installed_bundle_version",
        type_=sa.Integer(),
        existing_type=sa.String(length=16),
        existing_nullable=True,
        postgresql_using=(
            "CASE "
            "WHEN installed_bundle_version IS NULL THEN NULL "
            "WHEN installed_bundle_version ~ '^0\\.\\d+$' "
            "THEN split_part(installed_bundle_version, '.', 2)::integer "
            "WHEN installed_bundle_version ~ '^\\d+$' "
            "THEN installed_bundle_version::integer "
            "ELSE NULL "
            "END"
        ),
    )
