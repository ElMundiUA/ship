"""add digitalocean to native integration provider check

Revision ID: 0081_native_provider_do
Revises: 0080_repo_intel_project_mat
Create Date: 2026-05-29
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0081_native_provider_do"
down_revision: Union[str, None] = "0080_repo_intel_project_mat"
branch_labels = None
depends_on = None


_CONSTRAINT = "ck_native_integration_installations_provider"
_TABLE = "native_integration_installations"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        (
            "provider IN ('github', 'azure_devops', 'atlassian', "
            "'notion', 'linear', 'gitlab', 'digitalocean')"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        (
            "provider IN ('github', 'azure_devops', 'atlassian', "
            "'notion', 'linear', 'gitlab')"
        ),
    )
