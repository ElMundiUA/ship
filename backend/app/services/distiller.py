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
  under the same ``content_sha``, or the LLM decided the blob
  adds no information).

Phase 6a introduced the stub classifier (deterministic, no LLM)
and the end-to-end plumbing. Phase 6b (this module) generalises
the dispatch: :func:`run_distiller` now takes an optional
``classifier`` callable and defaults to the stub. The LLM impl
lives in :mod:`backend.app.services.distiller_llm` and is
selected by the HTTP layer.

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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

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
    classifier: str = "stub"


@dataclass(slots=True)
class Classification:
    """Classifier verdict for one input.

    All write-path branching in :func:`run_distiller` is driven
    off this record. The stub and the LLM both produce instances
    of it, so the write path stays single-source-of-truth.

    Fields:
      * ``decision`` — one of
        :class:`backend.app.db.models.agent_memory.DistillerRunDecision`
        (excluding ``error``).
      * ``slug`` — final slug to write under. The classifier is
        free to override the caller-provided hint (typical for
        an LLM that picks a cleaner taxonomy).
      * ``title`` — final title. ``None`` means "derive from body".
      * ``target_article`` — existing published row that
        ``update`` supersedes; ``None`` for ``new`` / ``skip``.
      * ``reason`` — short, user-facing explanation for ``skip``.
      * ``reasoning`` — classifier audit trail, stashed into the
        run's ``output_refs.classifier`` so operators can debug.
      * ``name`` — classifier implementation label ("stub"/"llm").
    """

    decision: str
    slug: str
    title: str | None = None
    target_article: BucketArticle | None = None
    reason: str | None = None
    reasoning: str | None = None
    name: str = "stub"
    extras: dict[str, Any] = field(default_factory=dict)


Classifier = Callable[
    [AsyncSession, KnowledgeBucket, "DistillerInput"],
    Awaitable[Classification],
]


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


async def _lookup_published(
    session: AsyncSession, *, bucket_id: uuid.UUID, slug: str
) -> BucketArticle | None:
    return (
        await session.execute(
            select(BucketArticle).where(
                BucketArticle.bucket_id == bucket_id,
                BucketArticle.slug == slug,
                BucketArticle.status == BucketArticleStatus.PUBLISHED,
            )
        )
    ).scalars().first()


