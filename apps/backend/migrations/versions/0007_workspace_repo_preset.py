"""carry a catalog preset on each activated repo (Day 4 Phase 2)

Revision ID: 0007_workspace_repo_preset
Revises: 0006_pipeline_repo_binding
Create Date: 2026-04-20

Day-4 Phase-2 ties each activated repo to one of the catalog presets
(``artifacts/collections/preset-*``) — ``web-app`` / ``api-backend`` /
``mobile-app`` / ``cli`` / ``monorepo`` / ``adoption-minimum``. The
wizard collects it once; :func:`seed_default_pipelines` keys off it
to decide which of the five lanes ship enabled vs. disabled on first
activation.

Column is nullable because legacy rows predate the concept — the
backend treats ``NULL`` the same as ``adoption-minimum`` (minimum
surface area, just PR review enabled) so no data migration is
required.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0007_workspace_repo_preset"
down_revision: Union[str, None] = "0006_pipeline_repo_binding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_repos",
        sa.Column("preset", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_repos", "preset")
