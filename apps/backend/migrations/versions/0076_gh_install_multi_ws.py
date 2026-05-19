"""github_installations — allow multiple workspaces per installation.

Pilot constraint: ``uq_github_installations_installation_id`` enforced
1:1 between a GitHub App install and a Ship workspace. Operators with
several Ship workspaces under the same GitHub org (Dev / Staging /
Prod) had to either re-install per workspace (breaks the others) or
share a single Ship workspace across environments (loses isolation).

Drop the single-column unique on ``installation_id`` and replace it
with a compound unique on ``(workspace_id, installation_id)`` so the
same install can attach to N workspaces while still being unique
within a workspace. Add an index on ``installation_id`` alone so the
webhook fan-out path (one install → N workspace rows) stays fast.

Existing data is fine — every row currently has at most one
``installation_id`` because the old unique kept it that way; the new
compound constraint is no stricter for any of them.

Revision ID: 0076_gh_install_multi_ws
Revises: 0075_planning_proposals
Create Date: 2026-05-19

NOTE: revision id ≤ 32 chars — ``alembic_version.version_num`` is
``VARCHAR(32)``; the original ``0076_github_installations_multi_workspace``
(42 chars) fails CI with ``StringDataRightTruncation``.
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0076_gh_install_multi_ws"
down_revision: Union[str, None] = "0075_planning_proposals"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_github_installations_installation_id",
        "github_installations",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_github_installations_workspace_installation",
        "github_installations",
        ["workspace_id", "installation_id"],
    )
    op.create_index(
        "ix_github_installations_installation_id",
        "github_installations",
        ["installation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_github_installations_installation_id",
        table_name="github_installations",
    )
    op.drop_constraint(
        "uq_github_installations_workspace_installation",
        "github_installations",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_github_installations_installation_id",
        "github_installations",
        ["installation_id"],
    )