async def classify_stub(
    session: AsyncSession,
    bucket: KnowledgeBucket,
    inp: "DistillerInput",
) -> Classification:
    """Deterministic classifier — no LLM.

    Slug is derived from the incoming hints only; update vs new
    is inferred from whether a published article already owns
    that slug; skip fires on empty bodies and unchanged content.
    This is the default classifier and also the fallback when
    the LLM variant refuses to produce a usable verdict.
    """
    slug = _derive_slug(inp)
    body = inp.body_md
    if not body.strip():
        return Classification(
            decision=DistillerRunDecision.SKIP,
            slug=slug,
            reason="empty body",
            name="stub",
        )

    current = await _lookup_published(session, bucket_id=bucket.id, slug=slug)
    if current is None:
        return Classification(
            decision=DistillerRunDecision.NEW, slug=slug, name="stub"
        )

    if current.content_sha == _sha256_hex(body):
        return Classification(
            decision=DistillerRunDecision.SKIP,
            slug=slug,
            target_article=current,
            reason="content already published under this slug",
            name="stub",
        )
    return Classification(
        decision=DistillerRunDecision.UPDATE,
        slug=slug,
        target_article=current,
        name="stub",
    )


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
    classifier: Classifier | None = None,
) -> DistillerOutcome:
    """Execute one ingest attempt end-to-end.

    Creates the :class:`DistillerRun` row, classifies, writes any
    article rows, and flushes. All side effects share the caller's
    transaction so a failure in the article insert rolls the run
    row back too.

    ``classifier`` defaults to :func:`classify_stub` (deterministic
    rules). The HTTP layer can inject :func:`classify_with_llm` from
    :mod:`backend.app.services.distiller_llm` to turn on the LLM
    path. Any classifier that raises falls back to the stub so
    ingest never hard-fails on a misbehaving model.
    """
    chosen = classifier or classify_stub
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
        try:
            cls = await chosen(session, bucket, inp)
        except Exception as exc:  # noqa: BLE001 — classifier is untrusted
            logger.warning(
                "distiller: classifier %r raised, falling back to stub (%s)",
                getattr(chosen, "__name__", chosen),
                exc,
            )
            cls = await classify_stub(session, bucket, inp)
            cls.reasoning = (
                f"fallback to stub after {type(exc).__name__}: {exc!s}"[:512]
            )

        cls = await _reconcile_classification(session, bucket, inp, cls)

        article_ids: list[uuid.UUID] = []
        diff: dict[str, Any] = {"slug": cls.slug}
        reason: str | None = cls.reason

        if cls.decision == DistillerRunDecision.SKIP:
            pass
        else:
            body = inp.body_md
            content_sha = _sha256_hex(body)
            new_version = await _next_version(
                session, bucket_id=bucket.id, slug=cls.slug
            )
            supersedes_id: uuid.UUID | None = None
            current = cls.target_article
            if cls.decision == DistillerRunDecision.UPDATE and current is not None:
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

            title = (cls.title or _derive_title(inp, cls.slug))[:512]
            article = BucketArticle(
                id=uuid.uuid4(),
                bucket_id=bucket.id,
                slug=cls.slug,
                title=title,
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

        run.decision = cls.decision
        run.status = DistillerRunStatus.DONE
        run.finished_at = datetime.now(timezone.utc)
        run.output_refs = {
            "article_ids": [str(a) for a in article_ids],
            "diff": diff,
            "reason": reason,
            "classifier": {
                "name": cls.name,
                "reasoning": cls.reasoning,
                **({"extras": cls.extras} if cls.extras else {}),
            },
        }
        await session.flush()

        return DistillerOutcome(
            run_id=run.id,
            decision=cls.decision,
            article_ids=article_ids,
            status=run.status,
            reason=reason,
            classifier=cls.name,
        )
    except Exception as exc:  # noqa: BLE001 — want the row to record error
        run.status = DistillerRunStatus.FAILED
        run.decision = DistillerRunDecision.ERROR
        run.error = str(exc)[:4000]
        run.finished_at = datetime.now(timezone.utc)
        await session.flush()
        raise


async def _reconcile_classification(
    session: AsyncSession,
    bucket: KnowledgeBucket,
    inp: DistillerInput,
    cls: Classification,
) -> Classification:
    """Validate a classifier verdict and tighten it against reality.

    LLM classifiers can hallucinate: pick a slug that doesn't exist
    for ``update``, or claim ``new`` for a slug that's already
    published. This guard rails the verdict so the write path only
    ever sees consistent state:

    * empty body → force SKIP (the LLM has no business overriding
      "nothing to ingest").
    * UPDATE with no / unknown target → either look up by slug or
      demote to NEW.
    * NEW with an already-published slug → bump to UPDATE against
      that row.
    * Missing / empty slug → fall back to the hint-derived slug.
    """
    slug = (cls.slug or "").strip() or _derive_slug(inp)
    cls.slug = _slugify(slug) or _derive_slug(inp)

    if not inp.body_md.strip():
        cls.decision = DistillerRunDecision.SKIP
        cls.reason = cls.reason or "empty body"
        cls.target_article = None
        return cls

    if cls.decision == DistillerRunDecision.UPDATE and cls.target_article is None:
        candidate = await _lookup_published(
            session, bucket_id=bucket.id, slug=cls.slug
        )
        if candidate is None:
            cls.decision = DistillerRunDecision.NEW
        else:
            cls.target_article = candidate

    if cls.decision == DistillerRunDecision.NEW:
        existing = await _lookup_published(
            session, bucket_id=bucket.id, slug=cls.slug
        )
        if existing is not None:
            if existing.content_sha == _sha256_hex(inp.body_md):
                cls.decision = DistillerRunDecision.SKIP
                cls.target_article = existing
                cls.reason = (
                    cls.reason
                    or "content already published under this slug"
                )
            else:
                cls.decision = DistillerRunDecision.UPDATE
                cls.target_article = existing

    if cls.decision == DistillerRunDecision.UPDATE and cls.target_article:
        if cls.target_article.content_sha == _sha256_hex(inp.body_md):
            cls.decision = DistillerRunDecision.SKIP
            cls.reason = (
                cls.reason or "content already published under this slug"
            )

    return cls


__all__ = [
    "Classification",
    "Classifier",
    "DistillerInput",
    "DistillerOutcome",
    "classify_stub",
    "run_distiller",
]
