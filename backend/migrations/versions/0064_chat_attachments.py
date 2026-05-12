"""chat_attachments — image / PDF / text files attached to chat messages

Phase 3 of the Navigator overhaul adds multimodal input. The
operator drags a screenshot or a PDF into the chat widget, the
backend stores it under workspace-scoped storage (local disk in
v1, S3 via a follow-up env-toggle), and the LLM message builder
emits a vision / document block referencing the bytes.

The row carries enough metadata for two readers:

* The LLM-message builder, which needs ``kind`` / ``mime`` /
  ``storage_path`` to decide whether to inline as an image block,
  a Claude ``document`` block, or text-extracted prose.
* The console UI, which renders ``filename`` / ``size_bytes`` /
  ``kind`` thumbnails so the operator can see what they attached
  without re-opening their file picker.

``extracted_text`` is opt-in. We populate it inline for PDF (cheap)
and skip it for images in v1 — Claude's vision pathway reads the
pixels directly. A follow-up can wire in OCR (Tesseract or
Claude-vision-extract) to backfill ``extracted_text`` for images
so they become searchable.

ON DELETE CASCADE on ``message_id`` so a chat-thread purge takes
the attachments with it; the storage backend's GC sweeper
periodically reconciles orphans the other direction (file present,
row absent — happens if a multipart upload landed but the message
transaction rolled back).

Revision ID: 0064_chat_attachments
Revises: 0063_agent_provider
Create Date: 2026-05-12
"""

from __future__ import annotations

from typing import Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0064_chat_attachments"
down_revision: Union[str, None] = "0063_agent_provider"
branch_labels: Union[str, tuple[str, ...], None] = None
depends_on: Union[str, tuple[str, ...], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_attachments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "message_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_messages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Coarse type: drives which LLM content-block we emit.
        # Image → vision block; PDF → Claude document block (or
        # text-extracted on non-Claude providers); text → text block.
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("mime", sa.String(length=128), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        # Storage-backend-agnostic path. Local disk: ``file:///var/ship/...``;
        # S3 (future): ``s3://bucket/key``. Resolver in
        # ``attachment_service`` opens by scheme.
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        # Text we managed to pull out at upload time. PDF → pypdf;
        # text/markdown → identity; image → null (LLM vision reads
        # pixels directly). A future OCR pass can backfill this for
        # images so chat threads become text-searchable.
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extracted_text_source", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_check_constraint(
        "ck_chat_attachments_kind_enum",
        "chat_attachments",
        "kind IN ('image', 'pdf', 'text')",
    )
    op.create_index(
        "ix_chat_attachments_message_id",
        "chat_attachments",
        ["message_id"],
    )
    op.create_index(
        "ix_chat_attachments_workspace_id",
        "chat_attachments",
        ["workspace_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_attachments_workspace_id", table_name="chat_attachments")
    op.drop_index("ix_chat_attachments_message_id", table_name="chat_attachments")
    op.drop_constraint(
        "ck_chat_attachments_kind_enum",
        "chat_attachments",
        type_="check",
    )
    op.drop_table("chat_attachments")
