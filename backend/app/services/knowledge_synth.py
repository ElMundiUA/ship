"""Phase-3 synthesiser for the knowledge-ingestion epic (ELS-37 / KB-3).

Reads routed-but-not-yet-synthesised
``Improvement(kind='knowledge_note')`` rows, groups them per workspace
bucket, and produces one **draft** :class:`BucketArticle` per bucket-
per-tick. The LLM decides whether the new notes are a *new* article
(fresh slug, version=1) or an *update* (incremented version of an
existing slug, ``supersedes_id`` linked).

Acceptance contract baked into the cron output (per the ELS-37
addendum we landed earlier):

  - Every BucketArticle write populates ``embedding`` via
    ``embed_text(title + body_md)`` (best-effort — log + continue
    on a missing OPENAI key, mirroring Distiller's ``_maybe_embed``).
  - Every successful write bumps ``knowledge_buckets.updated_at`` so
    the Console's "Last indexed" badge stays fresh.

KB-4 (operator review) reads ``status='draft'`` rows tagged with
``provenance.kind = 'auto_routed_notes'`` and surfaces them in the
inbox. Approve flips ``draft → published`` (and superseded chain
finalises) — that path is owned by KB-4, not here.

The synthesiser never commits — caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    KnowledgeBucket,
)
from backend.app.db.models.agent_surface import Improvement
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.tenancy import AuditLog, Workspace
from backend.app.services.agent.client import AgentClient, ChatMessage
from backend.app.services.agent.embedding import embed_text
from backend.app.services.knowledge_harvest import NOTE_KIND


log = logging.getLogger(__name__)


# Cap how many notes we feed the model per bucket — keeps the prompt
# bounded and naturally sheds back-pressure if a single bucket gets
# floods of notes (later ticks pick up the rest).
NOTES_PER_BUCKET_LIMIT = 20
# Cap how many existing articles we expose to the model — prefer
# tightest-fit by recency to keep the prompt small.
EXISTING_ARTICLES_LIMIT = 12
# Per-source body excerpts.
_NOTE_BODY_CAP = 2500
_EXISTING_BODY_CAP = 800
_TITLE_CAP = 512
_BODY_CAP = 20_000


@dataclass(slots=True)
class _SynthDecision:
    action: str  # 'new' | 'update'
    slug: str
    title: str
    body_md: str
    supersedes_slug: str | None  # only set when action=='update'


@dataclass(slots=True)
class SynthReport:
    workspace_id: uuid.UUID
    buckets_inspected: int = 0
    drafts_created: int = 0
    drafts_skipped_no_notes: int = 0
    drafts_skipped_no_llm: int = 0
    drafts_skipped_dup_content: int = 0
    notes_consumed: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def synthesise_workspace(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    llm_client: AgentClient | None,
) -> SynthReport:
    """One synthesis pass for one workspace.

    Per workspace bucket: gather pending notes (capped), pull a
    summary of existing published articles, ask the LLM to decide
    new vs update, write the draft, mark the consumed notes.
    """
    report = SynthReport(workspace_id=workspace_id)

    bucket_rows = (
        await session.execute(
            select(KnowledgeBucket)
            .where(KnowledgeBucket.workspace_id == workspace_id)
            .where(KnowledgeBucket.scope_kind == BucketScope.WORKSPACE)
            .where(KnowledgeBucket.archived_at.is_(None))
        )
    ).scalars().all()

    for bucket in bucket_rows:
        report.buckets_inspected += 1
        try:
            await _synthesise_bucket(
                session,
                workspace_id=workspace_id,
                bucket=bucket,
                llm_client=llm_client,
                report=report,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort per bucket
            log.exception(
                "knowledge_synth: workspace=%s bucket=%s failed",
                workspace_id,
                bucket.slug,
            )
            report.errors.append(f"{bucket.slug}: {exc}")

    await session.flush()
    return report


async def synthesise_all_workspaces(
    session: AsyncSession,
    *,
    llm_client: AgentClient | None,
) -> list[SynthReport]:
    """Cron entry point — sweep every workspace once."""
    workspace_ids = (
        await session.execute(select(Workspace.id))
    ).scalars().all()

    reports: list[SynthReport] = []
    for ws_id in workspace_ids:
        try:
            r = await synthesise_workspace(
                session, workspace_id=ws_id, llm_client=llm_client
            )
        except Exception as exc:
            log.exception("knowledge_synth: workspace=%s failed", ws_id)
            r = SynthReport(workspace_id=ws_id, errors=[str(exc)])
        reports.append(r)
    return reports


# ---------------------------------------------------------------------------
# Per-bucket pass
# ---------------------------------------------------------------------------


async def _synthesise_bucket(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    bucket: KnowledgeBucket,
    llm_client: AgentClient | None,
    report: SynthReport,
) -> None:
    pending = await _pending_notes_for_bucket(
        session, workspace_id=workspace_id, bucket_id=bucket.id
    )
    if not pending:
        report.drafts_skipped_no_notes += 1
        return

    if llm_client is None:
        # Without an LLM we can't synthesise — leave notes pending so
        # a later tick (with creds configured) picks them up.
        report.drafts_skipped_no_llm += 1
        return

    existing = await _existing_published_articles(
        session, bucket_id=bucket.id
    )

    decision = await _ask_llm_synthesis(
        client=llm_client,
        bucket=bucket,
        notes=pending,
        existing=existing,
    )
    if decision is None:
        report.drafts_skipped_no_llm += 1
        return

    body_sha = hashlib.sha256(decision.body_md.encode("utf-8")).hexdigest()

    # Idempotency: if this exact (bucket, slug, content_sha) already
    # lives as a draft or published article, don't re-create. The
    # cron may run twice in pathological cases (lock release race);
    # the existing article is the truth.
    existing_same = (
        await session.execute(
            select(BucketArticle)
            .where(BucketArticle.bucket_id == bucket.id)
            .where(BucketArticle.slug == decision.slug)
            .where(BucketArticle.content_sha == body_sha)
            .where(BucketArticle.archived_at.is_(None))
        )
    ).scalar_one_or_none()
    if existing_same is not None:
        # Even on dup, mark the source notes as consumed by this
        # article so the next tick doesn't loop.
        await _mark_notes_consumed(session, pending, existing_same.id)
        report.drafts_skipped_dup_content += 1
        report.notes_consumed += len(pending)
        return

    supersedes_id: uuid.UUID | None = None
    new_version = 1
    if decision.action == "update" and decision.supersedes_slug:
        prev = next(
            (
                a
                for a in existing
                if a.slug == decision.supersedes_slug
            ),
            None,
        )
        if prev is not None:
            # Use the actual slug that the operator-facing draft is
            # an update *of*. If the LLM proposed a new slug we
            # accept that — KB-4 will surface "this draft updates
            # article X" via supersedes_id either way.
            supersedes_id = prev.id
            new_version = (prev.version or 0) + 1

    # Embedding: best-effort. Missing key → article still useful.
    embedding = await _maybe_embed(decision.title, decision.body_md)

    article = BucketArticle(
        id=uuid.uuid4(),
        bucket_id=bucket.id,
        slug=decision.slug,
        title=decision.title[:_TITLE_CAP],
        body_md=decision.body_md[:_BODY_CAP],
        content_sha=body_sha,
        version=new_version,
        status=BucketArticleStatus.DRAFT,
        supersedes_id=supersedes_id,
        embedding=embedding,
        provenance={
            "kind": "auto_routed_notes",
            "synthesised_at": datetime.now(timezone.utc).isoformat(),
            "source_note_ids": [str(n.id) for n in pending],
            "llm_action": decision.action,
        },
    )
    session.add(article)
    await session.flush()  # need article.id for the consume step

    # Bump bucket.updated_at so the Console's freshness badge moves.
    bucket.updated_at = datetime.now(timezone.utc)

    await _mark_notes_consumed(session, pending, article.id)
    report.notes_consumed += len(pending)
    report.drafts_created += 1

    # KB-4 (ELS-38): drop one InboxItem per draft so the operator
    # review surface picks it up. The inbox-disposition side-effect
    # handler (services/inbox/side_effects.py) reads
    # source_table='bucket_articles' + payload.kind='auto_routed_draft'
    # to decide between publish (accept) and archive (dismiss).
    summary_excerpt = decision.body_md.strip().splitlines()
    summary = " ".join(summary_excerpt[:3])[:1000] if summary_excerpt else None
    session.add(
        InboxItem(
            workspace_id=workspace_id,
            repo_id=None,
            type="improvement",
            title=(
                f"Draft article: {decision.title}"
                if decision.action == "new"
                else f"Draft update: {decision.title}"
            )[:300],
            summary=summary,
            payload={
                "kind": "auto_routed_draft",
                "article_id": str(article.id),
                "bucket_id": str(bucket.id),
                "bucket_slug": bucket.slug,
                "article_slug": decision.slug,
                "action": decision.action,
                "version": new_version,
                "source_note_count": len(pending),
                "source_note_ids": [str(n.id) for n in pending],
            },
            status="new",
            source_table="bucket_articles",
            source_id=article.id,
            intake_handle=None,
            intake_reason="knowledge_draft_review",
        )
    )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="knowledge.article.synthesised",
            target_kind="bucket_article",
            target_id=str(article.id),
            payload={
                "bucket_slug": bucket.slug,
                "article_slug": decision.slug,
                "action": decision.action,
                "version": new_version,
                "supersedes_id": str(supersedes_id) if supersedes_id else None,
                "source_note_count": len(pending),
            },
        )
    )


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


async def _pending_notes_for_bucket(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    bucket_id: uuid.UUID,
) -> list[Improvement]:
    """Routed-but-not-yet-synthesised notes for one bucket."""
    stmt = (
        select(Improvement)
        .where(Improvement.workspace_id == workspace_id)
        .where(Improvement.kind == NOTE_KIND)
        .where(Improvement.context["routed_bucket_id"].astext == str(bucket_id))
        .where(
            Improvement.context["synthesised_into_article_id"].astext.is_(None)
        )
        .order_by(Improvement.created_at.asc())
        .limit(NOTES_PER_BUCKET_LIMIT)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _existing_published_articles(
    session: AsyncSession, *, bucket_id: uuid.UUID
) -> list[BucketArticle]:
    """Most-recent published articles in the bucket, capped for prompt size."""
    stmt = (
        select(BucketArticle)
        .where(BucketArticle.bucket_id == bucket_id)
        .where(BucketArticle.status == BucketArticleStatus.PUBLISHED)
        .where(BucketArticle.archived_at.is_(None))
        .order_by(BucketArticle.updated_at.desc())
        .limit(EXISTING_ARTICLES_LIMIT)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _mark_notes_consumed(
    session: AsyncSession,
    notes: list[Improvement],
    article_id: uuid.UUID,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for n in notes:
        ctx = dict(n.context or {})
        ctx["synthesised_into_article_id"] = str(article_id)
        ctx["synthesised_at"] = now
        n.context = ctx


async def _maybe_embed(title: str, body: str) -> list[float] | None:
    text = f"{title}\n\n{body}"
    try:
        return await embed_text(text)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info("knowledge_synth: embedding skipped (%s)", exc)
        return None


# ---------------------------------------------------------------------------
# LLM synthesis call
# ---------------------------------------------------------------------------


_SYNTH_SYSTEM_PROMPT = """You are the synthesiser stage of a knowledge pipeline.
You receive a list of new "notes" routed into one knowledge bucket and a
summary of the bucket's already-published articles. Decide whether the
notes belong as a **new** article or as an **update** to an existing one,
then produce the draft.

