"""Internal observer that turns operator-resolved knowledge events into
``knowledge_note`` Improvement rows.

This is the Phase-1 ingest stage of the knowledge-pipeline (see Linear
ELS-33 / KB-anchor). Agents do **not** explicitly publish to buckets.
Ship itself watches the surfaces where high-density knowledge lands —
today only **resolved clarifications** — and drops a typed
:class:`Improvement` row per source. Phase 2 will route each row to a
workspace bucket; Phase 3 synthesises drafts; Phase 4 surfaces them
for operator review.

What "knowledge note" means for Phase 1a:

- Source: a :class:`Clarification` whose ``status='answered'`` and
  whose ``answer`` field is non-empty.
- Extractor: identity. The operator's answer text *is* the note body;
  the agent's question becomes part of the title and the source
  excerpt. No LLM call yet — that lands in KB-1b (ELS-35).
- Storage: a single :class:`Improvement` row with
  ``kind='knowledge_note'``. The ``context`` JSONB carries
  ``source_kind``, ``source_id``, ``source_excerpt``, ``ticket_ref``,
  and (initially null) ``routed_bucket_id`` / ``route_confidence``.

Idempotency: we skip a clarification whose id already appears as
``context->>'source_id'`` on a row with kind='knowledge_note' for
this workspace. No extra index — the harvest cron is hourly, so the
existence query is cheap and dodging a schema migration is worth the
linear scan over a few thousand notes.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_surface import Clarification, Improvement
from backend.app.db.models.tenancy import AuditLog, Workspace
from backend.app.services.agent.client import AgentClient
from backend.app.services.knowledge_extractor import (
    ClarificationSource,
    KnowledgeAtom,
    extract_clarification_atoms,
)


log = logging.getLogger(__name__)

NOTE_KIND = "knowledge_note"
SOURCE_KIND_CLARIFICATION = "clarification"

# Phase 1a uses identity extraction (answer text passed through). Cap
# the body the same way the operator-facing 'answer' field is capped
# server-side so we don't accidentally store a 50KB paste.
ANSWER_BODY_CAP = 8000
QUESTION_EXCERPT_CAP = 1000
TITLE_CAP = 240


@dataclass(slots=True)
class HarvestReport:
    """One workspace's harvest tick."""

    workspace_id: uuid.UUID
    inspected: int = 0
    created: int = 0
    skipped_duplicate: int = 0
    skipped_no_answer: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "workspace_id": str(self.workspace_id),
            "inspected": self.inspected,
            "created": self.created,
            "skipped_duplicate": self.skipped_duplicate,
            "skipped_no_answer": self.skipped_no_answer,
            "errors": self.errors,
        }


async def harvest_workspace(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    since: datetime | None = None,
    limit: int = 200,
    llm_client: AgentClient | None = None,
) -> HarvestReport:
    """Drop one or more knowledge-note Improvement rows per resolved clarification.

    ``since`` lets a backfill caller widen the window beyond the
    cron's "everything answered, ever" baseline (the existence query
    handles dedup either way). The cron passes ``since=None``.

    ``llm_client`` switches the extractor strategy:
      * non-None → KB-1b LLM extractor; the model emits 0..N atoms
        per source, each becomes one Improvement row tagged with
        ``extractor='llm_v1'`` + ``atom_idx``.
      * None → KB-1a identity extractor (one row per source, full
        answer text passthrough, ``extractor='identity_v1'``).
    LLM failures auto-fall-back to identity for that source so the
    cron never stalls on a flaky model.

    The function commits nothing — caller owns the transaction
    boundary so a partial failure mid-loop rolls back cleanly.
    """
    report = HarvestReport(workspace_id=workspace_id)

    stmt = (
        select(Clarification)
        .where(Clarification.workspace_id == workspace_id)
        .where(Clarification.status == "answered")
        .order_by(Clarification.answered_at.asc().nulls_last())
        .limit(limit)
    )
    if since is not None:
        stmt = stmt.where(Clarification.answered_at >= since)

    result = await session.execute(stmt)
    rows = list(result.scalars().all())
    report.inspected = len(rows)

    for clar in rows:
        answer = (clar.answer or "").strip()
        if not answer:
            report.skipped_no_answer += 1
            continue

        if await _already_harvested(
            session,
            workspace_id=workspace_id,
            source_kind=SOURCE_KIND_CLARIFICATION,
            source_id=str(clar.id),
        ):
            report.skipped_duplicate += 1
            continue

        question_excerpt = (clar.question or "").strip()[:QUESTION_EXCERPT_CAP]
        ticket_ref = clar.ticket_ref
        atoms, extractor = await _atoms_for_clarification(
            session,
            workspace_id=workspace_id,
            clar_question=clar.question or "",
            clar_answer=answer,
            ticket_ref=ticket_ref,
            llm_client=llm_client,
        )

        # Empty atoms-list (LLM said "no reusable knowledge") is a
        # valid outcome — count as inspected, not as created. Don't
        # fall back to identity in that case; the model decided it
        # wasn't useful, respect that signal.
        if not atoms:
            continue

        for atom_idx, atom in enumerate(atoms):
            improvement = Improvement(
                workspace_id=workspace_id,
                repo_id=clar.repo_id,
                routine_run_id=None,
                kind=NOTE_KIND,
                title=atom.title,
                body=atom.body,
                impact=None,
                effort=None,
                context={
                    "source_kind": SOURCE_KIND_CLARIFICATION,
                    "source_id": str(clar.id),
                    "source_excerpt": question_excerpt,
                    "ticket_ref": ticket_ref,
                    # Phase-2 fields — populated by the routing cron.
                    "routed_bucket_id": None,
                    "route_confidence": None,
                    # Phase-1b: LLM may suggest a bucket; KB-2 takes it
                    # as a tiebreaker when centroid score is ambiguous.
                    "bucket_hint": atom.bucket_hint,
                    # Multi-atom dedup key inside one source.
                    "atom_idx": atom_idx,
                    "extractor": extractor,
                    "harvested_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            session.add(improvement)
            report.created += 1

        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=clar.answered_by_user_id,
                actor_token_id=None,
                action="knowledge.note.harvested",
                target_kind=SOURCE_KIND_CLARIFICATION,
                target_id=str(clar.id),
                payload={
                    "extractor": extractor,
                    "ticket_ref": ticket_ref,
                    "atoms": len(atoms),
                },
            )
        )

    await session.flush()
    return report


