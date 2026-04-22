"""LLM-backed canonical-draft generator (RFC-0008 §I, PR-7B).

Given a set of repo-scope :class:`BucketArticle` rows that the
dedup pipeline flagged as a cluster, ask the configured LLM to
synthesise a single *workspace canonical* article that generalises
across repos.

The drafter is:

- **Stateless** — it never writes to the DB. The operator reviews
  the draft in the Console modal; persistence happens in the
  separate ``POST /knowledge/promote`` endpoint.
- **Best-effort** on JSON — we parse the model's output via the
  shared :func:`backend.app.services.json_utils.salvage_json`
  helper, same contract used by the pattern-draft endpoint.
- **Vendor-agnostic** — the caller passes an :class:`AgentClient`
  (typically via ``pick_default_client(settings)``) so tests can
  inject a fake without mocking the config layer.

``PromotionDraft`` is the wire shape the Console renders on the
review screen. ``notes`` is free-form rationale that surfaces to
the operator but never lands in the persisted article.
"""

from __future__ import annotations

import logging
import uuid
from typing import Protocol, Sequence

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import BucketArticle, KnowledgeBucket
from backend.app.services.json_utils import salvage_json


logger = logging.getLogger(__name__)


class PromotionDraft(BaseModel):
    """LLM-generated canonical draft.

    ``slug`` / ``title`` / ``body`` are what gets persisted if the
    operator accepts. ``summary`` is a short headline (rendered on
    the bucket card); ``notes`` is free-form advice from the model
    (conflicts flagged across repos, unresolved decisions) — it
    never lands in the persisted article, only in the review UI.
    """

    slug: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    summary: str | None = None
    notes: str | None = None


class _AcompleteClient(Protocol):
    """Subset of :class:`AgentClient` we actually call here."""

    async def acomplete(
        self,
        messages: Sequence[object],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        response_format: dict | None = None,
    ) -> str:
        ...


_DRAFT_SYSTEM_PROMPT = """You are a senior platform engineer consolidating \
workspace-wide knowledge for Ship — an internal fleet-automation platform. \
Operators ran a deduplication pass across their repositories and clustered \
several near-duplicate knowledge articles (one per repo). Your job is to \
merge them into **one** generalised, repo-agnostic canonical article that \
will live at workspace scope and be referenced from every repo.

You receive the clustered articles as a numbered list with their
repository context. Emit **a single JSON object** (no prose, no
markdown fences, no code blocks — raw JSON only). It must match
this TypeScript schema:

  {
    slug: string,     // kebab-case identifier, <=120 chars, used as the
                      // workspace-scope bucket slug. Prefer the
                      // slug the operator already uses; otherwise
                      // pick a concise noun phrase.
    title: string,    // human-friendly article title
    body: string,     // MARKDOWN. Merge common guidance from every
                      // source article. Where articles disagree,
                      // surface the decision explicitly instead of
                      // silently picking one side.
    summary: string,  // one-paragraph executive summary rendered
                      // on the bucket card (<=320 chars).
    notes: string     // free-form caveats, unresolved conflicts,
                      // or follow-ups. Can be "" if nothing to flag.
  }

Rules:
- Be REPO-AGNOSTIC: strip repo-specific paths, file names, and
  environment handles unless they are the topic of the article.
- NEVER invent policy that isn't present in at least one source.
- Call out genuine conflicts in ``notes`` instead of guessing.
- Output MUST be valid JSON parseable by ``json.loads`` on the
  first try."""


def _render_article_blob(
    articles: list[BucketArticle],
    buckets: dict[uuid.UUID, KnowledgeBucket],
) -> str:
    """Render the source articles as a single numbered blob for the user prompt.

    We keep each article bounded to ~4k chars so a large cluster
    doesn't blow past the model's context window; beyond that budget
    the dedup clustering is almost certainly wrong and the operator
    should split the cluster, not ask the model to heroically merge.
    """
    parts: list[str] = []
    for i, article in enumerate(articles, start=1):
        bucket = buckets.get(article.bucket_id)
        slug = bucket.slug if bucket is not None else "?"
        body = (article.body_md or "").strip()
        if len(body) > 4000:
            body = body[:4000].rstrip() + "\n\n…(truncated)"
        header = f"### Source {i} — bucket `{slug}`"
        if article.title:
            header += f" · {article.title}"
        parts.append(f"{header}\n\n{body}")
    return "\n\n---\n\n".join(parts)


async def draft_canonical(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    article_ids: list[uuid.UUID],
    *,
    llm_client: _AcompleteClient,
) -> PromotionDraft:
    """Ask the LLM for a canonical draft over ``article_ids``.

    Loads the articles + parent buckets in a single batched query
    (scoped to ``workspace_id`` so a client can't smuggle in an
    article from another tenant). Returns the parsed
    :class:`PromotionDraft`; raises on an empty cluster, a tenant
    mismatch, or an unparseable LLM response.
    """
    if not article_ids:
        raise ValueError("article_ids must be non-empty")

    stmt = (
        select(BucketArticle, KnowledgeBucket)
        .join(KnowledgeBucket, KnowledgeBucket.id == BucketArticle.bucket_id)
        .where(
            BucketArticle.id.in_(article_ids),
            KnowledgeBucket.workspace_id == workspace_id,
        )
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        raise ValueError("no articles resolved for this workspace")

    article_map: dict[uuid.UUID, BucketArticle] = {row[0].id: row[0] for row in rows}
    bucket_map: dict[uuid.UUID, KnowledgeBucket] = {row[1].id: row[1] for row in rows}

    # Preserve the caller's order so the Console can highlight the
    # "representative" article the dedup pass picked out.
    ordered_articles: list[BucketArticle] = []
    for aid in article_ids:
        if aid in article_map:
            ordered_articles.append(article_map[aid])
    if not ordered_articles:
        raise ValueError("no articles resolved for this workspace")

    blob = _render_article_blob(ordered_articles, bucket_map)

    # Lazy import — keeps this module loadable in tests that want
    # to patch ``draft_canonical`` without pulling the vendor SDKs.
    from backend.app.services.agent.client import ChatMessage

    messages = [
        ChatMessage(role="system", content=_DRAFT_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=(
                f"Cluster of {len(ordered_articles)} article(s) across "
                f"{len({bucket_map[a.bucket_id].repo_id for a in ordered_articles if a.bucket_id in bucket_map})} "
                "repo(s). Merge into one canonical article.\n\n" + blob
            ),
        ),
    ]

    raw = await llm_client.acomplete(
        messages,
        max_tokens=2400,
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    data = salvage_json(raw)
    if not data:
        raise ValueError("LLM returned no usable JSON")

    return PromotionDraft.model_validate(data)


__all__ = ["PromotionDraft", "draft_canonical"]
