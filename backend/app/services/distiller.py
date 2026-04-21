"""Distiller — Phase 6 ingest pipeline for :class:`BucketArticle`.

The Distiller is the single write path every inbound knowledge
blob travels through on its way into ``bucket_articles``: PR
reviews, external uploads, connector-proxy snapshots, and audio
transcripts (Phase 7) all converge on the same surface. The
classifier answers one question per input:

- **new** — write a fresh article (new slug under the bucket).
- **update** — bump an existing article's version, flip the old
  row to ``superseded``, and insert the replacement as
  ``published``.
- **skip** — nothing to ingest (empty body, content already live
  under the same ``content_sha``).

Phase 6a (this module) implements the stub classifier: simple,
deterministic, no LLM. It's good enough to prove the plumbing
(queue row + article write + provenance trail) and to let the
console drive an ingest from the Knowledge page today. Phase 6b
replaces :func:`_classify` with an LLM call, Phase 6c wires the
inbound sources; both swap in without touching the endpoint
surface or the stored-row shape.

The module intentionally does not know about workspaces or
RBAC — the HTTP layer in ``v1/routes/distiller.py`` owns the
``_require_membership`` + ``_load_bucket`` enforcement and then
calls :func:`run_distiller` with the validated bucket row. Unit
tests exercise this layer directly against a session fixture.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    DistillerRun,
    DistillerRunDecision,
    DistillerRunStatus,
    KnowledgeBucket,
)
from backend.app.services.agent.embedding import embed_text


logger = logging.getLogger(__name__)


# Default slug when the caller doesn't provide a ``slug_hint`` and
# the body contains nothing we can slugify. Mirrors the
# ``bucket_repo_files_sync`` convention for single-article buckets.
_DEFAULT_SLUG = "distilled"


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DistillerInput:
    """Canonical shape for one ingest attempt.

    ``source_kind`` must be one of
    :class:`backend.app.db.models.agent_memory.BucketSource` (keeps
    provenance consistent with the bucket-side categorisation).
    ``provenance`` is merged into the article's ``provenance`` JSON
    so the stored row carries enough context for an audit walk.
    """

    body_md: str
    source_kind: str
    title_hint: str | None = None
    slug_hint: str | None = None
    provenance: dict[str, Any] | None = None
    input_ref: dict[str, Any] | None = None


@dataclass(slots=True)
class DistillerOutcome:
    """What the Distiller decided + persisted for one input."""

    run_id: uuid.UUID
    decision: str
    article_ids: list[uuid.UUID]
    status: str
    reason: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256_hex(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _slugify(value: str) -> str:
    out: list[str] = []
    for ch in value.lower().strip():
        if ch.isalnum():
            out.append(ch)
        elif ch in {" ", "-", "_", "/"}:
            out.append("-")
    slug = "".join(out).strip("-")
    # Collapse repeats so "foo---bar" → "foo-bar".
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug[:120]


def _derive_slug(inp: DistillerInput) -> str:
    if inp.slug_hint:
        cand = _slugify(inp.slug_hint)
        if cand:
            return cand
    if inp.title_hint:
        cand = _slugify(inp.title_hint)
        if cand:
            return cand
    # Fall back to a deterministic short-hash of the body so
    # repeated distills of the same blob don't create slug churn.
    digest = _sha256_hex(inp.body_md)[:12]
    return f"{_DEFAULT_SLUG}-{digest}" if inp.body_md else _DEFAULT_SLUG


def _derive_title(inp: DistillerInput, slug: str) -> str:
    if inp.title_hint:
        return inp.title_hint[:512]
    # First non-empty line of the body, stripped of markdown
    # heading markers. Keeps the audit view readable for the
    # common "PR summary" ingest without yet requiring an LLM.
    for raw in inp.body_md.splitlines():
        line = raw.strip().lstrip("#").strip()
        if line:
            return line[:512]
    return slug[:512]


async def _classify(
    session: AsyncSession,
    *,
    bucket: KnowledgeBucket,
    slug: str,
    body: str,
) -> tuple[str, BucketArticle | None]:
    """Phase 6a stub — deterministic rules, no LLM.

    Returns a ``(decision, current_published_article_or_None)``
    pair. The HTTP layer uses ``current`` to branch between
    insert-new and supersede-existing write paths.
    """
    if not body.strip():
        return DistillerRunDecision.SKIP, None

    current = (
        await session.execute(
            select(BucketArticle).where(
                BucketArticle.bucket_id == bucket.id,
                BucketArticle.slug == slug,
                BucketArticle.status == BucketArticleStatus.PUBLISHED,
            )
        )
    ).scalars().first()

    if current is None:
        return DistillerRunDecision.NEW, None

    if current.content_sha == _sha256_hex(body):
        # Same body already live — skip instead of churning versions.
        return DistillerRunDecision.SKIP, current
    return DistillerRunDecision.UPDATE, current


async def _next_version(
    session: AsyncSession, *, bucket_id: uuid.UUID, slug: str
) -> int:
    """Highest version across all rows for ``(bucket_id, slug)`` + 1.

    Walking ``max(version)`` instead of ``current.version + 1``
    makes the resurrection path (all prior rows archived) safe.
    """
    max_prev = (
        await session.execute(
            select(func.max(BucketArticle.version)).where(
                BucketArticle.bucket_id == bucket_id,
                BucketArticle.slug == slug,
            )
        )
    ).scalar()
    return (int(max_prev) if max_prev is not None else 0) + 1


async def _maybe_embed(body: str) -> list[float] | None:
    """Best-effort embedding.

    We do not want the Distiller to hard-fail when OPENAI_API_KEY
    is missing; the article is still useful for manual browsing
    and keyword search. The embedding just isn't populated and
    ``retrieve_buckets`` silently skips it (``embedding IS NOT
    NULL`` filter).
    """
    try:
        return await embed_text(body)
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.info("distiller: embedding skipped (%s)", exc)
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def run_distiller(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    bucket: KnowledgeBucket,
    actor_user_id: uuid.UUID | None,
    inp: DistillerInput,
) -> DistillerOutcome:
    """Execute one ingest attempt end-to-end.

    Creates the :class:`DistillerRun` row, classifies, writes any
    article rows, and flushes. All side effects share the caller's
    transaction so a failure in the article insert rolls the run
    row back too.
    """
    now = datetime.now(timezone.utc)
    run = DistillerRun(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        bucket_id=bucket.id,
        source_kind=inp.source_kind,
        status=DistillerRunStatus.RUNNING,
        decision=None,
        input_ref={
            **(inp.input_ref or {}),
            "title_hint": inp.title_hint,
            "slug_hint": inp.slug_hint,
            "bytes": len(inp.body_md.encode("utf-8")),
        },
        output_refs={},
        created_by_user_id=actor_user_id,
        started_at=now,
    )
    session.add(run)
    await session.flush()

    try:
        slug = _derive_slug(inp)
        decision, current = await _classify(
            session, bucket=bucket, slug=slug, body=inp.body_md
        )

        article_ids: list[uuid.UUID] = []
        diff: dict[str, Any] = {"slug": slug}
        reason: str | None = None

        if decision == DistillerRunDecision.SKIP:
            reason = (
                "empty body"
                if not inp.body_md.strip()
                else "content already published under this slug"
            )
        else:
            body = inp.body_md
            content_sha = _sha256_hex(body)
            new_version = await _next_version(
                session, bucket_id=bucket.id, slug=slug
            )
            supersedes_id: uuid.UUID | None = None
            if decision == DistillerRunDecision.UPDATE and current is not None:
                current.status = BucketArticleStatus.SUPERSEDED
                supersedes_id = current.id
                new_version = current.version + 1
                diff["previous_version"] = current.version
                diff["previous_article_id"] = str(current.id)

            provenance = {
                "source_kind": inp.source_kind,
                "distiller_run_id": str(run.id),
                **(inp.provenance or {}),
            }
            embedding = await _maybe_embed(body)

            article = BucketArticle(
                id=uuid.uuid4(),
                bucket_id=bucket.id,
                slug=slug,
                title=_derive_title(inp, slug),
                body_md=body,
                content_sha=content_sha,
                version=new_version,
                status=BucketArticleStatus.PUBLISHED,
                supersedes_id=supersedes_id,
                provenance=provenance,
                embedding=embedding,
            )
            session.add(article)
            await session.flush()
            article_ids.append(article.id)
            diff["new_article_id"] = str(article.id)
            diff["new_version"] = new_version

        run.decision = decision
        run.status = DistillerRunStatus.DONE
        run.finished_at = datetime.now(timezone.utc)
        run.output_refs = {
            "article_ids": [str(a) for a in article_ids],
            "diff": diff,
            "reason": reason,
        }
        await session.flush()

        return DistillerOutcome(
            run_id=run.id,
            decision=decision,
            article_ids=article_ids,
            status=run.status,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001 — want the row to record error
        run.status = DistillerRunStatus.FAILED
        run.decision = DistillerRunDecision.ERROR
        run.error = str(exc)[:4000]
        run.finished_at = datetime.now(timezone.utc)
        await session.flush()
        raise


__all__ = [
    "DistillerInput",
    "DistillerOutcome",
    "run_distiller",
]
