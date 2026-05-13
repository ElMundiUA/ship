"""Drop ``methodology_chunks`` — orphan data from the retired E13 corpus.

Phase 2.4 Step D retired the unauthenticated ``/search`` / ``/fetch``
methodology endpoints that fed this table. The reindex pipeline
(``services/methodology_index.py``) and the ORM model
(``db/models/methodology.py``) were deleted in the DEADCODE-2 sweep
because nothing imported them. The table itself stayed behind as
orphan data, documented in ``main.py`` lifespan as "awaiting a
follow-up migration to drop." This is that migration.

Reversible by recreating the schema, but we leave the pgvector
``methodology_chunks`` column definitions out of the downgrade since
the production data is gone and a re-create would land an empty
table either way — a separate restore would have to come from a
backup, not from this migration.

Revision ID: 0065_drop_methodology_chunks
Revises: 0064_chat_attachments
Create Date: 2026-05-13
"""

from __future__ import annotations

from typing import Union

from alembic import op


revision: str = "0065_drop_methodology_chunks"
down_revision: Union[str, None] = "0064_chat_attachments"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    # ``IF EXISTS`` so a fresh dev DB (where the create migration may
    # have been skipped manually) doesn't blow up. Production has the
    # table — we just stopped reading or writing it months ago.
    op.execute("DROP TABLE IF EXISTS methodology_chunks")


def downgrade() -> None:
    # Intentionally a no-op. Recreating the empty table buys nothing
    # — the reindex pipeline that filled it was deleted, so the
    # column would stay empty even after a downgrade. Operators
    # restoring from backup should run their own DDL.
    pass
