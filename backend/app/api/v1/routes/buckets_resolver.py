"""Resolver for the scope × source knowledge-bucket view.

``GET /v1/workspaces/{ws}/buckets/resolved?repo_id=…&project_id=…``

The resolver is Phase 3 of the consolidation plan
(``backend/docs/knowledge-consolidation.md``). Callers pass the
*context* they're rendering (which repo? which project?) and we
return the buckets visible in that context, ordered by priority so a
frontend/agent can either:

* Take the whole list to answer "what knowledge is in scope" — the
  scope-pill in the console, say — or
* Dedupe by ``slug`` and keep the highest-priority candidate to
  answer "what's the *effective* bucket for this context" — which
  file does `get_knowledge_bucket("code-style")` actually read.

Scope ladder (low → high priority; later wins):

```
workspace (10)  ≺  project (20)  ≺  repo (30)
                    ⊕
                  user (40, private overlay — caller only)
```

Workspace rows are always included (they're the baseline). Project
and repo are opt-in via query params. The user overlay is always
added for the caller — rows scoped to other users are invisible (the
endpoint is the enforcement point: FK + caller guard in one place).

Wire shape keeps the bucket projection consistent with the existing
``list_buckets`` endpoint — the ``BucketOut`` fields plus:

* ``priority`` — the numeric priority above,
* ``effective_scope`` — one of ``"workspace" | "project" | "repo" |
  "user"``,
* ``effective`` — true if this row is the winner for its slug in
  this context (false for shadowed candidates).

Plus a ``winners_by_slug`` map as a convenience for consumers that
only care about the deduped view.

Archived rows are filtered by default; pass
``?include_archived=true`` to see them (rare; mostly for the
"what happened to my bucket" debugging flow).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleStatus,
    BucketScope,
    KnowledgeBucket,
)
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.tenancy import Project
from backend.app.db.session import get_session


router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["buckets-resolver"],
)


# ---------------------------------------------------------------------------
# Priority ladder
# ---------------------------------------------------------------------------
#
# Values are deliberately 10-spaced so a future phase can interleave
# a new scope (e.g. "team" between project and repo) without
# renumbering every existing tier. Consumers should treat priorities
# as a sortable opaque int — "higher wins" — not as a rigid contract.

_PRIORITY_BY_SCOPE: dict[str, int] = {
    BucketScope.WORKSPACE: 10,
    BucketScope.PROJECT: 20,
    BucketScope.REPO: 30,
    BucketScope.USER: 40,
}


class ResolvedBucketOut(BaseModel):
    """Bucket row in the resolver response.

    Extends the core bucket shape with resolver-only fields. We
    duplicate the bucket fields inline (rather than nesting) so the
    UI / CLI can stay table-oriented: one row → one card.
    """

    id: uuid.UUID
    slug: str
    name: str
    description: str | None
    archived_at: Any  # datetime | None — kept loose to share the serializer
    scope_kind: str
    source_kind: str
    source_ref: dict[str, Any] | None = None
    project_id: uuid.UUID | None = None
    repo_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    summary_count: int = 0

    # Resolver-only.
    priority: int
    effective_scope: str
    effective: bool


class ResolvedContextOut(BaseModel):
    repo_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None


class ResolvedBucketsResponse(BaseModel):
    version: int = 1
    workspace_id: uuid.UUID
    context: ResolvedContextOut
    buckets: list[ResolvedBucketOut]
    # Quick-dedupe map for consumers that don't want to scan the list:
    # ``slug -> winning bucket id``. Shadowed candidates are still in
    # ``buckets`` so the full trace is one response away.
    winners_by_slug: dict[str, uuid.UUID] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Context validation
# ---------------------------------------------------------------------------


async def _verify_repo_in_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, repo_id: uuid.UUID
) -> None:
    row = (
        await session.execute(
            select(WorkspaceRepo.id).where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.id == repo_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"repo {repo_id} not found in workspace {workspace_id}",
        )


async def _verify_project_in_workspace(
    session: AsyncSession, workspace_id: uuid.UUID, project_id: uuid.UUID
) -> None:
    row = (
        await session.execute(
            select(Project.id).where(
                Project.workspace_id == workspace_id,
                Project.id == project_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"project {project_id} not found in workspace {workspace_id}"
            ),
        )


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------


@router.get("/buckets/resolved", response_model=ResolvedBucketsResponse)
async def resolve_buckets(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID | None = Query(default=None),
    project_id: uuid.UUID | None = Query(default=None),
    include_archived: bool = Query(default=False),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ResolvedBucketsResponse:
    """Return every bucket visible in this (workspace × project × repo)
    context plus the caller's private user overlay.

    This is a read-only projection: it never creates, updates or
    archives rows. Archive state is reported inline so the caller
    can surface "this bucket used to be effective but was archived"
    without a second query.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    # Validate the carrier FKs belong to this workspace. We check
    # here (not in the resolver SQL) so the caller gets a 404 with a
    # useful message, not an empty list.
    if repo_id is not None:
        await _verify_repo_in_workspace(session, workspace_id, repo_id)
    if project_id is not None:
        await _verify_project_in_workspace(session, workspace_id, project_id)

    # ---- Build the OR-of-scope-filters query ----
    #
    # The resolver returns the union of:
    # * workspace-scoped rows (baseline; always included),
    # * project-scoped rows for the given project_id (if provided),
    # * repo-scoped rows for the given repo_id (if provided),
    # * user-scoped rows for the caller (always; private overlay).
    #
    # Each clause nails down the scope AND its carrier FK so a
    # partial-unique index leak (a project-scoped row with the wrong
    # project_id, say) can't accidentally appear in a neighbour's
    # context.
    scope_clauses = [
        and_(
            KnowledgeBucket.scope_kind == BucketScope.WORKSPACE,
            KnowledgeBucket.project_id.is_(None),
            KnowledgeBucket.repo_id.is_(None),
            KnowledgeBucket.user_id.is_(None),
        ),
        and_(
            KnowledgeBucket.scope_kind == BucketScope.USER,
            KnowledgeBucket.user_id == auth.user.id,
        ),
    ]
    if project_id is not None:
        scope_clauses.append(
            and_(
                KnowledgeBucket.scope_kind == BucketScope.PROJECT,
                KnowledgeBucket.project_id == project_id,
            )
        )
    if repo_id is not None:
        scope_clauses.append(
            and_(
                KnowledgeBucket.scope_kind == BucketScope.REPO,
                KnowledgeBucket.repo_id == repo_id,
            )
        )

    stmt = (
        select(KnowledgeBucket)
        .where(KnowledgeBucket.workspace_id == workspace_id)
        .where(or_(*scope_clauses))
        .order_by(KnowledgeBucket.slug, KnowledgeBucket.scope_kind)
    )
    if not include_archived:
        stmt = stmt.where(KnowledgeBucket.archived_at.is_(None))

    rows = list((await session.execute(stmt)).scalars().all())

    # ---- Published article counts in one query ----
    summary_counts: dict[uuid.UUID, int] = {}
    if rows:
        bucket_ids = [b.id for b in rows]
        summary_rows = (
            await session.execute(
                select(BucketArticle.bucket_id).where(
                    BucketArticle.bucket_id.in_(bucket_ids),
                    BucketArticle.status == BucketArticleStatus.PUBLISHED,
                    BucketArticle.archived_at.is_(None),
                )
            )
        ).scalars().all()
        for bid in summary_rows:
            summary_counts[bid] = summary_counts.get(bid, 0) + 1

    # ---- Compute winner-per-slug ----
    #
    # Highest priority wins. On a priority tie (shouldn't happen
    # with the current ladder — one row per scope_kind per carrier)
    # we keep whichever the DB returned first for determinism.
    winners: dict[str, KnowledgeBucket] = {}
    for b in rows:
        prio = _PRIORITY_BY_SCOPE.get(b.scope_kind, 0)
        incumbent = winners.get(b.slug)
        if incumbent is None:
            winners[b.slug] = b
            continue
        incumbent_prio = _PRIORITY_BY_SCOPE.get(incumbent.scope_kind, 0)
        if prio > incumbent_prio:
            winners[b.slug] = b

    # ---- Project to wire shape ----
    buckets_out: list[ResolvedBucketOut] = []
    for b in rows:
        prio = _PRIORITY_BY_SCOPE.get(b.scope_kind, 0)
        winner = winners.get(b.slug)
        is_effective = winner is not None and winner.id == b.id
        buckets_out.append(
            ResolvedBucketOut(
                id=b.id,
                slug=b.slug,
                name=b.name,
                description=b.description,
                archived_at=b.archived_at,
                scope_kind=b.scope_kind,
                source_kind=b.source_kind,
                source_ref=b.source_ref,
                project_id=b.project_id,
                repo_id=b.repo_id,
                user_id=b.user_id,
                summary_count=summary_counts.get(b.id, 0),
                priority=prio,
                effective_scope=b.scope_kind,
                effective=is_effective,
            )
        )

    # Stable output order: by priority ascending, then slug. "Low →
    # high" lets a UI render the inheritance visually (workspace at
    # the top, repo / user overlay at the bottom, winners marked).
    buckets_out.sort(key=lambda r: (r.priority, r.slug))

    return ResolvedBucketsResponse(
        workspace_id=workspace_id,
        context=ResolvedContextOut(
            repo_id=repo_id,
            project_id=project_id,
            user_id=auth.user.id,
        ),
        buckets=buckets_out,
        winners_by_slug={slug: b.id for slug, b in winners.items()},
    )
