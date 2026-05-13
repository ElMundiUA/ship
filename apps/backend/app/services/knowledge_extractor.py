"""LLM extractor for the Phase-1b knowledge-note pipeline (ELS-35 / KB-1b).

Replaces Phase-1a's identity extractor (answer-text passthrough). The
extractor takes a knowledge **source** (today: a resolved Clarification;
follow-ups: chat-pack events, inbox-comment events) and asks a fast
model to surface 0..N **atomic knowledge facts** other agents would
benefit from knowing.

Output shape:

    [
      KnowledgeAtom(
        title="Linear adapter — `ready:*` is a namespace, not allowlist",
        body=("`ready:*` labels are treated as one open namespace ..."
              "Tracker writes only land via the finish endpoint."),
        bucket_hint="architecture-decisions",
      ),
      ...
    ]

Each atom becomes one ``Improvement(kind='knowledge_note')`` row in
the harvest pass. KB-2 (routing) reads ``bucket_hint`` as a tiebreaker
when its centroid score is ambiguous.

Behaviour contract:

- **Best-effort.** If the LLM client is unconfigured (no
  ``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``) or any call fails,
  return ``None`` so the caller can fall back to the identity
  extractor. Never raise out of this module — the harvester runs
  hourly and one bad model response must not stall the cron.
- **Bounded.** Hard cap on atoms (``_MAX_ATOMS``) and per-atom body
  length so a chatty model can't fill the buckets table with
  thousand-word "atoms".
- **Bucket-aware.** The prompt is given the workspace's bucket
  catalogue (slug + description) so ``bucket_hint`` falls within
  that vocabulary; LLM-named slugs that don't exist are dropped to
  ``None`` post-hoc rather than fabricated.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import BucketScope, KnowledgeBucket
from backend.app.services.agent.client import AgentClient, ChatMessage


logger = logging.getLogger(__name__)


_MAX_ATOMS = 6
_TITLE_CAP = 240
_BODY_CAP = 4000
_QUESTION_PROMPT_CAP = 4000
_ANSWER_PROMPT_CAP = 8000
_DEFAULT_MODEL = "gpt-4o-mini"  # cheap, JSON-mode capable


@dataclass(slots=True)
class KnowledgeAtom:
    title: str
    body: str
    bucket_hint: str | None


@dataclass(slots=True)
class ClarificationSource:
    """The Q&A pair the extractor sees. Decoupled from
    :class:`Clarification` so the extractor can be reused by chat-pack /
    inbox-comment paths in follow-up tickets without a circular import."""

    question: str
    answer: str
    ticket_ref: str | None = None


async def extract_clarification_atoms(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    source: ClarificationSource,
    client: AgentClient | None,
    model: str | None = None,
) -> list[KnowledgeAtom] | None:
    """Run the LLM against one resolved clarification.

    Returns ``None`` to signal "fall back to identity extractor"; an
    empty list means "the model decided this Q&A holds no general-
    purpose knowledge atoms" (which is a valid outcome — not every
    operator answer is reusable).
    """
    if client is None:
        return None

    answer = (source.answer or "").strip()
    if not answer:
        return []

    bucket_catalogue = await _bucket_catalogue(session, workspace_id=workspace_id)
    prompt = _build_prompt(source=source, buckets=bucket_catalogue)

    try:
        raw = await client.acomplete(
            messages=[
                ChatMessage(role="system", content=_SYSTEM_PROMPT),
                ChatMessage(role="user", content=prompt),
            ],
            model=model or _DEFAULT_MODEL,
            max_tokens=2000,
            temperature=0.0,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.info("knowledge_extractor: LLM call failed (%s); fallback identity", exc)
        return None

    try:
        parsed = _parse_atoms(raw, allowed_slugs={b.slug for b in bucket_catalogue})
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.info(
            "knowledge_extractor: malformed LLM output (%s); fallback identity",
            exc,
        )
        return None

    return parsed


# ---------------------------------------------------------------------------
# Prompt machinery
# ---------------------------------------------------------------------------


_SYSTEM_PROMPT = """You extract reusable knowledge from a Q&A exchange that an
operator used to answer an agent's clarification question on a Ship workspace
ticket. Your job is to surface zero or more **atomic knowledge facts** that
*future agents working on unrelated tickets* would benefit from knowing.

