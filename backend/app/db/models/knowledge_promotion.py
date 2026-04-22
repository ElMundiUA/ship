"""Dedup cluster cache for workspace-knowledge promotion (RFC-0008 §I, PR-7B).

Sits between the raw :class:`BucketArticle` corpus and the
operator-facing promotion flow:

1. :func:`backend.app.services.knowledge_dedup.rebuild_candidates`
   clusters repo-scope articles whose embeddings are mutually close
   (cosine similarity ≥ threshold, drawn from ≥ 2 distinct repos).
2. Each resulting cluster becomes one :class:`KnowledgePromotionCandidate`
   row. ``fingerprint`` is a deterministic SHA of the sorted article
   ids so the same cluster upserts in place across refreshes.
3. ``GET /knowledge/candidates`` reads the TTL-fresh rows back;
   ``POST /knowledge/candidates/refresh`` forces a rebuild.

Rows are intentionally ephemeral — the source of truth is the live
:class:`BucketArticle` corpus; this table is a *cache*. We let
``ttl_expires_at`` expire cached clusters and recompute instead of
trying to incrementally maintain the clustering, which is a
harder problem than the MVP's "≤ 500 articles, a few operators"
budget warrants.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701
    _ts_created,  # noqa: PLC2701
)


class KnowledgePromotionCandidate(Base):
    """One cached dedup cluster for a workspace.

    ``article_ids`` is the ordered (sort-by-uuid-string) list of
    :class:`BucketArticle.id` strings that make up the cluster. The
    first entry doubles as a "representative" for the slug_hint
    fallback when no member bucket carries a slug.
    """

    __tablename__ = "knowledge_promotion_candidates"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "fingerprint",
            name="uq_knowledge_promotion_candidates_fp",
        ),
        Index(
            "ix_knowledge_promotion_candidates_ws_ttl",
            "workspace_id",
            "ttl_expires_at",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    article_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    slug_hint: Mapped[str] = mapped_column(Text, nullable=False)
    centroid_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = _ts_created()
    ttl_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


__all__ = ["KnowledgePromotionCandidate"]
