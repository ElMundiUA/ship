"""repo_intel — project_type + sdlc_maturity classification (BS0.2).

The bootstrap intelligence work needs to know two things about every
activated repo before it can recommend SDLC tooling: *what kind of
project is this* (web / mobile / desktop / backend / library) and *how
mature is its delivery setup* (tests, e2e, Docker, compose, env count,
promotion). Both are derived deterministically from the already-harvested
file listing + manifests by ``services.repo_intel`` — this migration just
adds the two columns the harvest now writes.

``project_type`` is a denormalised String column (filterable: "show me
every web repo that still has no Dockerfile") while the maturity flags +
classification confidence/signals live in the ``sdlc_maturity`` JSONB
blob, consistent with the model's "harvested signals live in JSONB"
convention. Existing rows predate classification: ``project_type`` stays
NULL and ``sdlc_maturity`` defaults to ``{}`` until the next harvest.

Revision ID: 0080_repo_intel_project_mat
Revises: 0079_workspace_repo_routing
Create Date: 2026-05-24

Note: revision id kept <=32 chars (``alembic_version.version_num`` is
``varchar(32)``).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0080_repo_intel_project_mat"
down_revision: Union[str, None] = "0079_workspace_repo_routing"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None

_TABLE = "repo_intel"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("project_type", sa.String(length=32), nullable=True),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "sdlc_maturity",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "sdlc_maturity")
    op.drop_column(_TABLE, "project_type")
