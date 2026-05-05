"""Source-document → :class:`KnowledgeClaim` extractor.

The pre-claim pipeline treated each source document as a single
``Improvement(kind='knowledge_note')`` that the synth engine would
later try to digest into an article. That meant the LLM saw raw
mixed signal (architecture decisions, runbook steps, meeting notes,
jokes, stale bits from two years ago) all bundled together and had
to do signal extraction *and* article-level reconciliation in one
pass — which is why operators saw 50 docs collapse to 4 articles
with 43/74 notes ignored as "skip".

This module separates those concerns. It does **signal extraction
only**: read one source item body, ask an LLM to produce a list of
atomic claims, persist each as a :class:`KnowledgeClaim` row with
``status='active'``. Reconciliation (dedupe / supersede / contradict-
detect) lives in the next phase and operates on the claim store
after this module has finished.

Note: the existing :mod:`backend.app.services.knowledge_extractor`
operates on resolved-clarification Q&A pairs and emits
``KnowledgeAtom`` records for the legacy harvester. It is unrelated
to this module — both can coexist while the old pipeline runs in
parallel behind a feature flag.

Why a separate cheap LLM call per item instead of inline in the
ingestion path:

- Source ingestion is bandwidth-bound (Notion / GitHub fetches).
  Bolting an LLM call onto every fetch would multiply the latency
  budget per page and starve the rest of the sync.
- Idempotency. We hash ``body_md`` and skip un-changed items, so a
  re-run of the cron is free for items the operator hasn't touched.
- Extractor-vendor / model can change without touching ingestion.

Caps + safety:

- ``_MAX_BODY_CHARS`` clamps the body sent to the LLM. A 100k-char
  Notion runbook turns extraction into a per-call cost spike with
  diminishing returns; the synthesis-stage rendering still has
  access to the full body via ``source_item.body_md``.
- ``_MAX_CLAIMS_PER_ITEM`` clamps how many claims one document can
  emit. Hub pages with 200 bullet items would otherwise flood the
  store with low-signal claims.
- Extractor failure is per-item: one malformed LLM response or one
  oversized body marks ``extracted_at = now()`` with zero claims and
  moves on. The next ingestion that *changes* ``body_md_sha`` will
  trigger a re-try.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_memory import (
    ClaimKind,
    ClaimStatus,
    KnowledgeClaim,
    KnowledgeSourceItem,
)
from backend.app.services.agent.client import AgentClient, ChatMessage
from backend.app.services.agent.embedding import embed_text


log = logging.getLogger(__name__)


# Trim very long source bodies. Long-tail Notion / Confluence runbooks
# would otherwise blow the per-call token budget without improving
# extraction quality much (LLMs hit diminishing returns past ~10k
# chars of mixed-purpose prose).
_MAX_BODY_CHARS = 24_000

# Per-document fan-out cap. Pages with 200 bullet items would flood
# the store with low-signal "Bob said X" claims and starve the
# reconciliation engine.
_MAX_CLAIMS_PER_ITEM = 30

# Per-cron-tick batch — how many un-extracted items the worker pulls
# per workspace. Bounded so a 5000-doc backfill doesn't single-thread
# a tick.
_EXTRACTOR_BATCH_LIMIT = 25


_SYSTEM_PROMPT = """\
You extract atomic, verifiable claims from a workspace document and
discard noise. The output feeds a knowledge base that operators and
agents will search to answer questions about the system.

Rules:

- One claim per output item. Claims must be self-contained sentences
  that can stand without the surrounding document.
- DROP noise: meeting attendance, "who said what" gossip, personal
  status, scheduling chatter, "let's circle back" notes.
- DROP claims that are explicitly outdated by the document itself
  ("we used to do X but switched to Y" → emit only the Y claim).
- PREFER concrete decisions, rules, facts, and examples. If the
  document is *only* noise, return zero claims.
- Tag each claim with one or more topic_tags — short kebab-case
  topical labels (e.g. ``linear-fsm``, ``oauth-flow``,
  ``runbooks-deploy``). Topic tags are free-form, NOT a fixed list.
- Pick the closest ``kind`` from: fact, rule, decision, example,
  glossary, other.

Output ONLY a JSON object of shape:

  {"claims":[
    {"text": "...", "kind": "fact|rule|decision|example|glossary|other",
     "topic_tags": ["tag1","tag2"]},
    ...
  ]}