Rules:
- Each atom must be a self-contained statement, not a recap of the specific
  ticket. Generalise: turn "for ELS-27 we treat ready:* as namespace" into
  "in this workspace, ready:* labels are a namespace, not an allowlist".
- Skip the ticket itself — answers that are purely "yes" / "do it now" /
  "see PR #N" produce **zero** atoms. Better an empty list than fabricated
  generalisations.
- Title: imperative or declarative, ≤240 chars, no trailing punctuation.
- Body: 2-12 sentences of Markdown. Include the *why* not just the *what*.
- bucket_hint: pick the slug from the catalogue below that best fits, or
  null if none does. Don't invent slugs.

Return strictly:
{"atoms": [{"title": "...", "body": "...", "bucket_hint": "slug-or-null"}, ...]}

If the Q&A holds no reusable knowledge, return {"atoms": []}.
"""


@dataclass(slots=True, frozen=True)
class _BucketRow:
    slug: str
    name: str
    description: str | None


async def _bucket_catalogue(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> list[_BucketRow]:
    """All workspace-scoped non-archived buckets — what the model picks from.

    Repo-scoped buckets are excluded on purpose: KB-2 routes only into
    workspace buckets (and KB-5 will remove repo scope as a class).
    """
    rows = (
        await session.execute(
            select(KnowledgeBucket.slug, KnowledgeBucket.name, KnowledgeBucket.description)
            .where(KnowledgeBucket.workspace_id == workspace_id)
            .where(KnowledgeBucket.scope_kind == BucketScope.WORKSPACE)
            .where(KnowledgeBucket.archived_at.is_(None))
            .order_by(KnowledgeBucket.slug)
        )
    ).all()
    return [_BucketRow(slug=r.slug, name=r.name, description=r.description) for r in rows]


def _build_prompt(*, source: ClarificationSource, buckets: Sequence[_BucketRow]) -> str:
    catalogue = "\n".join(
        f"- `{b.slug}` — {b.name}: {(b.description or '').strip() or '(no description)'}"
        for b in buckets
    ) or "- (no workspace buckets configured)"

    ticket_line = f"Ticket: {source.ticket_ref}\n" if source.ticket_ref else ""
    question = (source.question or "").strip()[:_QUESTION_PROMPT_CAP]
    answer = (source.answer or "").strip()[:_ANSWER_PROMPT_CAP]

    return (
        f"{ticket_line}"
        f"## Bucket catalogue\n{catalogue}\n\n"
        f"## Agent's question\n{question}\n\n"
        f"## Operator's answer\n{answer}"
    )


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def _parse_atoms(raw: str, *, allowed_slugs: set[str]) -> list[KnowledgeAtom]:
    """Strict-then-salvage JSON parse, then per-atom field validation."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        obj = _salvage_json_object(raw)

    atoms_in = obj.get("atoms")
    if not isinstance(atoms_in, list):
        return []

    out: list[KnowledgeAtom] = []
    for entry in atoms_in[:_MAX_ATOMS]:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()
        body = str(entry.get("body") or "").strip()
        if not title or not body:
            continue
        bucket_hint_raw = entry.get("bucket_hint")
        bucket_hint: str | None
        if isinstance(bucket_hint_raw, str) and bucket_hint_raw.strip() in allowed_slugs:
            bucket_hint = bucket_hint_raw.strip()
        else:
            bucket_hint = None
        out.append(
            KnowledgeAtom(
                title=title[:_TITLE_CAP],
                body=body[:_BODY_CAP],
                bucket_hint=bucket_hint,
            )
        )
    return out


def _salvage_json_object(raw: str) -> dict[str, Any]:
    """Best-effort {…} extraction for models that wrap JSON in prose."""
    match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


__all__ = [
    "ClarificationSource",
    "KnowledgeAtom",
    "extract_clarification_atoms",
]
