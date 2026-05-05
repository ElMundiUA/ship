"""knowledge_claim.reconciled_at timestamp + pending-claims partial index

The claim extractor (P1) inserts every freshly extracted claim with
``status='active', confidence=1.0`` and never looks at neighbours. The
reconciliation engine (P2) needs a way to find claims that haven't
been through the dedup / supersede / contradict-judge pass yet without
re-scanning the whole table on every cron tick.

``reconciled_at TIMESTAMPTZ`` defaults to NULL on insert (so the next
reconciler tick picks the row up); the partial index targets exactly
that NULL set so a workspace with 50k canon claims still scans a
handful of pending rows per tick.

Revision ID: 0060_claim_reconciled_at
Revises: 0059_knowledge_claims
Create Date: 2026-05-06
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "0060_claim_reconciled_at"
down_revision: Union[str, None] = "0059_knowledge_claims"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_claim",
        sa.Column(
            "reconciled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    # Partial index: every existing row from P1 is NULL, and the cron
    # tick filters on ``reconciled_at IS NULL``. A full btree index
    # would index ~all rows for a query that always wants the tiny
    # tail; partial keeps the index hot and small.
    op.execute(
        "CREATE INDEX ix_knowledge_claim_pending_reconciliation "
        "ON knowledge_claim (workspace_id, created_at) "
        "WHERE reconciled_at IS NULL"
    )


def downgrade() -> None:
    op.execute(
        "DROP INDEX IF EXISTS ix_knowledge_claim_pending_reconciliation"
    )
    op.drop_column("knowledge_claim", "reconciled_at")
