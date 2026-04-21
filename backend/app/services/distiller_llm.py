"""LLM-backed classifier for the Distiller pipeline (Phase 6b).

This module plugs into :func:`backend.app.services.distiller.run_distiller`
via the ``classifier`` parameter. It asks a fast chat model to
classify the incoming blob as ``new`` / ``update`` / ``skip`` and
to pick a taxonomy slot (slug + title).

Design notes:

* We only show the model the **published** articles for the target
  bucket, with a short excerpt (first ~600 chars) — enough signal
  to recognise "this extends an existing article" without blowing
  the context window. Bucket scope isolation keeps the prompt
  small and keeps cross-bucket bleed out of the decision.
* We use JSON mode (``response_format={"type": "json_object"}``)
  on OpenAI; for Anthropic (which doesn't enforce JSON mode)
  :func:`_salvage_json_object` best-effort extracts braces from
  prose, matching the convention already used in
  :mod:`backend.app.services.agent.topic`.
* The classifier is considered untrusted: whatever it returns is
  rechecked by :func:`_reconcile_classification` in ``distiller``
  before the write path runs, so a bad verdict can at worst cause
  a reclassification (never an incorrect supersede).
* On any API error / malformed JSON, we raise — the Distiller's
  outer ``try`` catches and falls back to the stub classifier,
  so ingest never hard-fails just because the model is flaky.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    DistillerRunDecision,
    KnowledgeBucket,
)
from backend.app.services.agent.client import AgentClient, ChatMessage
from backend.app.services.distiller import (
    Classification,
    DistillerInput,
    _derive_slug,
    _slugify,
)


logger = logging.getLogger(__name__)


# Hard cap on how many existing articles we ever show the model.
# Phase 6b targets single-owner buckets; larger buckets should
# get a retrieval prefilter (Phase 6d). Keeps prompt cost bounded.
_MAX_CANDIDATES = 20

# Per-article excerpt budget + full-body budget. Rough token
# budget for gpt-4o-mini: these caps + the instructions fit
# comfortably in ~3-4k tokens.
_EXCERPT_CHARS = 600
_BODY_CHARS = 6000


def _truncate(text: str, limit: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "\u2026"


def _salvage_json_object(raw: str) -> dict[str, Any]:
    """Best-effort JSON extraction for models that wrap JSON in prose."""
    if not raw:
        return {}
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}


async def _load_candidates(
    session: AsyncSession, *, bucket_id: uuid.UUID
) -> list[BucketArticle]:
    """Return up to ``_MAX_CANDIDATES`` published articles, newest first."""
    rows = (
        await session.execute(
            select(BucketArticle)
            .where(
                BucketArticle.bucket_id == bucket_id,
                BucketArticle.status == BucketArticleStatus.PUBLISHED,
            )
            .order_by(BucketArticle.updated_at.desc())
            .limit(_MAX_CANDIDATES)
        )
    ).scalars().all()
    return list(rows)


def _build_prompt(
    bucket: KnowledgeBucket,
    candidates: list[BucketArticle],
    inp: DistillerInput,
) -> list[ChatMessage]:
    """Compose the single-turn classifier prompt."""
    lines: list[str] = []
    lines.append(
        "You are the Ship Distiller classifier. You categorise inbound "
        "knowledge blobs (PR summaries, pasted notes, connector fetches, "
        "audio transcripts) into a bucket's article taxonomy.\n"
    )
    lines.append(
        f"Bucket:\n"
        f"  slug: {bucket.slug}\n"
        f"  name: {bucket.name}\n"
        f"  scope: {bucket.scope_kind}\n"
        f"  source: {bucket.source_kind}\n"
        f"  description: {_truncate(bucket.description or '', 600)}\n"
    )

    if candidates:
        lines.append("Existing published articles (most recent first):\n")
        for art in candidates:
            excerpt = _truncate(art.body_md or "", _EXCERPT_CHARS)
            lines.append(
                f"- slug={art.slug!r} | v{art.version} | title="
                f"{art.title!r}\n  {excerpt}\n"
            )
    else:
        lines.append("Existing published articles: (none)\n")

    slug_hint = inp.slug_hint or ""
    title_hint = inp.title_hint or ""
    lines.append("Incoming blob:\n")
    lines.append(
        f"  source_kind: {inp.source_kind}\n"
        f"  title_hint: {title_hint!r}\n"
        f"  slug_hint: {slug_hint!r}\n"
        f"  body_md (may be truncated):\n"
        f"---\n{_truncate(inp.body_md, _BODY_CHARS)}\n---\n"
    )

    lines.append(
        "Decide exactly one of:\n"
        "  - \"new\": materially different from every existing article.\n"
        "  - \"update\": this blob extends / refines / supersedes an "
        "existing article. You MUST set `target_slug` to that article's "
        "slug.\n"
        "  - \"skip\": the blob adds no information beyond what is "
        "already published. Fill `reason`.\n"
        "\n"
        "Pick a clean, kebab-case `slug` (3-60 chars). For `update`, "
        "re-use the existing article's slug. For `new`, mint a slug "
        "that reads well in a URL and is not an exact duplicate of an "
        "existing one.\n"
        "\n"
        "Respond with a single JSON object, no prose:\n"
        "{\n"
        '  "decision": "new" | "update" | "skip",\n'
        '  "slug": "kebab-case-slug",\n'
        '  "title": "Human-readable title",\n'
        '  "target_slug": "existing-slug-or-null",\n'
        '  "reason": "short human-readable note (required for skip)",\n'
        '  "reasoning": "one-sentence rationale for audit"\n'
        "}\n"
    )
    return [ChatMessage(role="user", content="".join(lines))]


def _coerce_decision(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    lower = value.strip().lower()
    if lower in {
        DistillerRunDecision.NEW,
        DistillerRunDecision.UPDATE,
        DistillerRunDecision.SKIP,
    }:
        return lower
    return None


async def classify_with_llm(
    session: AsyncSession,
    bucket: KnowledgeBucket,
    inp: DistillerInput,
    *,
    client: AgentClient,
    model: str | None = None,
) -> Classification:
    """LLM-backed classifier.

    Raises on transport error or unparseable JSON — the caller
    catches and falls back to the stub classifier.
    """
    if not inp.body_md.strip():
        # Don't burn a model call on obviously empty input — the
        # reconciler in ``distiller`` would override anyway.
        return Classification(
            decision=DistillerRunDecision.SKIP,
            slug=_derive_slug(inp),
            reason="empty body",
            name="llm",
            reasoning="bypassed model: empty body",
        )

    candidates = await _load_candidates(session, bucket_id=bucket.id)
    messages = _build_prompt(bucket, candidates, inp)

    raw = await client.acomplete(
        messages,
        model=model,
        max_tokens=400,
        temperature=0.1,
        response_format={"type": "json_object"},
    )

    try:
        payload: dict[str, Any] = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        payload = _salvage_json_object(str(raw or ""))

    if not payload:
        raise ValueError("LLM classifier returned no parseable JSON")

    decision = _coerce_decision(payload.get("decision"))
    if decision is None:
        raise ValueError(
            f"LLM classifier returned unknown decision: {payload.get('decision')!r}"
        )

    raw_slug = str(payload.get("slug") or "").strip()
    slug = _slugify(raw_slug) if raw_slug else _derive_slug(inp)
    if not slug:
        slug = _derive_slug(inp)

    title_raw = payload.get("title")
    title = (
        title_raw.strip()[:512]
        if isinstance(title_raw, str) and title_raw.strip()
        else None
    )

    target_slug = payload.get("target_slug")
    target_article: BucketArticle | None = None
    if decision == DistillerRunDecision.UPDATE:
        if isinstance(target_slug, str) and target_slug.strip():
            for cand in candidates:
                if cand.slug == target_slug.strip():
                    target_article = cand
                    # Force the write path to re-use the target's
                    # slug — models occasionally pick a new slug
                    # *and* flag update, which would orphan the old
                    # row under a stale slug.
                    slug = cand.slug
                    break

    reason_raw = payload.get("reason")
    reason = (
        reason_raw.strip()[:512]
        if isinstance(reason_raw, str) and reason_raw.strip()
        else None
    )

    reasoning_raw = payload.get("reasoning")
    reasoning = (
        reasoning_raw.strip()[:1024]
        if isinstance(reasoning_raw, str) and reasoning_raw.strip()
        else None
    )

    return Classification(
        decision=decision,
        slug=slug,
        title=title,
        target_article=target_article,
        reason=reason,
        reasoning=reasoning,
        name="llm",
        extras={"vendor": getattr(client, "vendor", "unknown")},
    )


def make_llm_classifier(
    client: AgentClient, *, model: str | None = None
):
    """Adapt :func:`classify_with_llm` to the ``Classifier`` protocol.

    Returns a bound callable that matches ``Classifier`` so it can
    be handed straight to :func:`run_distiller`.
    """

    async def _classify(
        session: AsyncSession,
        bucket: KnowledgeBucket,
        inp: DistillerInput,
    ) -> Classification:
        return await classify_with_llm(
            session, bucket, inp, client=client, model=model
        )

    _classify.__name__ = "classify_with_llm"
    return _classify


# Permissive slug validator used only in logs / tests; the real
# slug sanitiser is ``distiller._slugify``.
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,118}[a-z0-9]$")


__all__ = [
    "classify_with_llm",
    "make_llm_classifier",
]
