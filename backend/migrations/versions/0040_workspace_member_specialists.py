"""workspace member answer specialist slugs (Inbox / routing UX).

Adds JSON array on ``workspace_members`` for which specialist queues
(BA, QA, …) a person may answer. ``["*"]`` means all queues. Empty
``[]`` means none (owners default to all via backfill).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0040_member_specialists"
down_revision: Union[str, None] = "0039_native_provider_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workspace_members",
        sa.Column(
            "answer_specialist_slugs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    # Owners can answer all specialist-tagged work by default.
    op.execute(
        """
        UPDATE workspace_members
        SET answer_specialist_slugs = '["*"]'::jsonb
        WHERE role = 'owner'
        """
    )
    op.alter_column(
        "workspace_members",
        "answer_specialist_slugs",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("workspace_members", "answer_specialist_slugs")