Rules:

- If the notes overlap heavily with one of the existing articles
  (same topic, same decision, same recipe), choose ``"update"`` and
  set ``supersedes_slug`` to that article's slug. Rewrite the body
  to incorporate the new content; do NOT just append "addendum" —
  the operator-review surface will diff old vs new for you.
- Otherwise return ``"new"`` with a fresh slug. Slugs must be
  lowercase, kebab-case, only ``[a-z0-9-]``, ≤120 chars; pick
  something durable, not date-stamped.
- Title: ≤ 240 chars, declarative.
- body_md: structured Markdown. Lead with a one-sentence summary,
  then sections as appropriate (Decision / Why / How / Constraints /
  etc.). Aim for the depth of an ADR or a runbook entry — not a
  one-liner.

Return strictly one JSON object:
{"action":"new"|"update","slug":"...","title":"...","body_md":"...",
 "supersedes_slug": "..." | null}

If the notes are too thin / off-topic for any draft, return:
{"action":"skip"}
"""


async def _ask_llm_synthesis(
    *,
    client: AgentClient,
    bucket: KnowledgeBucket,
    notes: list[Improvement],
    existing: list[BucketArticle],
) -> _SynthDecision | None:
    """One non-streaming completion. Returns ``None`` on failure / skip."""
    user_msg = _build_user_prompt(bucket=bucket, notes=notes, existing=existing)
    try:
        raw = await client.acomplete(
            messages=[
                ChatMessage(role="system", content=_SYNTH_SYSTEM_PROMPT),
                ChatMessage(role="user", content=user_msg),
            ],
            model="gpt-4o-mini",
            max_tokens=4000,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info("knowledge_synth: LLM call failed (%s)", exc)
        return None

    try:
        obj = _parse_synth_json(raw)
    except Exception as exc:  # noqa: BLE001 — best-effort
        log.info("knowledge_synth: malformed LLM output (%s)", exc)
        return None

    action = obj.get("action")
    if action not in ("new", "update"):
        return None

    slug = obj.get("slug")
    title = obj.get("title")
    body_md = obj.get("body_md")
    if not (
        isinstance(slug, str)
        and isinstance(title, str)
        and isinstance(body_md, str)
    ):
        return None
    slug = _normalise_slug(slug)
    if not slug:
        return None
    title = title.strip()
    body_md = body_md.strip()
    if not title or not body_md:
        return None

    supersedes_slug_raw = obj.get("supersedes_slug")
    supersedes_slug = (
        supersedes_slug_raw.strip()
        if isinstance(supersedes_slug_raw, str) and supersedes_slug_raw.strip()
        else None
    )

    return _SynthDecision(
        action=action,
        slug=slug,
        title=title,
        body_md=body_md,
        supersedes_slug=supersedes_slug,
    )


def _build_user_prompt(
    *,
    bucket: KnowledgeBucket,
    notes: list[Improvement],
    existing: list[BucketArticle],
) -> str:
    out: list[str] = []
    out.append(f"## Bucket: `{bucket.slug}` — {bucket.name}")
    if bucket.description:
        out.append(bucket.description.strip())

    out.append("\n## Existing published articles")
    if not existing:
        out.append("- (none yet)")
    else:
        for a in existing:
            excerpt = (a.body_md or "").strip()[:_EXISTING_BODY_CAP]
            out.append(f"- slug: `{a.slug}`, version {a.version}")
            out.append(f"  title: {a.title}")
            if excerpt:
                out.append(f"  excerpt: {excerpt}")

    out.append("\n## New notes routed into this bucket")
    for n in notes:
        body = (n.body or "").strip()[:_NOTE_BODY_CAP]
        ctx = n.context or {}
        src = ctx.get("source_kind", "?")
        ticket = ctx.get("ticket_ref")
        ticket_line = f" (ticket {ticket})" if ticket else ""
        out.append(f"\n### {n.title} [{src}{ticket_line}]")
        out.append(body)
    return "\n".join(out)


def _parse_synth_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


def _normalise_slug(value: str) -> str:
    """Lowercase, kebab-case; keep only ``[a-z0-9-]``; collapse runs of ``-``."""
    cleaned = re.sub(r"[^a-z0-9-]+", "-", value.strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:120]


__all__ = [
    "SynthReport",
    "synthesise_all_workspaces",
    "synthesise_workspace",
]