No commentary, no markdown fences, just JSON.
"""


@dataclass(slots=True)
class ExtractionReport:
    """Outcome of one extractor pass over a source item."""

    source_item_id: uuid.UUID
    claims_created: int = 0
    claims_skipped_duplicate: int = 0
    skipped_no_body: bool = False
    skipped_unchanged: bool = False
    llm_failed: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BatchReport:
    """Outcome of one extractor batch over a workspace's pending items."""

    inspected: int = 0
    extracted: int = 0
    skipped_unchanged: int = 0
    skipped_no_body: int = 0
    failed: int = 0
    claims_created: int = 0


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _excerpt(body: str, max_chars: int = 400) -> str:
    """Tight body excerpt for the claim's ``source_links`` payload.

    Search results show this excerpt next to the source link so the
    operator can eyeball provenance without re-opening the doc.
    """
    body = (body or "").strip()
    if len(body) <= max_chars:
        return body
    return body[: max_chars - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# LLM call + parse
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ExtractedClaim:
    text: str
    kind: str
    topic_tags: tuple[str, ...]


async def _call_extractor_llm(
    *,
    client: AgentClient,
    title: str,
    body_md: str,
) -> list[_ExtractedClaim]:
    """Invoke the LLM, parse the JSON response, return validated claims.

    Returns an empty list rather than raising on malformed responses —
    one bad doc shouldn't fail the whole batch. Validation rejects
    individual claims with empty text, unknown ``kind``, or non-list
    ``topic_tags`` rather than dropping the whole batch.
    """
    user_msg = (
        f"# {title}\n\n{body_md[:_MAX_BODY_CHARS]}"
        + (
            "\n\n[document truncated for extraction]"
            if len(body_md) > _MAX_BODY_CHARS
            else ""
        )
    )
    raw = await client.acomplete(
        messages=[
            ChatMessage(role="system", content=_SYSTEM_PROMPT),
            ChatMessage(role="user", content=user_msg),
        ],
        max_tokens=2000,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    return _parse_extractor_json(raw)


def _parse_extractor_json(raw: str) -> list[_ExtractedClaim]:
    """Tolerant parser for the extractor's JSON envelope.

    Handles the two common LLM misbehaviours:
    - wrapping the JSON in a markdown code fence
    - producing a top-level array instead of ``{"claims": [...]}``.
    """
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].lstrip()
        cleaned = cleaned.strip("`").strip()
    try:
        obj: Any = json.loads(cleaned)
    except json.JSONDecodeError:
        return []
    raw_items = obj.get("claims") if isinstance(obj, dict) else obj
    if not isinstance(raw_items, list):
        return []
    out: list[_ExtractedClaim] = []
    for entry in raw_items[:_MAX_CLAIMS_PER_ITEM]:
        if not isinstance(entry, dict):
            continue
        text_val = str(entry.get("text") or "").strip()
        if not text_val:
            continue
        kind = str(entry.get("kind") or ClaimKind.OTHER).strip().lower()
        if kind not in ClaimKind.ALL:
            kind = ClaimKind.OTHER
        tags_raw = entry.get("topic_tags") or []
        if not isinstance(tags_raw, list):
            tags_raw = []
        tags = tuple(
            sorted(
                {
                    str(tag).strip().lower()
                    for tag in tags_raw
                    if isinstance(tag, str) and tag.strip()
                }
            )
        )
        out.append(_ExtractedClaim(text=text_val, kind=kind, topic_tags=tags))
    return out


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def _persist_claim(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    item: KnowledgeSourceItem,
    claim: _ExtractedClaim,
    extracted_at: datetime,
    settings: Settings,
) -> bool:
    """Insert a claim row, or no-op if the (ws, sha) pair already exists.

    Returns True when a new row was created, False on unique-collision
    (the row was already in the store from a prior extraction). The
    ``source_links`` payload of the existing row is intentionally not
    updated here; the reconciliation engine in the next phase decides
    whether two extractions referring to the same exact text should
    merge their provenance — that interacts with confidence
    accumulation we don't want to settle inline.

    Embedding is best-effort: a missing API key or rate-limit hiccup
    leaves ``embedding=NULL`` and search falls back to keyword for
    that one row, instead of poisoning the whole batch.
    """
    sha = _sha256(claim.text)
    existing = (
        await session.execute(
            select(KnowledgeClaim).where(
                KnowledgeClaim.workspace_id == workspace_id,
                KnowledgeClaim.claim_md_sha == sha,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Stamp last_seen_at so decay knows the source still confirms
        # this claim, even though we didn't insert a new row.
        existing.last_seen_at = extracted_at
        return False

    embedding: list[float] | None
    try:
        embedding = await embed_text(claim.text, settings=settings)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info(
            "claim_extractor: embedding failed for sha=%s (%s) — "
            "row stored without embedding, search will fall back to "
            "keyword for this claim",
            sha[:12],
            exc,
        )
        embedding = None

    row = KnowledgeClaim(
        workspace_id=workspace_id,
        claim_md=claim.text,
        claim_md_sha=sha,
        embedding=embedding,
        topic_tags=list(claim.topic_tags),
        kind=claim.kind,
        status=ClaimStatus.ACTIVE,
        confidence=1.0,
        source_links=[
            {
                "source_item_id": str(item.id),
                "external_url": item.external_url,
                "title": item.title,
                "excerpt": _excerpt(item.body_md or ""),
                "extracted_at": extracted_at.isoformat(),
            }
        ],
        first_seen_at=extracted_at,
        last_seen_at=extracted_at,
    )
    session.add(row)
    return True


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def extract_claims_for_item(
    session: AsyncSession,
    *,
    item: KnowledgeSourceItem,
    llm_client: AgentClient,
    settings: Settings | None = None,
) -> ExtractionReport:
    """Run the extractor against one source item and persist its claims.

    Caller owns the transaction. The function flushes between rows so
    a partial batch failure mid-loop still leaves the successfully
    extracted claims in the session, which the caller can commit or
    roll back atomically.
    """
    settings = settings or get_settings()
    report = ExtractionReport(source_item_id=item.id)

    body = (item.body_md or "").strip()
    if not body:
        report.skipped_no_body = True
        return report

    body_sha = _sha256(body)
    if (
        item.body_md_sha == body_sha
        and item.extracted_at is not None
    ):
        report.skipped_unchanged = True
        return report

    try:
        claims = await _call_extractor_llm(
            client=llm_client, title=item.title, body_md=body
        )
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "claim_extractor: LLM call failed for item %s", item.id
        )
        report.llm_failed = True
        report.errors.append(str(exc))
        # Stamp body_md_sha so we don't redo the failed extraction
        # on every tick — the operator can force a retry by editing
        # the source row's ``extracted_at = NULL``.
        item.body_md_sha = body_sha
        item.extracted_at = datetime.now(timezone.utc)
        return report

    extracted_at = datetime.now(timezone.utc)
    for claim in claims:
        try:
            created = await _persist_claim(
                session,
                workspace_id=item.workspace_id,
                item=item,
                claim=claim,
                extracted_at=extracted_at,
                settings=settings,
            )
        except Exception as exc:  # noqa: BLE001 — per-row best-effort
            log.exception(
                "claim_extractor: persist failed for item %s claim %r",
                item.id,
                claim.text[:80],
            )
            report.errors.append(str(exc))
            continue
        if created:
            report.claims_created += 1
        else:
            report.claims_skipped_duplicate += 1

    item.body_md_sha = body_sha
    item.extracted_at = extracted_at
    await session.flush()
    return report


async def extract_pending_workspace(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    llm_client: AgentClient,
    limit: int = _EXTRACTOR_BATCH_LIMIT,
    settings: Settings | None = None,
) -> BatchReport:
    """Run the extractor over un-extracted items in one workspace.

    "Un-extracted" = ``body_md`` is set AND
    (``extracted_at IS NULL`` OR the body has changed since last
    extraction, detected by ``body_md_sha`` mismatch).
    """
    settings = settings or get_settings()
    report = BatchReport()

    rows = (
        await session.execute(
            select(KnowledgeSourceItem)
            .where(KnowledgeSourceItem.workspace_id == workspace_id)
            .where(KnowledgeSourceItem.body_md.isnot(None))
            .where(KnowledgeSourceItem.deleted_at.is_(None))
            .order_by(KnowledgeSourceItem.last_seen_at.desc().nullslast())
            .limit(limit)
        )
    ).scalars().all()
    report.inspected = len(rows)

    for item in rows:
        item_report = await extract_claims_for_item(
            session, item=item, llm_client=llm_client, settings=settings
        )
        if item_report.skipped_no_body:
            report.skipped_no_body += 1
            continue
        if item_report.skipped_unchanged:
            report.skipped_unchanged += 1
            continue
        if item_report.llm_failed:
            report.failed += 1
            continue
        report.extracted += 1
        report.claims_created += item_report.claims_created

    return report


__all__ = [
    "BatchReport",
    "ExtractionReport",
    "extract_claims_for_item",
    "extract_pending_workspace",
]
