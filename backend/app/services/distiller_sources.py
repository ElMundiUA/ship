"""Distiller inbound source adapters (Phase 6c).

Each public function in this module turns some external payload
(a PR-merged webhook, a multipart upload, a connector-proxy page)
into a :class:`~backend.app.services.distiller.DistillerInput` and
calls :func:`~backend.app.services.distiller.run_distiller`. The
adapters are intentionally thin — they know *where a blob came
from*, nothing about *what the blob means*. Meaning is the
classifier's job (stub or LLM) and is the same code path for every
source.

Design rules:

- **No side effects outside the distiller contract.** The adapters
  do not talk to GitHub / Notion / S3; that's the caller's job.
  This keeps testing cheap (no network fixtures) and keeps the
  webhook path resilient to transient upstream failures.
- **Scope-aware bucket resolution.** Every adapter ensures a
  :class:`KnowledgeBucket` exists for the target scope (workspace,
  project, repo, user) before invoking the Distiller, so a first
  write against a repo never 404s.
- **Best-effort.** All adapters return ``None`` (not raise) on
  trivial miss conditions (empty body, missing FK) so the caller's
  outer loop — a webhook, a scheduled job — can keep going.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_memory import (
    BucketScope,
    BucketSource,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.services.distiller import (
    Classifier,
    DistillerInput,
    DistillerOutcome,
    run_distiller,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bucket resolution
# ---------------------------------------------------------------------------


async def ensure_bucket(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    slug: str,
    name: str,
    scope_kind: str = BucketScope.WORKSPACE,
    source_kind: str = BucketSource.EXTERNAL_STATIC,
    repo_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    description: str | None = None,
) -> KnowledgeBucket:
    """Fetch or create a bucket matching the given scope carrier.

    Lookup is keyed on ``(workspace_id, scope_kind, carrier_id,
    slug)`` — the same tuple Phase 3's resolver treats as unique.
    If the bucket exists we reuse it; otherwise we mint it with
    the supplied ``name``/``description`` defaults.

    Validation mirrors the ``ck_knowledge_buckets_scope_carrier``
    CHECK constraint: repo scope needs ``repo_id``, project scope
    needs ``project_id``, user scope needs ``user_id``, workspace
    scope takes none. We raise early with a readable error instead
    of relying on the DB error path.
    """
    if scope_kind == BucketScope.WORKSPACE:
        if repo_id or project_id or user_id:
            raise ValueError("workspace-scoped bucket cannot have a carrier id")
    elif scope_kind == BucketScope.REPO:
        if not repo_id:
            raise ValueError("repo-scoped bucket requires repo_id")
    elif scope_kind == BucketScope.PROJECT:
        if not project_id:
            raise ValueError("project-scoped bucket requires project_id")
    elif scope_kind == BucketScope.USER:
        if not user_id:
            raise ValueError("user-scoped bucket requires user_id")
    else:
        raise ValueError(f"unknown scope_kind: {scope_kind!r}")

    stmt = select(KnowledgeBucket).where(
        KnowledgeBucket.workspace_id == workspace_id,
        KnowledgeBucket.scope_kind == scope_kind,
        KnowledgeBucket.slug == slug,
    )
    if scope_kind == BucketScope.REPO:
        stmt = stmt.where(KnowledgeBucket.repo_id == repo_id)
    elif scope_kind == BucketScope.PROJECT:
        stmt = stmt.where(KnowledgeBucket.project_id == project_id)
    elif scope_kind == BucketScope.USER:
        stmt = stmt.where(KnowledgeBucket.user_id == user_id)

    existing = (await session.execute(stmt)).scalars().first()
    if existing is not None:
        return existing

    # Wrap the INSERT in a SAVEPOINT so a CHECK/integrity failure
    # here doesn't poison the surrounding transaction. Callers (PR
    # webhook, save-to-memory, upload handler) are composed of many
    # independent writes inside one request-scoped session; before
    # this block was nested, a flush crash left the Session in an
    # aborted state and every subsequent ``await session.flush()``
    # from that request raised ``PendingRollbackError`` -- which
    # presented downstream (Navigator, dashboard) as "server-side
    # exception" even though the Distiller was the one at fault.
    # Nested transactions roll back just the SAVEPOINT, so the outer
    # work the caller already did (notifications, audit rows) stays
    # intact and their ``try/except`` can decide what to do.
    savepoint = await session.begin_nested()
    try:
        row = KnowledgeBucket(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            slug=slug,
            name=name,
            description=description,
            scope_kind=scope_kind,
            source_kind=source_kind,
            repo_id=repo_id,
            project_id=project_id,
            user_id=user_id,
        )
        session.add(row)
        await session.flush()
    except IntegrityError as exc:
        await savepoint.rollback()
        logger.error(
            "distiller_sources.ensure_bucket flush failed: "
            "workspace_id=%s slug=%r scope=%s source=%s "
            "repo_id=%s project_id=%s user_id=%s exc=%s",
            workspace_id,
            slug,
            scope_kind,
            source_kind,
            repo_id,
            project_id,
            user_id,
            exc,
        )
        raise
    else:
        await savepoint.commit()
    logger.info(
        "distiller_sources: ensured bucket slug=%s scope=%s src=%s",
        slug,
        scope_kind,
        source_kind,
    )
    return row


# ---------------------------------------------------------------------------
# Per-user memory bucket (Phase 8)
# ---------------------------------------------------------------------------


# Stable slug so the Navigator — and any future retrieval surface —
# can address "my memory" by a predictable key instead of having to
# look up the id first. The surface is ``source_kind=agent_memory``
# so retrieval clauses that gate on that (TopicService,
# search_buckets tool) pick it up with no extra wiring.
USER_MEMORY_SLUG = "my-memory"
USER_MEMORY_NAME = "My memory"
USER_MEMORY_DESCRIPTION = (
    "Private notes saved from chat threads. Only you can read from or "
    "write to this bucket."
)


async def ensure_user_memory_bucket(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
) -> KnowledgeBucket:
    """Return (creating if needed) the caller's ``my-memory`` bucket.

    Thin wrapper around :func:`ensure_bucket` that pins every
    per-user bucket to a fixed slug and friendly name. This is what
    the Navigator "save to memory" hook calls before packing a
    thread summary — idempotent, so multiple concurrent first
    writes never race to create two rows thanks to the partial
    ``uq_knowledge_buckets_user_slug`` index.

    Why a shared slug: the resolver's scope ladder makes
    ``my-memory`` at ``scope=user`` shadow any workspace-level
    slug collision automatically, so a workspace-wide bucket with
    the same name would never pollute a user's private memory.
    """
    return await ensure_bucket(
        session,
        workspace_id=workspace_id,
        slug=USER_MEMORY_SLUG,
        name=USER_MEMORY_NAME,
        scope_kind=BucketScope.USER,
        source_kind=BucketSource.AGENT_MEMORY,
        user_id=user_id,
        description=USER_MEMORY_DESCRIPTION,
    )


# ---------------------------------------------------------------------------
# PR-merged adapter
# ---------------------------------------------------------------------------


# Slug the adapter writes to on every merged PR. Stable so the
# Navigator can retrieve "what merged in repo X?" by slug.
PR_SUMMARIES_SLUG = "pr-summaries"
PR_SUMMARIES_NAME = "Merged pull requests"


def _format_pr_body(pr: dict[str, Any], *, repo_full_name: str) -> str:
    """Render the merged PR as a compact markdown blob.

    We intentionally keep the body short-ish (~8k chars) — the
    classifier + embedding both degrade on huge blobs, and the PR
    description is usually the signal we want anyway. Longer diff
    commentary can come later through the review-summary tool.
    """
    number = pr.get("number") or 0
    title = (pr.get("title") or "").strip() or f"PR #{number}"
    body = (pr.get("body") or "").strip()
    author = ((pr.get("user") or {}).get("login") or "unknown").strip()
    merged_by = ((pr.get("merged_by") or {}).get("login") or "").strip()
    merged_at = (pr.get("merged_at") or "").strip()
    head_ref = ((pr.get("head") or {}).get("ref") or "").strip()
    base_ref = ((pr.get("base") or {}).get("ref") or "").strip()
    html_url = (pr.get("html_url") or "").strip()

    header = [
        f"# {title}",
        "",
        f"- Repo: `{repo_full_name}`",
        f"- PR: #{number} ({html_url})" if html_url else f"- PR: #{number}",
        f"- Author: @{author}",
    ]
    if merged_by:
        header.append(f"- Merged by: @{merged_by}")
    if merged_at:
        header.append(f"- Merged at: {merged_at}")
    if head_ref or base_ref:
        header.append(f"- Branch: `{head_ref}` → `{base_ref}`")
    header.append("")

    if body:
        header.append("## Description")
        header.append("")
        header.append(body[:6000])

    return "\n".join(header).strip()


def _pr_slug(pr: dict[str, Any]) -> str:
    """Derive a deterministic article slug from the PR.

    Stable across webhook replays — same PR → same slug, so repeated
    deliveries skip via content_sha instead of creating duplicates.
    """
    number = pr.get("number") or 0
    return f"pr-{int(number)}"


async def ingest_pr_merge(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo: WorkspaceRepo,
    payload: dict[str, Any],
    actor_user_id: uuid.UUID | None = None,
    classifier: Classifier | None = None,
) -> DistillerOutcome | None:
    """Ingest one "PR merged" webhook delivery into knowledge.

    Returns the outcome of :func:`run_distiller`, or ``None`` if
    the payload doesn't describe a merged PR (we're tolerant of
    replays + unrelated actions).
    """
    pr = payload.get("pull_request") or {}
    if not pr:
        return None
    if not pr.get("merged"):
        return None

    # Skip Ship's own install PRs — they carry no knowledge the
    # user wants in their bucket.
    head_ref = ((pr.get("head") or {}).get("ref") or "").strip()
    if head_ref.startswith("ship/install-"):
        logger.debug("ingest_pr_merge: skipping install PR %s", head_ref)
        return None

    # Belt-and-braces: the repo row came from
    # ``_resolve_workspace_repo`` in the webhook handler, so ``.id``
    # should always be set; but we've hit a production case where the
    # downstream ``ensure_bucket`` flush failed with the CHECK
    # constraint fingerprinting a NULL ``repo_id``. Bail out loudly
    # here instead of handing the adapter a blank carrier and letting
    # Postgres tell us about it with a less actionable trace.
    if not getattr(repo, "id", None) or not getattr(repo, "workspace_id", None):
        logger.error(
            "ingest_pr_merge: repo row missing identity "
            "(repo.id=%s workspace_id=%s full_name=%s pr_number=%s) — skipping",
            getattr(repo, "id", None),
            getattr(repo, "workspace_id", None),
            getattr(repo, "full_name", None),
            pr.get("number"),
        )
        return None

    bucket = await ensure_bucket(
        session,
        workspace_id=workspace_id,
        slug=PR_SUMMARIES_SLUG,
        name=f"{repo.full_name} — {PR_SUMMARIES_NAME}",
        scope_kind=BucketScope.REPO,
        source_kind=BucketSource.EXTERNAL_STATIC,
        repo_id=repo.id,
        description=(
            "Auto-populated from merged pull requests. One article per "
            "PR (slug `pr-<number>`); provenance carries the PR URL, "
            "author, branch, and merged-at timestamp."
        ),
    )

    body_md = _format_pr_body(pr, repo_full_name=repo.full_name)
    slug = _pr_slug(pr)
    title_hint = (pr.get("title") or "").strip()[:512] or None

    provenance = {
        "kind": "pr_merged",
        "repo_full_name": repo.full_name,
        "repo_id": str(repo.id),
        "pr_number": int(pr.get("number") or 0),
        "pr_id": pr.get("id"),
        "html_url": pr.get("html_url"),
        "author": ((pr.get("user") or {}).get("login")),
        "merged_at": pr.get("merged_at"),
        "head_ref": head_ref or None,
        "base_ref": ((pr.get("base") or {}).get("ref") or None),
    }

    outcome = await run_distiller(
        session,
        workspace_id=workspace_id,
        bucket=bucket,
        actor_user_id=actor_user_id,
        inp=DistillerInput(
            body_md=body_md,
            source_kind=BucketSource.EXTERNAL_STATIC,
            title_hint=title_hint,
            slug_hint=slug,
            provenance=provenance,
            input_ref={
                "webhook_event": "pull_request",
                "action": payload.get("action"),
                "delivery": (payload.get("delivery") or None),
            },
        ),
        classifier=classifier,
    )
    return outcome


# ---------------------------------------------------------------------------
# External-static upload adapter
# ---------------------------------------------------------------------------


async def ingest_external_static_upload(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    bucket: KnowledgeBucket,
    actor_user_id: uuid.UUID | None,
    filename: str,
    content_type: str | None,
    body_md: str,
    classifier: Classifier | None = None,
) -> DistillerOutcome:
    """Turn a user-uploaded file into a bucket article.

    The HTTP layer has already validated the file type + decoded
    the bytes into a utf-8 string. This adapter records the upload
    metadata on the article's provenance and derives a slug from
    the filename so repeated uploads of the same file are
    idempotent (content_sha does the final dedupe).
    """
    safe_filename = (filename or "upload").strip() or "upload"
    # Strip extension for the slug hint; the distiller's slugify
    # will camel-case-to-kebab the rest.
    slug_hint = safe_filename.rsplit(".", 1)[0] or "upload"
    title_hint = safe_filename

    provenance = {
        "kind": "external_static_upload",
        "filename": safe_filename,
        "content_type": content_type or "text/markdown",
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }

    return await run_distiller(
        session,
        workspace_id=workspace_id,
        bucket=bucket,
        actor_user_id=actor_user_id,
        inp=DistillerInput(
            body_md=body_md,
            source_kind=BucketSource.EXTERNAL_STATIC,
            title_hint=title_hint,
            slug_hint=slug_hint,
            provenance=provenance,
            input_ref={
                "source": "upload",
                "filename": safe_filename,
            },
        ),
        classifier=classifier,
    )


# ---------------------------------------------------------------------------
# Connector-proxy adapter (placeholder)
# ---------------------------------------------------------------------------


async def ingest_connector_page(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    bucket: KnowledgeBucket,
    actor_user_id: uuid.UUID | None,
    connector_kind: str,
    page_ref: dict[str, Any],
    body_md: str,
    classifier: Classifier | None = None,
) -> DistillerOutcome:
    """Adapter stub for connector-proxy sources.

    Kept in the module so the shape is documented and call sites
    can import a real symbol. The actual connector fetch layer
    (Notion/Linear/Confluence) will live in ``backend/app/services/
    connectors/*`` in a later phase; this function only handles
    the Distiller call once the caller has produced a markdown
    rendering of the page.
    """
    slug_hint = str(page_ref.get("slug") or page_ref.get("id") or "page")
    title_hint = str(page_ref.get("title") or slug_hint)[:512]

    provenance = {
        "kind": "connector_proxy",
        "connector_kind": connector_kind,
        **page_ref,
    }

    return await run_distiller(
        session,
        workspace_id=workspace_id,
        bucket=bucket,
        actor_user_id=actor_user_id,
        inp=DistillerInput(
            body_md=body_md,
            source_kind=BucketSource.CONNECTOR_PROXY,
            title_hint=title_hint,
            slug_hint=slug_hint,
            provenance=provenance,
            input_ref={
                "source": "connector_proxy",
                "connector_kind": connector_kind,
            },
        ),
        classifier=classifier,
    )


__all__ = [
    "PR_SUMMARIES_NAME",
    "PR_SUMMARIES_SLUG",
    "ensure_bucket",
    "ingest_connector_page",
    "ingest_external_static_upload",
    "ingest_pr_merge",
]