async def harvest_all_workspaces(
    session: AsyncSession,
    *,
    limit_per_workspace: int = 200,
    llm_client: AgentClient | None = None,
) -> list[HarvestReport]:
    """Cron entry point — sweep every workspace once.

    ``llm_client`` is optional; when ``None`` the harvester falls back
    to the KB-1a identity extractor for every source. The cron worker
    constructs the client once via :func:`pick_default_client` and
    passes it in so every workspace shares one connection pool.
    """
    workspace_ids = (
        await session.execute(select(Workspace.id))
    ).scalars().all()

    reports: list[HarvestReport] = []
    for ws_id in workspace_ids:
        try:
            report = await harvest_workspace(
                session,
                workspace_id=ws_id,
                limit=limit_per_workspace,
                llm_client=llm_client,
            )
        except Exception as exc:
            log.exception("knowledge_harvest workspace=%s failed", ws_id)
            report = HarvestReport(workspace_id=ws_id, errors=[str(exc)])
        reports.append(report)
    return reports


async def _atoms_for_clarification(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    clar_question: str,
    clar_answer: str,
    ticket_ref: str | None,
    llm_client: AgentClient | None,
) -> tuple[list[KnowledgeAtom], str]:
    """LLM extractor first, identity fallback. Returns ``(atoms, extractor_label)``.

    The LLM may legitimately return an empty list (no reusable knowledge
    here) — that's distinct from "LLM failed / not configured": we let
    empty propagate so the harvester records "inspected, no atoms" and
    moves on. Failures (None) trigger the identity extractor so we
    never lose signal entirely.
    """
    if llm_client is not None:
        atoms = await extract_clarification_atoms(
            session,
            workspace_id=workspace_id,
            source=ClarificationSource(
                question=clar_question,
                answer=clar_answer,
                ticket_ref=ticket_ref,
            ),
            client=llm_client,
        )
        if atoms is not None:
            return atoms, "llm_v1"

    # Identity fallback: one atom carrying the answer verbatim. Title
    # is the same shape Phase 1a used so dashboards comparing the two
    # extractors stay readable.
    excerpt = clar_question.strip()[:QUESTION_EXCERPT_CAP]
    title = _make_note_title(ticket_ref=ticket_ref, question=excerpt)
    return [
        KnowledgeAtom(
            title=title,
            body=clar_answer[:ANSWER_BODY_CAP],
            bucket_hint=None,
        )
    ], "identity_v1"


async def _already_harvested(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    source_kind: str,
    source_id: str,
) -> bool:
    """Has a knowledge-note row already been created for this source?

    Uses the JSONB ``context->>'source_id'`` projection so the
    existence check stays in PostgreSQL without an extra column or
    index. Fine for hourly cron volumes; if note throughput crosses
    O(10k/day) we add a partial unique index on
    (workspace_id, kind, context->>'source_kind', context->>'source_id').
    """
    stmt = (
        select(Improvement.id)
        .where(Improvement.workspace_id == workspace_id)
        .where(Improvement.kind == NOTE_KIND)
        .where(
            and_(
                Improvement.context["source_kind"].astext == source_kind,
                Improvement.context["source_id"].astext == source_id,
            )
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


def _make_note_title(*, ticket_ref: str | None, question: str) -> str:
    """Compact, recognisable title: '<ticket>: <first line of question>'.

    Falls back to question prefix when there's no ticket. Title cap
    matches what the existing improvements UI tolerates without
    truncating mid-word in the list view.
    """
    first_line = (question.splitlines()[0] if question else "").strip()
    if not first_line:
        first_line = "(no question text)"
    if ticket_ref:
        head = f"{ticket_ref}: {first_line}"
    else:
        head = first_line
    return head[:TITLE_CAP]


__all__ = [
    "NOTE_KIND",
    "SOURCE_KIND_CLARIFICATION",
    "HarvestReport",
    "harvest_workspace",
    "harvest_all_workspaces",
]
