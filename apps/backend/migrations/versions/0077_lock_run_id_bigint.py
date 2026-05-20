"""agent_dispatch_locks.run_id — UUID → bigint to match audit_log.id.

The column was scaffolded as ``UUID`` in 0069 but its sole consumer,
``dispatch_lock_sweep.sweep_dangling_project_locks``, joins
``agent_dispatch_locks.run_id = audit_log.id`` — and ``audit_log.id``
is ``bigint``. The mismatch never surfaced in prod because every
``acquire_lock`` caller leaves ``run_id`` NULL (the lock is claimed
before the dispatch audit row exists, so the sweeper uses its
project-id fallback path). It DID surface in CI: any test that
exercises the primary sweep path inserts a lock with a real
``audit_log.id`` (an int), and asyncpg rejects ``int`` for a UUID
column with ``'int' object has no attribute 'bytes'`` — turning the
whole backend suite red and blocking every merge.

All existing rows have ``run_id IS NULL``, so the type change needs no
data conversion (``USING NULL``).

Revision ID: 0077_lock_run_id_bigint
Revises: 0076_gh_install_multi_ws
Create Date: 2026-05-20

Note: revision id kept <=32 chars (``alembic_version.version_num`` is
``varchar(32)``).
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "0077_lock_run_id_bigint"
down_revision: Union[str, None] = "0076_gh_install_multi_ws"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.alter_column(
        "agent_dispatch_locks",
        "run_id",
        existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        type_=sa.BigInteger(),
        existing_nullable=True,
        postgresql_using="NULL",
    )


def downgrade() -> None:
    op.alter_column(
        "agent_dispatch_locks",
        "run_id",
        existing_type=sa.BigInteger(),
        type_=sa.dialects.postgresql.UUID(as_uuid=True),
        existing_nullable=True,
        postgresql_using="NULL",
    )
