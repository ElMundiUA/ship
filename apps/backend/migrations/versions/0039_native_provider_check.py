"""ensure native provider check includes oauth providers

Revision ID: 0039_native_provider_check
Revises: 0038_native_integration_core
Create Date: 2026-04-25
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0039_native_provider_check"
down_revision: Union[str, None] = "0038_native_integration_core"
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
            "'notion', 'linear', 'gitlab')"
        ),
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT,
        _TABLE,
        "provider IN ('github', 'azure_devops', 'atlassian', 'gitlab')",
    )
