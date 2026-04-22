"""Track the bundle version installed on each workspace repo.

Revision ID: 0020_workspace_repo_bundle_version
Revises: 0019_repo_scoped_integrations
Create Date: 2026-04-22

Adds ``workspace_repos.installed_bundle_version`` (nullable integer)
so the dashboard can detect drift between the currently-published
seed bundle (``seed_bundle.BUNDLE_VERSION``) and what a given repo
last received via wizard-seed / install-bundle. Nullable:

- ``NULL`` after upgrade for every existing row; routes will
  populate it on the next successful seed.
- Populated with the current ``BUNDLE_VERSION`` as the PR is
  opened, so the UI immediately knows "this repo is up to date".

Partial backfill is done here for rows that already minted a
``run_token_hash`` (a reliable "wizard-seeded" marker pre-dating
this column): they're set to ``1``, i.e. the baseline bundle
version. Legacy ``install_bundle``-only rows without a run token
stay ``NULL`` until their next re-seed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0020_repo_bundle_version"
down_revision = "0019_repo_scoped_integrations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_repos",
        sa.Column("installed_bundle_version", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE workspace_repos "
            "SET installed_bundle_version = 1 "
            "WHERE run_token_hash IS NOT NULL "
            "  AND installed_bundle_version IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("workspace_repos", "installed_bundle_version")
