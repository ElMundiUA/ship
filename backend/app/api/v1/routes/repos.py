"""Workspace repository activations (pilot Day 2 — repo picker).

Two surfaces:

- ``GET /v1/workspaces/{ws}/repos/available`` — calls the GitHub App
  ``/installation/repositories`` API live and returns the rich set the
  picker UI renders, with an ``activated`` flag mirroring our DB state.
  The vendor is the source of truth; we never assume the persisted set
  is complete.
- ``POST /v1/workspaces/{ws}/repos/activate`` — admin-only, accepts a
  list of vendor numeric ids and upserts :class:`WorkspaceRepo` rows for
  the selection. Anything not in the payload is *deactivated* (row
  deleted) so the picker stays "what you see is what's wired".
- ``GET /v1/workspaces/{ws}/repos`` — already-activated set, served
  straight from Postgres for the dashboard / Day-3 default-pipeline UI.
- ``GET /v1/workspaces/{ws}/repos/{repo_id}/code-map`` — Day-2 Code Map
  MVP. Returns the recursive file list of the repo's default branch via
  the GitHub Trees API, capped to 5_000 paths. Synchronous because the
  upstream API itself is sub-second for sane repos.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import Pipeline
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.gateway.code_host import RepoRef, RepoSummary
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.services.agent.kb_indexer import reindex_repo_kb
from backend.app.services.lane_recipes import (
    normalize_preset,
    seed_default_pipelines,
)
from backend.app.services.seed_bundle import BUNDLE_VERSION as _BUNDLE_VERSION


router = APIRouter(
    prefix="/workspaces/{workspace_id}/repos",
    tags=["repos"],
)

logger = logging.getLogger(__name__)


# Day-2 cap; the picker UI starts to suffer past a few hundred repos
# anyway. The adapter caps at 500 too — kept symmetric on purpose.
_CODE_MAP_FILE_CAP = 5_000


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AvailableRepoOut(BaseModel):
    """One row in the picker UI."""

    external_id: int
    full_name: str
    owner: str
    name: str
    default_branch: str
    private: bool
    html_url: str
    description: str | None
    activated: bool


class ActivatedRepoOut(BaseModel):
    """Persisted activation row (returned from `GET /repos` and after activate)."""

    id: uuid.UUID
    external_id: int
    full_name: str
    default_branch: str
    private: bool
    html_url: str
    description: str | None
    activated_at: datetime | None
    provider: str
    preset: str | None
    # Dashboard uses these to decide whether to show the "Open wizard"
    # CTA (never seeded), "Update available" CTA (drift), or no CTA
    # (up to date). ``current`` mirrors ``seed_bundle.BUNDLE_VERSION``
    # so the client doesn't need a separate meta endpoint.
    installed_bundle_version: int | None = None
    current_bundle_version: int = _BUNDLE_VERSION


class RepoActivateIn(BaseModel):
    """Payload for ``POST /repos/activate``."""

    external_ids: list[int] = Field(
        ...,
        description=(
            "Vendor numeric repository ids to keep activated. The set is "
            "**replacing**: anything previously activated and *not* in this "
            "list is removed."
        ),
    )
    preset: str | None = Field(
        default=None,
        description=(
            "Catalog preset id to attach to the activated repo(s). "
            "Post-P5-01 the meaningful surface is the single canonical "
            "``\"default\"`` preset. The 14 historical preset ids "
            "(``web-app`` / ``api-backend`` / …) remain accepted at "
            "this API boundary for backwards compatibility but are "
            "normalized to ``\"default\"`` via "
            ":func:`backend.app.services.lane_recipes.normalize_preset` "
            "before being persisted to ``WorkspaceRepo.preset``. ``None`` "
            "keeps any existing preset and otherwise resolves to "
            "``\"default\"``."
        ),
    )


class CodeMapOut(BaseModel):
    """Flat file listing for the Code Map MVP."""

    repo_id: uuid.UUID
    full_name: str
    default_branch: str
    ref_sha: str
    files: list[str]
    truncated: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_installation(
    session: AsyncSession, workspace_id: uuid.UUID
) -> GitHubInstallation:
    """Return the workspace's *active* GitHub App installation or 409.

    409 because "you haven't connected GitHub yet" is a precondition
    error, not a not-found — the workspace exists, the link doesn't.
    The frontend renders this as "Install the GitHub App first" with a
    deep-link back to the install step.
    """
    stmt = (
        select(GitHubInstallation)
        .where(GitHubInstallation.workspace_id == workspace_id)
        .where(GitHubInstallation.suspended_at.is_(None))
    )
    install = (await session.execute(stmt)).scalars().first()
    if install is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No active GitHub App installation for this workspace. "
                "Install the Ship app first."
            ),
        )
    return install


def _summary_to_out(
    summary: RepoSummary, *, activated_ids: set[int]
) -> AvailableRepoOut:
    return AvailableRepoOut(
        external_id=summary.external_id,
        full_name=summary.full_name,
        owner=summary.ref.owner,
        name=summary.ref.repo,
        default_branch=summary.default_branch,
        private=summary.private,
        html_url=summary.html_url,
        description=summary.description,
        activated=summary.external_id in activated_ids,
    )


def _row_to_out(row: WorkspaceRepo) -> ActivatedRepoOut:
    return ActivatedRepoOut(
        id=row.id,
        external_id=row.external_id,
        full_name=row.full_name,
        default_branch=row.default_branch,
        private=row.private,
        html_url=row.html_url,
        description=row.description,
        activated_at=row.activated_at,
        provider=row.provider,
        preset=row.preset,
        installed_bundle_version=row.installed_bundle_version,
        current_bundle_version=_BUNDLE_VERSION,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/available", response_model=list[AvailableRepoOut])
async def list_available_repos(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[AvailableRepoOut]:
    """Live list of repos the App installation can see, with our
    activation flag merged in. Members can read; only admins can activate."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    install = await _resolve_installation(session, workspace_id)

    activated_stmt = select(WorkspaceRepo.external_id).where(
        WorkspaceRepo.workspace_id == workspace_id,
        WorkspaceRepo.provider == "github",
    )
    activated_ids: set[int] = {
        row[0] for row in (await session.execute(activated_stmt)).all()
    }

    gateway = GitHubCodeHost(install.installation_id, settings=settings)
    try:
        summaries = await gateway.list_repo_summaries()
    except httpx.HTTPStatusError as exc:
        # Most common shape: 401/403 because the installation token went
        # bad (key rotated, install suspended just now). Surface 502 to
        # the console so it shows "GitHub link is broken — reinstall the
        # app" instead of leaving the UI on a confusing 500.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "GitHub API rejected the installation token "
                f"(HTTP {exc.response.status_code}). Reinstall the Ship app."
            ),
        ) from exc

    return [_summary_to_out(s, activated_ids=activated_ids) for s in summaries]


@router.get("", response_model=list[ActivatedRepoOut])
async def list_activated_repos(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ActivatedRepoOut]:
    """Activated repos for this workspace, served from our DB."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    stmt = (
        select(WorkspaceRepo)
        .where(WorkspaceRepo.workspace_id == workspace_id)
        .order_by(WorkspaceRepo.full_name)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_row_to_out(r) for r in rows]


@router.post("/activate", response_model=list[ActivatedRepoOut])
async def activate_repos(
    workspace_id: uuid.UUID,
    payload: RepoActivateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[ActivatedRepoOut]:
    """Replace the workspace's activated repo set with the given selection.

    Admin-only — activations are workspace-wide capabilities.

    The body is treated as the *complete* desired set, not a delta:
    anything previously activated and missing from ``external_ids`` is
    removed. This matches the picker's mental model ("ticked = wired,
    unticked = not wired").
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    install = await _resolve_installation(session, workspace_id)

    desired_ids: set[int] = {int(x) for x in payload.external_ids}

    # P5-01 collapse: legacy preset ids ("web-app", …) and ``None``
    # collapse to ``"default"``; any other string passes through
    # unchanged to keep the door open for catalog-authored future
    # presets. Persistence + audit log get the normalized value.
    preset = (
        normalize_preset(payload.preset) if payload.preset is not None else None
    )

    # Fetch the live picker view once so we can validate ids and grab
    # the metadata we'll persist. We *trust* the GitHub installation
    # API: if a repo is missing from the live list, the App can't see
    # it, end of story.
    gateway = GitHubCodeHost(install.installation_id, settings=settings)
    try:
        summaries = await gateway.list_repo_summaries()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "GitHub API rejected the installation token "
                f"(HTTP {exc.response.status_code}). Reinstall the Ship app."
            ),
        ) from exc

    by_ext_id: dict[int, RepoSummary] = {s.external_id: s for s in summaries}
    unknown = desired_ids - set(by_ext_id)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Some external_ids are not visible to this GitHub App "
                f"installation: {sorted(unknown)}"
            ),
        )

    existing_stmt = select(WorkspaceRepo).where(
        WorkspaceRepo.workspace_id == workspace_id,
        WorkspaceRepo.provider == "github",
    )
    existing_rows = (await session.execute(existing_stmt)).scalars().all()
    existing_by_ext: dict[int, WorkspaceRepo] = {
        r.external_id: r for r in existing_rows
    }

    now = datetime.now(timezone.utc)
    added: list[int] = []
    updated: list[int] = []
    removed: list[int] = []

    for ext_id in desired_ids:
        summary = by_ext_id[ext_id]
        row = existing_by_ext.get(ext_id)
        if row is None:
            row = WorkspaceRepo(
                workspace_id=workspace_id,
                installation_id=install.id,
                provider="github",
                external_id=ext_id,
                full_name=summary.full_name,
                default_branch=summary.default_branch,
                private=summary.private,
                html_url=summary.html_url,
                description=summary.description,
                activated_at=now,
                preset=preset,
            )
            session.add(row)
            added.append(ext_id)
        else:
            # Refresh the snapshot in case the repo was renamed,
            # made private, or the default branch changed.
            row.installation_id = install.id
            row.full_name = summary.full_name
            row.default_branch = summary.default_branch
            row.private = summary.private
            row.html_url = summary.html_url
            row.description = summary.description
            row.updated_at = now
            if preset is not None:
                # Caller explicitly chose a preset on this call — adopt
                # it. ``None`` means "don't touch"; this preserves the
                # preset from the original activation even if a later
                # repo-picker reconfiguration forgets to re-send it.
                row.preset = preset
            updated.append(ext_id)

    for ext_id, row in existing_by_ext.items():
        if ext_id in desired_ids:
            continue
        await session.delete(row)
        removed.append(ext_id)

    # Seed the default pipeline set if this is the workspace's first
    # activation (idempotent; subsequent activations are no-ops). We
    # do this before the audit log so the audit row can record how
    # many pipelines we materialised in the same transaction.
    pipeline_count_before = (
        await session.execute(
            select(Pipeline.id).where(Pipeline.workspace_id == workspace_id)
        )
    ).scalars().all()

    # Pick a "default" repo to bind newly seeded pipelines to. Pilot
    # heuristic: lexicographically smallest ``full_name`` from the
    # currently desired set keeps the choice deterministic across
    # re-activations (whatever the user re-toggles, the binding is
    # stable so workflow_dispatch lands in the same repo every time).
    default_repo_id: uuid.UUID | None = None
    if desired_ids:
        binding_stmt = (
            select(WorkspaceRepo)
            .where(WorkspaceRepo.workspace_id == workspace_id)
            .where(WorkspaceRepo.external_id.in_(desired_ids))
            .order_by(WorkspaceRepo.full_name)
        )
        default_row = (await session.execute(binding_stmt)).scalars().first()
        if default_row is not None:
            default_repo_id = default_row.id

    # Prefer the preset requested on this call; otherwise adopt the
    # preset stored on the "default" repo the pipelines will bind to
    # so a reseed call without an explicit preset still respects the
    # original pick. Normalize either source so audit telemetry stops
    # fragmenting on legacy ids (P5-01: stale rows may still hold
    # ``"web-app"`` etc., which we collapse to ``"default"``).
    seed_preset = preset
    if seed_preset is None and default_repo_id is not None:
        default_row = await session.get(WorkspaceRepo, default_repo_id)
        if default_row is not None and default_row.preset is not None:
            seed_preset = normalize_preset(default_row.preset)

    seeded_pipelines = await seed_default_pipelines(
        session,
        workspace_id,
        default_repo_id=default_repo_id,
        preset=seed_preset,
    )

    # Phase 2 consolidation: on first activation, mirror each new
    # repo's ``.ship/knowledge/*.md`` into ``knowledge_buckets`` so
    # the operator sees the /knowledge list populated without having
    # to push an unrelated commit. Scoped to ``added`` only — updates
    # and removes are no-ops here; push webhooks + manual reindex own
    # those paths. Failures are swallowed per-repo so one misbehaving
    # repo doesn't abort the whole activation transaction.
    if added:
        from backend.app.services.bucket_repo_files_sync import (
            sync_repo_files,
        )

        for ext_id in added:
            new_row = (
                await session.execute(
                    select(WorkspaceRepo).where(
                        WorkspaceRepo.workspace_id == workspace_id,
                        WorkspaceRepo.external_id == ext_id,
                    )
                )
            ).scalars().first()
            if new_row is None:
                continue
            try:
                await sync_repo_files(session, new_row, install)
            except Exception:  # pragma: no cover — defensive
                # Swallow: next push / manual reindex will retry.
                # Don't log the traceback verbatim; the audit log
                # already records the activation and the push-webhook
                # path logs its own failures.
                continue

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=None,
            action="repos.activate",
            target_kind="workspace_repos",
            target_id=str(workspace_id),
            payload={
                "added": sorted(added),
                "updated": sorted(updated),
                "removed": sorted(removed),
                "installation_id": install.installation_id,
                # Tells the audit consumer how many default pipelines
                # already existed and how many are alive after seed
                # (delta = newly created in this call).
                "pipelines_existing": len(pipeline_count_before),
                "pipelines_total": len(seeded_pipelines),
                "preset": seed_preset,
            },
        )
    )

    await session.flush()

    rows = (
        await session.execute(
            select(WorkspaceRepo)
            .where(WorkspaceRepo.workspace_id == workspace_id)
            .order_by(WorkspaceRepo.full_name)
        )
    ).scalars().all()
    return [_row_to_out(r) for r in rows]


@router.get("/{repo_id}/code-map", response_model=CodeMapOut)
async def get_code_map(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CodeMapOut:
    """Code Map MVP — flat list of files at the default branch HEAD.

    Synchronous because the GitHub Trees API itself is fast enough for
    the pilot scope; we cap at ``_CODE_MAP_FILE_CAP`` paths so an
    unusually large monorepo doesn't blow the response size. The
    ``truncated`` flag tells the frontend whether it's looking at the
    head of a longer list.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    stmt = select(WorkspaceRepo).where(
        WorkspaceRepo.workspace_id == workspace_id,
        WorkspaceRepo.id == repo_id,
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if row.installation_id is None:
        # Non-GitHub repos don't have a code-map source yet (no PAT
        # cloning since git_sync went away). 409 = "concept exists, this
        # particular row can't satisfy it".
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repo is not backed by a GitHub App installation.",
        )

    install = await session.get(GitHubInstallation, row.installation_id)
    if install is None or install.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "GitHub App installation for this repo is missing or "
                "suspended. Reinstall the Ship app."
            ),
        )

    owner, _, name = row.full_name.partition("/")
    ref = RepoRef(kind="github", owner=owner, repo=name)

    gateway = GitHubCodeHost(install.installation_id, settings=settings)
    try:
        files = await gateway.list_files(ref, ref_sha=row.default_branch)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "GitHub Trees API rejected the request "
                f"(HTTP {exc.response.status_code})."
            ),
        ) from exc

    truncated = len(files) > _CODE_MAP_FILE_CAP
    return CodeMapOut(
        repo_id=row.id,
        full_name=row.full_name,
        default_branch=row.default_branch,
        ref_sha=row.default_branch,
        files=files[:_CODE_MAP_FILE_CAP],
        truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Agent KB reindex (C12 Phase 1.3)
# ---------------------------------------------------------------------------


class KbReindexOut(BaseModel):
    """Result of a manual ``POST /{repo_id}/kb/reindex`` call.

    Mirrors :class:`~backend.app.services.agent.kb_indexer.IndexReport`
    but typed with pydantic so the console can render the numbers
    inline after the button click.
    """

    repo_id: uuid.UUID
    files_discovered: int
    files_indexed: int
    files_skipped_unchanged: int
    files_skipped_too_big: int
    files_skipped_binary: int
    chunks_deleted: int
    chunks_written: int


@router.post("/{repo_id}/kb/reindex", response_model=KbReindexOut)
async def reindex_repo_kb_route(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> KbReindexOut:
    """Re-embed ``.ship/knowledge/**/*.md`` for one repo on demand.

    Synchronous by design: KB corpora are small (dozens of docs, not
    thousands) so the whole pass finishes inside a request window.
    When that stops being true we'll queue a job and stream the
    report over SSE; for now keep it simple.

    Admin-only. The push webhook (Day-3 polish) calls
    :func:`reindex_repo_kb` directly, without going through this
    route, so pushing code doesn't require an API token.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    row = (await session.execute(
        select(WorkspaceRepo).where(
            WorkspaceRepo.workspace_id == workspace_id,
            WorkspaceRepo.id == repo_id,
        )
    )).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if row.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repo is not backed by a GitHub App installation.",
        )
    install = await session.get(GitHubInstallation, row.installation_id)
    if install is None or install.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "GitHub App installation for this repo is missing or "
                "suspended. Reinstall the Ship app."
            ),
        )

    try:
        report = await reindex_repo_kb(
            session, row, install, settings=settings
        )
    except RuntimeError as exc:
        # ``embed_texts`` raises RuntimeError when OPENAI_API_KEY is
        # missing; surface that as a 412 (precondition) so the operator
        # sees a clear "configure this first" message.
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=str(exc),
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "GitHub API rejected a KB indexer request "
                f"(HTTP {exc.response.status_code})."
            ),
        ) from exc

    # Phase 2 consolidation: mirror into ``knowledge_buckets`` so the
    # operator console's /knowledge page reflects the same reindex run.
    # Failures here are non-fatal for the embedder's audit trail —
    # logged + captured in the bucket-sync counters, not the 200
    # response shape (which is the KB indexer's contract).
    try:
        from backend.app.services.bucket_repo_files_sync import (
            sync_repo_files,
        )

        bucket_report = await sync_repo_files(
            session, row, install, settings=settings
        )
    except Exception:
        bucket_report = None

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=None,
            action="agent.kb.reindex",
            target_kind="workspace_repo",
            target_id=str(repo_id),
            payload={
                "files_discovered": report.files_discovered,
                "files_indexed": report.files_indexed,
                "chunks_written": report.chunks_written,
                "chunks_deleted": report.chunks_deleted,
                "buckets_created": (
                    bucket_report.buckets_created if bucket_report else 0
                ),
                "buckets_updated": (
                    bucket_report.buckets_updated if bucket_report else 0
                ),
                "buckets_archived": (
                    bucket_report.buckets_archived if bucket_report else 0
                ),
            },
        )
    )
    await session.flush()

    return KbReindexOut(
        repo_id=repo_id,
        files_discovered=report.files_discovered,
        files_indexed=report.files_indexed,
        files_skipped_unchanged=report.files_skipped_unchanged,
        files_skipped_too_big=report.files_skipped_too_big,
        files_skipped_binary=report.files_skipped_binary,
        chunks_deleted=report.chunks_deleted,
        chunks_written=report.chunks_written,
    )


# ---------------------------------------------------------------------------
# Multi-preset bundle install
# ---------------------------------------------------------------------------


class BundleInstallIn(BaseModel):
    """Body for ``POST /workspaces/{ws}/repos/{repo_id}/install_bundle``."""

    # Comma/array of preset ids to bundle together. ``None`` means
    # "use the repo's persisted preset"; at least one valid preset
    # must resolve after expansion or the request fails 422.
    presets: list[str] | None = Field(
        default=None,
        description=(
            "Preset ids to bundle (e.g. ['web-app']). Defaults to the "
            "repo's persisted preset; pass multiple to combine."
        ),
    )


class BundleInstallOut(BaseModel):
    pr_url: str
    pr_number: int
    branch: str
    files: list[str]
    presets: list[str]


@router.post("/{repo_id}/install_bundle", response_model=BundleInstallOut)
async def install_bundle(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: BundleInstallIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BundleInstallOut:
    """Open a single PR carrying every workflow + ``.ship/`` file a preset needs.

    Admin-only. Combines one or more presets into a single
    ``ship/bundle-<label>-<unix>`` PR so the operator reviews + merges
    *once* instead of per-lane. On merge, the knowledge-gathering
    webhook takes over and auto-dispatches ``tech_debt`` / ``code_map``
    (see ``auto_dispatch_knowledge_pipelines``).

    Returns ``412`` with a structured code when the repo has no
    resolvable preset or the bundle comes out empty (preset maps only
    to YAML-less lanes — currently ``code_map`` alone).
    """
    # Local imports keep the catalog + github-workflows modules out of
    # the hot path for the code-map / availability endpoints that
    # don't need them.
    from backend.app.db.models.integrations import GitHubInstallation
    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        commit_bundle_pr,
    )
    from backend.app.services import catalog as catalog_service

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )

    install_row = (
        await session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.id == repo_row.installation_id
            )
        )
    ).scalars().first()
    if install_row is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App isn't installed for the workspace. "
                    "Reconnect it before opening a bundle PR."
                ),
            },
        )

    # Resolve the effective preset list. Prefer the caller's explicit
    # list, else fall back to the repo's persisted preset. Each id is
    # passed through :func:`normalize_preset` so legacy ids (and any
    # stale value persisted before P5-01) collapse to ``"default"``
    # before bundle composition; an empty/whitespace id is dropped.
    requested = payload.presets if payload and payload.presets else None
    if requested is None:
        requested = [repo_row.preset] if repo_row.preset else []
    cleaned: list[str] = []
    seen: set[str] = set()
    for pid in requested:
        pid = (pid or "").strip()
        if not pid:
            continue
        normalized = normalize_preset(pid)
        if normalized in seen:
            continue
        cleaned.append(normalized)
        seen.add(normalized)
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "preset_required",
                "message": (
                    "Pass at least one preset or set it on the repo before "
                    "opening a bundle PR."
                ),
            },
        )

    # Collect the per-preset bundles, de-duplicating on path — two
    # presets can legitimately share a workflow (e.g. web-app +
    # api-backend both ship pr-and-ci-gate) and the second copy
    # would break the tree create.
    seen_paths: set[str] = set()
    files: list[tuple[str, str]] = []
    for pid in cleaned:
        for path, content in catalog_service.preset_bundle_files(
            pid, repo_full_name=repo_row.full_name
        ):
            # ``.ship/config.yml`` gets rebuilt per preset but the
            # bundle needs exactly one — last preset wins, which is
            # fine since config.yml just records the label.
            if path == ".ship/config.yml":
                files = [
                    (p, c) for (p, c) in files if p != ".ship/config.yml"
                ]
                seen_paths.discard(path)
            if path in seen_paths:
                continue
            files.append((path, content))
            seen_paths.add(path)

    if not files:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "empty_bundle",
                "message": (
                    f"Preset(s) {cleaned!r} resolved to zero installable files. "
                    "Likely the preset only declares YAML-less lanes today."
                ),
            },
        )

    return_url = (
        f"{settings.console_url.rstrip('/')}/?ws={workspace_id}"
        f"&installed=bundle&reason=back_from_pr"
    )
    branch_label = "-".join(cleaned)
    try:
        result = await commit_bundle_pr(
            repo_row,
            install_row,
            files=files,
            title=f"Ship: install {', '.join(cleaned)} preset bundle",
            branch_label=branch_label,
            pr_body_header=(
                f"This PR wires Ship into this repo by installing every "
                f"workflow the selected preset(s) need in **one merge**:\n\n"
                f"**Presets**: {', '.join('`' + p + '`' for p in cleaned)}"
            ),
            settings=settings,
            return_url=return_url,
        )
    except WorkflowDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "install_bundle_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
            },
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.install_bundle",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload={
                "presets": cleaned,
                "files": [p for p, _ in files],
                "pr_number": result.pr_number,
                "pr_url": result.pr_url,
                "branch": result.branch,
                "legacy_bundle": True,
            },
        )
    )
    await session.flush()

    return BundleInstallOut(
        pr_url=result.pr_url,
        pr_number=result.pr_number,
        branch=result.branch,
        files=[p for p, _ in files],
        presets=cleaned,
    )


# ---------------------------------------------------------------------------
# One-shot knowledge seed (Phase 2a)
#
# Counterpart to ``install_bundle`` but for ``.ship/knowledge/*.md``
# starter buckets. The bucket-selection UI lives in the onboarding
# wizard (step 4, checkboxes for ``code-style`` / ``ui-runbook``); the
# endpoint opens a single PR that drops the selected markdown files at
# the Ship-scanned path. Idempotent by convention: merging a second
# PR over the same file is a no-op *review* (no content change unless
# the tenant edited the seed).
# ---------------------------------------------------------------------------


class KnowledgeSeedIn(BaseModel):
    """Body for ``POST /workspaces/{ws}/repos/{repo_id}/knowledge_seed``."""

    # Knowledge-starter slugs to seed. ``None`` means "seed everything
    # the catalog ships today" — matches the "Select all" default on
    # the wizard checkbox group.
    selection: list[str] | None = Field(
        default=None,
        description=(
            "Knowledge-starter slugs to commit (e.g. ['code-style']). "
            "Defaults to seeding every starter in the catalog."
        ),
    )


class KnowledgeSeedOut(BaseModel):
    pr_url: str
    pr_number: int
    branch: str
    files: list[str]
    selection: list[str]


@router.post("/{repo_id}/knowledge_seed", response_model=KnowledgeSeedOut)
async def knowledge_seed(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: KnowledgeSeedIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> KnowledgeSeedOut:
    """Open a PR that seeds ``.ship/knowledge/<slug>.md`` starter buckets.

    Admin-only. Reuses ``commit_bundle_pr`` so the review experience
    is identical to a preset bundle install — one branch, one PR, one
    merge. After merge, the knowledge lister picks up the new files
    on the next workspace read (no cache invalidation needed).
    """
    from backend.app.db.models.integrations import GitHubInstallation
    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        commit_bundle_pr,
    )
    from backend.app.services import catalog as catalog_service

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )

    install_row = (
        await session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.id == repo_row.installation_id
            )
        )
    ).scalars().first()
    if install_row is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App isn't installed for the workspace. "
                    "Reconnect it before opening a knowledge-seed PR."
                ),
            },
        )

    requested = payload.selection if payload and payload.selection else None
    try:
        files = catalog_service.knowledge_starter_files(requested)
    except catalog_service.CatalogError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if not files:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "empty_knowledge_selection",
                "message": (
                    "Selection resolved to zero starter files — pick at "
                    "least one knowledge bucket."
                ),
            },
        )

    # ``selection`` for the audit row — reverse-map the resolved paths
    # back to slugs so the log is readable even if future versions
    # rename the on-disk layout.
    selected_slugs = [
        path.removeprefix(".ship/knowledge/").removesuffix(".md")
        for path, _ in files
    ]

    return_url = (
        f"{settings.console_url.rstrip('/')}/?ws={workspace_id}"
        f"&installed=knowledge&reason=back_from_pr"
    )
    try:
        result = await commit_bundle_pr(
            repo_row,
            install_row,
            files=files,
            title=(
                "Ship: seed starter knowledge buckets "
                f"({', '.join(selected_slugs)})"
            ),
            branch_label="knowledge-seed",
            pr_body_header=(
                "This PR drops Ship's starter knowledge buckets into "
                "`.ship/knowledge/`. Merge once; edit the markdown files "
                "in-place afterwards to match your team's conventions — "
                "Ship always reads the latest committed content.\n\n"
                f"**Buckets**: {', '.join('`' + s + '`' for s in selected_slugs)}"
            ),
            settings=settings,
            return_url=return_url,
        )
    except WorkflowDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "knowledge_seed_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
            },
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.knowledge_seed",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload={
                "selection": selected_slugs,
                "files": [p for p, _ in files],
                "pr_number": result.pr_number,
                "pr_url": result.pr_url,
                "branch": result.branch,
            },
        )
    )
    await session.flush()

    return KnowledgeSeedOut(
        pr_url=result.pr_url,
        pr_number=result.pr_number,
        branch=result.branch,
        files=[p for p, _ in files],
        selection=selected_slugs,
    )


# ---------------------------------------------------------------------------
# Per-repo preset picker (B9)
# ---------------------------------------------------------------------------


class RepoPresetPatchIn(BaseModel):
    """Payload for ``PATCH /v1/workspaces/{ws}/repos/{id}``.

    Only the ``preset`` field is mutable today; future fields (e.g.
    ``default_branch`` or per-repo config) can land here without a new
    endpoint. ``reshape`` controls whether we also rewrite the
    ``enabled`` flag on lanes bound to this repo so they match the
    new preset's default shape. It defaults to ``False`` because the
    seed path is "additive only" (we never silently disable a lane
    the operator turned on) — flipping this flag is an explicit
    operator choice surfaced in the UI copy.
    """

    preset: str | None = None
    reshape: bool = False


@router.patch("/{repo_id}", response_model=ActivatedRepoOut)
async def update_repo(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: RepoPresetPatchIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ActivatedRepoOut:
    """Mutate the preset bound to ``repo`` (and optionally reshape lanes).

    Admin-only. Post-P5-01 the meaningful preset is the single
    ``"default"`` value; legacy preset ids passed by older Console
    builds collapse to ``"default"`` via
    :func:`backend.app.services.lane_recipes.normalize_preset` before
    being persisted. ``None`` clears the binding and falls back to
    the canonical default shape on future seeds.

    Behavioural notes:

    - If ``reshape`` is true the new preset's enabled lane set
      (derived via :func:`resolve_enabled_lane_ids`) is applied to
      every ``Pipeline`` in the workspace whose ``repo_id`` matches
      this row. Workspace-level (unbound) lanes are untouched — they
      keep their hand-toggled state because they're shared across
      repos.
    - The seed helper is invoked afterwards so a tenant that picked
      e.g. ``monorepo`` later gets the ``self_heal`` lane created if
      it didn't exist yet.
    - Every call records an ``AuditLog`` entry with the old / new
      preset + counts so we can trace "why did my code_map lane
      flip on overnight" later.
    """
    from backend.app.db.models.pipelines import Pipeline
    from backend.app.services.lane_recipes import (
        resolve_enabled_lane_ids,
        seed_default_pipelines,
    )

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )

    # P5-01 collapse: normalize legacy ids to ``"default"`` before
    # persisting so the row joins the post-collapse vocabulary.
    # ``None`` keeps its semantic of "clear the binding".
    new_preset = (
        normalize_preset(payload.preset)
        if payload.preset is not None
        else None
    )

    old_preset = repo_row.preset
    repo_row.preset = new_preset

    reshape_applied = 0
    if payload.reshape and new_preset is not None:
        # Limit reshape to lanes actually bound to this repo; shared
        # workspace-level lanes stay untouched (they may be driving
        # other repos in the same workspace).
        enabled_kinds = resolve_enabled_lane_ids(new_preset)
        bound_lanes = (
            await session.execute(
                select(Pipeline).where(Pipeline.repo_id == repo_row.id)
            )
        ).scalars().all()
        for lane in bound_lanes:
            desired = lane.lane_id in enabled_kinds
            if lane.enabled != desired:
                lane.enabled = desired
                reshape_applied += 1

    # Additive seed — creates lanes that the new preset implies but
    # that weren't part of the old one (e.g. ``self_heal`` on
    # ``monorepo``). Never disables anything.
    await seed_default_pipelines(
        session,
        workspace_id,
        default_repo_id=repo_row.id,
        preset=new_preset,
    )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.preset.update",
            target_kind="workspace_repo",
            target_id=str(repo_id),
            payload={
                "full_name": repo_row.full_name,
                "old_preset": old_preset,
                "new_preset": new_preset,
                "reshape": bool(payload.reshape),
                "reshape_applied": reshape_applied,
            },
        )
    )
    await session.flush()
    return _row_to_out(repo_row)


# ---------------------------------------------------------------------------
# Wizard v2 unified seed PR (iter 5)
#
# Single-shot replacement for ``install_bundle`` + ``knowledge_seed``:
# one PR carrying the preset workflows, ``.ship/config.yml``, bootstrap
# workflow, and the tracker FSM doc. Also mints the long-
# lived ``SHIP_RUN_TOKEN`` Actions secret *before* opening the PR so
# the lanes the PR installs can authenticate the moment the merge
# fires their first schedule tick. Plaintext never touches the DB —
# see ``services.repo_tokens`` for the hash-only persistence story.
# ---------------------------------------------------------------------------


class WizardSeedIn(BaseModel):
    """Body for ``POST /workspaces/{ws}/repos/{repo_id}/wizard_seed``."""

    # P5-06 deprecation: every wizard run now seeds the canonical
    # :data:`backend.app.services.lane_recipes.DEFAULT_BUNDLE`
    # regardless of what the caller passes. Field is retained so
    # legacy clients (CLI versions in flight, tests not yet rebased,
    # the FE during the Wave-8c cut-over) don't 422 the moment they
    # POST a body. Drop once Wave 8c lands and the FE stops sending
    # it.
    presets: list[str] | None = Field(
        default=None,
        description=(
            "DEPRECATED (P5-06). Ignored — the wizard always seeds "
            "DEFAULT_BUNDLE now. Field retained for legacy CLI / "
            "FE clients during the Wave-8c cut-over."
        ),
    )
    knowledge_slugs: list[str] | None = Field(
        default=None,
        description=(
            "Deprecated compatibility field. Wizard seed ignores it; "
            "knowledge is generated post-merge by the bootstrap workflow."
        ),
    )
    # The tracker kind to render into the FSM doc. Normally derived
    # from the repo's tracker binding, but the wizard lets the user
    # preview the seed before saving the binding — the request body
    # carries whatever was picked in the wizard so the FSM doc is
    # consistent with the PR's .ship/config.yml review.
    tracker_kind: str | None = Field(
        default=None,
        description=(
            "Tracker kind the FSM doc should address. ``null`` drops "
            "a \"not connected yet\" header. When omitted, the server "
            "reads the repo's persisted tracker binding."
        ),
    )
    include_fsm: bool = True
    # Force-rotate SHIP_RUN_TOKEN even if one already exists. First
    # wizard run always rotates (no hash on the row yet); later runs
    # default to "keep the existing token" to avoid invalidating
    # in-flight runners every time the operator re-opens the wizard.
    rotate_run_token: bool = False


class WizardSeedCodeownersSummary(BaseModel):
    """CODEOWNERS → routing summary block of :class:`WizardSeedOut`.

    Wave-8c FE renders this directly: the post-wizard "what we wired"
    panel reads ``rules_count`` ("we found N CODEOWNERS rules") and
    ``unresolved_owners`` ("but couldn't match these usernames to a
    workspace member yet").
    """

    file_found: bool
    rules_count: int
    routing_rules_created: int
    unresolved_owners: list[str]


class WizardSeedIntelHandle(BaseModel):
    """Legacy repo-intel harvest handle on :class:`WizardSeedOut`.

    Retained for old clients and the manual refresh endpoint. The wizard seed
    path now returns ``None`` because post-merge bootstrap owns repo analysis.

    Two modes:

    - Inline (``enqueued=False``): no redis worker configured (dev),
      the wizard ran the harvest synchronously and ``intel_id`` is
      the freshly-inserted :class:`RepoIntel.id`.
    - Queued (``enqueued=True``): arq worker handles the job;
      ``job_id`` is the arq id the FE polls. ``intel_id`` is ``None``
      until the worker writes the row.
    """

    enqueued: bool
    job_id: str | None = None
    intel_id: uuid.UUID | None = None


class WizardSeedOut(BaseModel):
    """Response shape for ``POST .../wizard_seed`` (extended in P5-06).

    Fields added in P5-06 (``codeowners``, ``intel``,
    ``synthetic_lanes_created``) are all defaulted so legacy FE
    builds that ignore them keep deserialising. Wave-8c FE binds
    against them once the new Inbox / Coverage panels ship.
    """

    pr_url: str
    pr_number: int
    branch: str
    files: list[str]
    presets: list[str]
    knowledge_slugs: list[str]
    tracker_kind: str | None = None
    run_token_prefix: str | None = None
    run_token_rotated: bool = False
    # ── P5-06 additions ──────────────────────────────────────────
    codeowners: WizardSeedCodeownersSummary | None = None
    intel: WizardSeedIntelHandle | None = None
    # ── P5-07 addition ───────────────────────────────────────────
    synthetic_lanes_created: int = 0


class KnowledgeBootstrapIn(BaseModel):
    force: bool = False


class KnowledgeBootstrapOut(BaseModel):
    status: str
    pr_url: str | None = None
    pr_number: int | None = None
    branch: str | None = None
    files: list[str] = Field(default_factory=list)
    intel_version: int | None = None
    articles_written: int = 0


class RepoTriggerIn(BaseModel):
    event: str = Field(pattern="^(schedule|manual|pull_request|push)$")
    config: dict[str, Any] = Field(default_factory=dict)
    github: dict[str, Any] = Field(default_factory=dict)
    tick_window_minutes: int = Field(default=20, ge=1, le=180)


class RepoTriggerLaneOut(BaseModel):
    lane_id: str
    kind: str
    pattern: str | None = None
    reason: str
    window_key: str


class RepoTriggerOut(BaseModel):
    event: str
    status: str
    due_lanes: list[RepoTriggerLaneOut] = Field(default_factory=list)
    skipped_lanes: list[dict[str, Any]] = Field(default_factory=list)


async def _dispatch_intel_harvest(
    *,
    request: Request | None,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
) -> WizardSeedIntelHandle:
    """Dispatch a one-shot repo-intel harvest for the wizard (P5-06).

    Two paths share one return shape so the FE doesn't branch on
    deployment topology:

    - ``request.app.state.redis_pool`` is set → enqueue an arq job
      and return ``enqueued=True`` with the ``job_id`` the FE polls.
    - Otherwise (typical local-dev: no ``--profile worker``) → run
      the harvest synchronously via :func:`harvest_repo_intel` and
      return ``enqueued=False`` with the freshly-inserted
      ``intel_id``. Failures degrade to ``intel_id=None`` so the
      wizard PR still ships even if the harvest itself blew up
      (a missing intel doc isn't worth aborting a successful seed).

    The ``triggered_by`` is always
    :data:`RepoIntelTriggeredBy.WIZARD` so the audit table can
    distinguish wizard-driven harvests from CLI / cron / manual
    re-triggers down the line.
    """
    import time as _time

    from backend.app.services.repo_intel import (
        RepoIntelTriggeredBy,
        harvest_repo_intel,
    )

    redis_pool = None
    if request is not None:
        redis_pool = getattr(request.app.state, "redis_pool", None)

    if redis_pool is not None:
        # Job id mirrors :func:`enqueue_harvest`'s scheme so the
        # worker logs are greppable across both call sites.
        job_id = f"harvest:{repo_id}:{int(_time.time())}"
        try:
            await redis_pool.enqueue_job(
                "harvest_repo_intel_job",
                str(workspace_id),
                str(repo_id),
                RepoIntelTriggeredBy.WIZARD,
                _job_id=job_id,
            )
        except Exception:  # pragma: no cover — logged + degraded
            logger.exception(
                "wizard_seed: redis enqueue_job failed; harvest "
                "skipped",
                extra={
                    "workspace_id": str(workspace_id),
                    "repo_id": str(repo_id),
                },
            )
            return WizardSeedIntelHandle(
                enqueued=False, job_id=None, intel_id=None
            )
        return WizardSeedIntelHandle(
            enqueued=True, job_id=job_id, intel_id=None
        )

    # Inline path. We deliberately ``await`` rather than
    # ``asyncio.create_task`` (which is ``enqueue_harvest``'s dev
    # behaviour) so the response carries a real ``intel_id`` —
    # the wizard's "View intel" CTA needs something to link to and
    # the FE has no other handle to poll for the inline result.
    try:
        report = await harvest_repo_intel(
            session=session,
            workspace_id=workspace_id,
            repo_id=repo_id,
            triggered_by=RepoIntelTriggeredBy.WIZARD,
        )
    except Exception:  # pragma: no cover — logged + degraded
        logger.exception(
            "wizard_seed: inline harvest_repo_intel failed",
            extra={
                "workspace_id": str(workspace_id),
                "repo_id": str(repo_id),
            },
        )
        return WizardSeedIntelHandle(
            enqueued=False, job_id=None, intel_id=None
        )
    return WizardSeedIntelHandle(
        enqueued=False, job_id=None, intel_id=report.intel_id
    )


@router.post("/{repo_id}/wizard_seed", response_model=WizardSeedOut)
async def wizard_seed(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    request: Request,
    payload: WizardSeedIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WizardSeedOut:
    """Open the single wizard seed PR for a repo (P5-06 orchestration v2).

    Admin-only. The flow is:

    1. Resolve the tracker kind (body override, else the per-repo
       binding, else the workspace default).
    2. Mint a fresh ``SHIP_RUN_TOKEN`` if one doesn't exist or if the
       caller asked to rotate. Plaintext is PUT to GitHub Actions
       *before* the PR opens so the workflows installed by the PR can
       authenticate on their first tick. On any failure here the PR is
       never opened — a PR without the secret would silently break
       every schedule-triggered lane it installs.
    2b. Push ``SHIP_API_BASE`` (``SHIP_PUBLIC_URL``) and mint a fresh
       workspace-scoped PAT as ``SHIP_API_TOKEN`` on every seed, so
       re-seeding older repos repairs missing CI secrets even when the
       long-lived run token is not rotated.
    3. Compose the file list via
       :func:`backend.app.services.seed_bundle.compose_seed_files`
       against the canonical
       :data:`backend.app.services.lane_recipes.DEFAULT_BUNDLE`. The
       legacy ``payload.presets`` field is silently ignored (P5-06
       deprecation — see :class:`WizardSeedIn`).
    4. Open one PR via ``commit_bundle_pr``.
    5. Audit-log the wizard seed with every file path (no plaintext).
       Repo analysis, routing reconciliation, and generated knowledge
       happen from the merged default branch through post-merge bootstrap.
    """

    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        commit_bundle_pr,
    )
    from backend.app.services.lane_recipes import DEFAULT_BUNDLE
    from backend.app.services.repo_tokens import (
        mint_repo_callback_token,
        push_ship_methodology_github_secrets,
    )
    from backend.app.services.seed_bundle import compose_seed_files

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )

    install_row = (
        await session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.id == repo_row.installation_id
            )
        )
    ).scalars().first()
    if install_row is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App isn't installed for the workspace. "
                    "Reconnect it before opening the wizard seed PR."
                ),
            },
        )

    payload = payload or WizardSeedIn()

    # ── Presets (deprecated; ignored post-P5-06) ──────────────────
    # Every wizard call now seeds the canonical DEFAULT_BUNDLE; the
    # ``presets=["default"]`` echo on the response is purely for
    # legacy FE compatibility (see :class:`WizardSeedOut`).
    cleaned: list[str] = ["default"]

    # ── Resolve tracker kind for the FSM doc ──────────────────────
    # Preference order: explicit body → per-repo binding → workspace
    # default. Reading the bindings here avoids a second server
    # roundtrip from the wizard ("save tracker" → "get tracker" →
    # "seed").
    from backend.app.db.models.tenancy import Integration

    tracker_kind = (payload.tracker_kind or "").strip().lower() or None
    repo_binding_kind: str | None = None
    workspace_default_kind: str | None = None
    if tracker_kind is None:
        repo_binding = (
            await session.execute(
                select(Integration)
                .where(
                    Integration.workspace_id == workspace_id,
                    Integration.repo_id == repo_id,
                    Integration.kind.in_(("linear", "github", "jira")),
                )
                .order_by(Integration.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if repo_binding is not None:
            tracker_kind = repo_binding.kind
            repo_binding_kind = repo_binding.kind
    # Always resolve the workspace default for the FSM header,
    # regardless of where the tracker kind came from — operators
    # want to see "overrides default" when the repo diverges.
    ws_default_row = (
        await session.execute(
            select(Integration)
            .where(
                Integration.workspace_id == workspace_id,
                Integration.repo_id.is_(None),
                Integration.kind.in_(("linear", "github", "jira")),
            )
            .order_by(Integration.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if ws_default_row is not None:
        workspace_default_kind = ws_default_row.kind
        if tracker_kind is None:
            tracker_kind = ws_default_row.kind

    # ── Mint SHIP_RUN_TOKEN before opening the PR ────────────────
    should_mint = (
        repo_row.run_token_hash is None or bool(payload.rotate_run_token)
    )
    rotated = False
    if should_mint:
        try:
            await mint_repo_callback_token(
                session,
                repo_row,
                install_row,
                settings=settings,
            )
            rotated = True
        except Exception as exc:  # pragma: no cover — surfaced as 502
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "code": "run_token_push_failed",
                    "message": (
                        "Couldn't push SHIP_RUN_TOKEN to the repo's GitHub "
                        "Actions secrets. The seed PR was not opened."
                    ),
                },
            ) from exc

    # ── Ship API origin + PAT for Actions / shipctl ───────────────
    try:
        await push_ship_methodology_github_secrets(
            session,
            workspace_id=workspace_id,
            acting_user_id=auth.user.id,
            repo=repo_row,
            install=install_row,
            settings=settings,
            mint_new_api_pat=True,
        )
    except Exception as exc:  # pragma: no cover — surfaced as 502
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "ship_ci_secret_push_failed",
                "message": (
                    "Couldn't push Ship CI secrets (SHIP_API_BASE / "
                    "SHIP_API_TOKEN) to GitHub Actions. The seed PR was not opened."
                ),
            },
        ) from exc

    # ── Compose the file bundle (pure) ────────────────────────────
    # Always DEFAULT_BUNDLE post-P5-06 — see WizardSeedIn.presets
    # deprecation. The bundle / knowledge counts still flow through
    # the audit log so we can tell apart "old tiny seed" from
    # "P5-06 full seed" on a wizard-replay.
    bundle = compose_seed_files(
        bundle=DEFAULT_BUNDLE,
        knowledge_slugs=[],
        tracker_kind=tracker_kind,
        workspace_default_tracker_kind=workspace_default_kind,
        include_fsm=payload.include_fsm,
        repo_intel_placeholder=False,
        repo_full_name=repo_row.full_name,
    )

    if not bundle.files:
        # Defensive: DEFAULT_BUNDLE is non-empty by construction so
        # this path is unreachable in production. Kept so a future
        # refactor that drops the bundle constant can't open an
        # empty PR.
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "empty_bundle",
                "message": (
                    "DEFAULT_BUNDLE + selected options resolved to "
                    "zero installable files."
                ),
            },
        )

    # ── Open the PR ──────────────────────────────────────────────
    return_url = (
        f"{settings.console_url.rstrip('/')}/?ws={workspace_id}"
        f"&installed=wizard&reason=back_from_pr"
    )
    tracker_line = (
        f"**Tracker**: `{tracker_kind}`"
        if tracker_kind
        else "**Tracker**: _not connected yet_"
    )
    body_header = (
        "This PR wires Ship into this repo in a single merge.\n\n"
        f"**Bundle**: `{bundle.bundle_hash}` "
        f"({len(bundle.bundle)} Plays)\n"
        f"{tracker_line}\n"
        "**Knowledge**: generated post-merge by `.github/workflows/ship-bootstrap.yml`\n\n"
        "Merge once. Ship's bootstrap workflow will analyze the merged repo "
        "and open a second PR with generated `.ship/knowledge/*.md` docs."
    )
    try:
        result = await commit_bundle_pr(
            repo_row,
            install_row,
            files=bundle.files,
            title="Ship: wizard seed",
            branch_label="wizard-default",
            pr_body_header=body_header,
            settings=settings,
            return_url=return_url,
        )
    except WorkflowDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "wizard_seed_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
            },
        ) from exc

    # Post-merge bootstrap/config sync owns all repo-analysis side effects.
    codeowners_summary: WizardSeedCodeownersSummary | None = None
    synthetic_lanes_created = 0
    intel_handle = None

    # Stamp the bundle version so the dashboard can tell "up to date"
    # from "upgrade available" next render.
    repo_row.installed_bundle_version = _BUNDLE_VERSION

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.wizard_seed",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload={
                "presets": cleaned,
                "knowledge_slugs": bundle.knowledge_slugs,
                "tracker_kind": tracker_kind,
                "tracker_source": (
                    "body"
                    if payload.tracker_kind
                    else ("repo" if repo_binding_kind else ("workspace" if ws_default_row else "none"))
                ),
                "files": [p for p, _ in bundle.files],
                "pr_number": result.pr_number,
                "pr_url": result.pr_url,
                "branch": result.branch,
                "run_token_rotated": rotated,
                # run_token_prefix only; plaintext never persisted.
                "run_token_prefix": repo_row.run_token_prefix,
                "bundle_version": _BUNDLE_VERSION,
                "bundle_hash": bundle.bundle_hash,
                "synthetic_lanes_created": synthetic_lanes_created,
                "codeowners": (
                    codeowners_summary.model_dump()
                    if codeowners_summary is not None
                    else None
                ),
                "intel": (
                    intel_handle.model_dump(mode="json")
                    if intel_handle is not None
                    else None
                ),
            },
        )
    )
    await session.flush()

    return WizardSeedOut(
        pr_url=result.pr_url,
        pr_number=result.pr_number,
        branch=result.branch,
        files=[p for p, _ in bundle.files],
        presets=cleaned,
        knowledge_slugs=bundle.knowledge_slugs,
        tracker_kind=tracker_kind,
        run_token_prefix=repo_row.run_token_prefix,
        run_token_rotated=rotated,
        codeowners=codeowners_summary,
        intel=intel_handle,
        synthetic_lanes_created=synthetic_lanes_created,
    )


@router.post(
    "/{repo_id}/knowledge/bootstrap",
    response_model=KnowledgeBootstrapOut,
)
async def bootstrap_repo_knowledge(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: KnowledgeBootstrapIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> KnowledgeBootstrapOut:
    """Analyze the merged repo and open PR 2 with generated knowledge docs."""

    from backend.app.db.models.repo_intel import RepoIntelTriggeredBy
    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        commit_bundle_pr,
    )
    from backend.app.services.generated_knowledge import (
        render_generated_knowledge_files,
    )
    from backend.app.services.repo_intel import (
        get_current_intel,
        harvest_repo_intel,
    )

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    payload = payload or KnowledgeBootstrapIn()

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )

    if not payload.force:
        previous = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.workspace_id == workspace_id,
                    AuditLog.action == "repo.knowledge_bootstrap",
                    AuditLog.target_kind == "workspace_repo",
                    AuditLog.target_id == str(repo_row.id),
                )
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if previous is not None:
            prev_payload = previous.payload or {}
            if prev_payload.get("status") == "knowledge_pr_opened":
                return KnowledgeBootstrapOut(
                    status="already_done",
                    pr_url=prev_payload.get("pr_url"),
                    pr_number=prev_payload.get("pr_number"),
                    branch=prev_payload.get("branch"),
                    files=list(prev_payload.get("files") or []),
                    intel_version=prev_payload.get("intel_version"),
                    articles_written=int(prev_payload.get("articles_written") or 0),
                )

    install_row = (
        await session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.id == repo_row.installation_id
            )
        )
    ).scalars().first()
    if install_row is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": "Ship's GitHub App is not installed for this repo.",
            },
        )

    report = await harvest_repo_intel(
        session=session,
        workspace_id=workspace_id,
        repo_id=repo_row.id,
        triggered_by=RepoIntelTriggeredBy.BOOTSTRAP,
        settings=settings,
    )
    intel = await get_current_intel(session, repo_row.id)
    if intel is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "repo_intel_missing",
                "message": "Bootstrap analyzed the repo but did not produce repo intel.",
            },
        )

    files = render_generated_knowledge_files(repo=repo_row, intel=intel)
    body_header = (
        "This PR adds Ship-generated repository knowledge files.\n\n"
        f"Generated from repo intel version `{intel.version}` after the wizard "
        "seed PR was merged. Review these docs, edit anything too generic, "
        "then merge to index them into Ship knowledge search."
    )
    try:
        pr = await commit_bundle_pr(
            repo_row,
            install_row,
            files=files,
            title="Ship: generated repository knowledge",
            branch_label="knowledge-bootstrap",
            pr_body_header=body_header,
            settings=settings,
        )
    except WorkflowDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "knowledge_bootstrap_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
            },
        ) from exc

    result_payload = {
        "status": "knowledge_pr_opened",
        "pr_url": pr.pr_url,
        "pr_number": pr.pr_number,
        "branch": pr.branch,
        "files": [path for path, _ in files],
        "intel_version": intel.version,
        "articles_written": report.knowledge_articles_written,
    }
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.knowledge_bootstrap",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload=result_payload,
        )
    )
    await session.flush()

    return KnowledgeBootstrapOut(**result_payload)


@router.post("/{repo_id}/trigger", response_model=RepoTriggerOut)
async def trigger_repo_lanes(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: RepoTriggerIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RepoTriggerOut:
    """Ship-side scheduler/router for thin GitHub trigger adapters."""

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )

    now = datetime.now(timezone.utc)
    lanes = payload.config.get("lanes")
    if not isinstance(lanes, dict):
        return RepoTriggerOut(
            event=payload.event,
            status="noop",
            skipped_lanes=[{"reason": "config_has_no_lanes"}],
        )

    due: list[RepoTriggerLaneOut] = []
    skipped: list[dict[str, Any]] = []
    for lane_id, lane in lanes.items():
        if not isinstance(lane_id, str) or not isinstance(lane, dict):
            skipped.append({"lane_id": str(lane_id), "reason": "invalid_lane"})
            continue
        kind = str(lane.get("kind") or "")
        if not _lane_accepts_event(lane, payload.event):
            skipped.append({"lane_id": lane_id, "kind": kind, "reason": "trigger_mismatch"})
            continue
        window_key = _trigger_window_key(
            event=payload.event,
            lane_id=lane_id,
            lane=lane,
            now=now,
            window_minutes=payload.tick_window_minutes,
        )
        if window_key is None:
            skipped.append({"lane_id": lane_id, "kind": kind, "reason": "not_due"})
            continue
        if await _trigger_window_seen(
            session,
            workspace_id=workspace_id,
            repo_id=repo_row.id,
            lane_id=lane_id,
            window_key=window_key,
        ):
            skipped.append({"lane_id": lane_id, "kind": kind, "reason": "already_dispatched"})
            continue
        pattern = _lane_primary_pattern(lane)
        due.append(
            RepoTriggerLaneOut(
                lane_id=lane_id,
                kind=kind,
                pattern=pattern,
                reason="due",
                window_key=window_key,
            )
        )

    for lane in due:
        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=auth.user.id,
                actor_token_id=auth.token.id if auth.token else None,
                action="repo.trigger_lane",
                target_kind="workspace_repo",
                target_id=str(repo_row.id),
                payload={
                    "event": payload.event,
                    "lane_id": lane.lane_id,
                    "kind": lane.kind,
                    "pattern": lane.pattern,
                    "window_key": lane.window_key,
                    "github": payload.github,
                    "status": "due",
                },
            )
        )
    await session.flush()

    return RepoTriggerOut(
        event=payload.event,
        status="due" if due else "noop",
        due_lanes=due,
        skipped_lanes=skipped,
    )


def _lane_accepts_event(lane: dict[str, Any], event: str) -> bool:
    kind = str(lane.get("kind") or "")
    if event == "schedule":
        return kind == "schedule"
    if event == "manual":
        return kind in {"schedule", "event", "once"}
    if kind != "event":
        return False
    configured = str(lane.get("on") or "")
    return event in {part.strip() for part in configured.split(",") if part.strip()}


def _trigger_window_key(
    *,
    event: str,
    lane_id: str,
    lane: dict[str, Any],
    now: datetime,
    window_minutes: int,
) -> str | None:
    if event == "manual":
        return f"manual:{lane_id}:{now.isoformat(timespec='seconds')}"
    if event != "schedule":
        return f"{event}:{lane_id}:{now.strftime('%Y%m%dT%H%M')}"
    cron = lane.get("cron")
    if not isinstance(cron, str) or not cron.strip():
        return None
    matched = _latest_cron_match(cron, now=now, window_minutes=window_minutes)
    if matched is None:
        return None
    return f"schedule:{lane_id}:{matched.strftime('%Y%m%dT%H%M')}"


def _latest_cron_match(
    cron: str, *, now: datetime, window_minutes: int
) -> datetime | None:
    fields = cron.split()
    if len(fields) != 5:
        return None
    minute_s, hour_s, dom_s, month_s, dow_s = fields
    cursor = now.replace(second=0, microsecond=0)
    for offset in range(0, window_minutes + 1):
        candidate = cursor - timedelta(minutes=offset)
        if (
            _cron_field_matches(minute_s, candidate.minute, 0, 59)
            and _cron_field_matches(hour_s, candidate.hour, 0, 23)
            and _cron_field_matches(dom_s, candidate.day, 1, 31)
            and _cron_field_matches(month_s, candidate.month, 1, 12)
            and _cron_field_matches(dow_s, (candidate.weekday() + 1) % 7, 0, 6)
        ):
            return candidate
    return None


def _cron_field_matches(expr: str, value: int, minimum: int, maximum: int) -> bool:
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if part == "*":
            return True
        step = 1
        base = part
        if "/" in part:
            base, raw_step = part.split("/", 1)
            try:
                step = int(raw_step)
            except ValueError:
                continue
            if step <= 0:
                continue
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            raw_start, raw_end = base.split("-", 1)
            try:
                start, end = int(raw_start), int(raw_end)
            except ValueError:
                continue
        else:
            try:
                start = end = int(base)
            except ValueError:
                continue
        if start <= value <= end and ((value - start) % step == 0):
            return True
    return False


def _lane_primary_pattern(lane: dict[str, Any]) -> str | None:
    pattern = lane.get("pattern")
    if isinstance(pattern, str) and pattern:
        return pattern
    patterns = lane.get("patterns")
    if isinstance(patterns, list):
        for item in patterns:
            if isinstance(item, str) and item:
                return item
    return None


async def _trigger_window_seen(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    lane_id: str,
    window_key: str,
) -> bool:
    rows = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "repo.trigger_lane",
                AuditLog.target_kind == "workspace_repo",
                AuditLog.target_id == str(repo_id),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(500)
        )
    ).scalars().all()
    return any(
        (row.payload or {}).get("lane_id") == lane_id
        and (row.payload or {}).get("window_key") == window_key
        for row in rows
    )


# ---------------------------------------------------------------------------
# Wizard seed read-back (P5-09 — post-onboarding "What just happened" page)
# ---------------------------------------------------------------------------
#
# The Wave-8c done step renders ``WizardSeedOut`` data: the merged-or-pending
# PR, CODEOWNERS-derived routing summary, intel handle, synthetic lane
# counts. Those fields are returned **once** by ``POST .../wizard_seed`` and
# then the FE persists them in ``sessionStorage`` so a tab refresh rerenders
# the page without another POST. The endpoints below are the durable
# fallback when sessionStorage is empty (the user reloaded the tab after
# closing it, opened the URL on another device, etc.) — we replay the most
# recent ``repo.wizard_seed`` audit log row as the same response shape.


@router.get(
    "/{repo_id}/wizard_seed/latest",
    response_model=WizardSeedOut,
)
async def get_latest_wizard_seed(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> WizardSeedOut:
    """Return the most recent ``WizardSeedOut`` payload for ``repo_id``.

    Sourced from the ``audit_log`` table (action ``repo.wizard_seed``)
    rather than a dedicated wizard-seed table — the audit log is the
    canonical record of the operation and already carries every field
    the FE needs. Returns ``404`` if this repo has never been seeded.

    Read-only; member role suffices (matches every other "what's the
    current state" GET on this router).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    row = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "repo.wizard_seed",
                AuditLog.target_kind == "workspace_repo",
                AuditLog.target_id == str(repo_id),
            )
            .order_by(AuditLog.id.desc())
            .limit(1)
        )
    ).scalars().first()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "no_wizard_seed",
                "message": (
                    "This repo has not been bootstrapped with the wizard yet."
                ),
            },
        )

    payload = row.payload or {}
    codeowners_payload = payload.get("codeowners")
    codeowners = (
        WizardSeedCodeownersSummary(**codeowners_payload)
        if isinstance(codeowners_payload, dict)
        else None
    )
    intel_payload = payload.get("intel")
    intel = (
        WizardSeedIntelHandle(**intel_payload)
        if isinstance(intel_payload, dict)
        else None
    )

    return WizardSeedOut(
        pr_url=payload.get("pr_url") or "",
        pr_number=int(payload.get("pr_number") or 0),
        branch=payload.get("branch") or "",
        files=list(payload.get("files") or []),
        presets=list(payload.get("presets") or []),
        knowledge_slugs=list(payload.get("knowledge_slugs") or []),
        tracker_kind=payload.get("tracker_kind"),
        run_token_prefix=payload.get("run_token_prefix"),
        run_token_rotated=bool(payload.get("run_token_rotated") or False),
        codeowners=codeowners,
        intel=intel,
        synthetic_lanes_created=int(payload.get("synthetic_lanes_created") or 0),
    )


# ---------------------------------------------------------------------------
# Repo-intel read-back + manual harvest (P5-09)
# ---------------------------------------------------------------------------


class RepoIntelOut(BaseModel):
    """Live ``RepoIntel`` snapshot for the post-onboarding done page.

    Mirrors :class:`backend.app.db.models.repo_intel.RepoIntel` minus
    workspace/repo plumbing already in the URL. Empty dicts/lists for
    payload columns mean "harvest succeeded, but the extractor found
    nothing" — readers must not treat empty as "no row" (the absence
    case is a 404 from the route).
    """

    intel_id: uuid.UUID
    version: int
    is_current: bool
    languages: dict[str, Any]
    frameworks: list[Any]
    package_managers: list[Any]
    entry_points: list[Any]
    structure: dict[str, Any]
    commit_style: dict[str, Any]
    visual_tokens: dict[str, Any]
    harvested_at: datetime
    harvested_by: str | None
    harvest_duration_ms: int | None
    harvest_error: str | None


@router.get(
    "/{repo_id}/intel/current",
    response_model=RepoIntelOut,
)
async def get_current_repo_intel(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RepoIntelOut:
    """Return the live :class:`RepoIntel` row for ``repo_id`` or 404.

    "Live" means ``is_current=TRUE`` — the same row the agent runners
    consume. The post-onboarding page polls this once the wizard
    handle reports ``intel.intel_id is None`` (harvest still queued)
    until either a row appears or the FE-side timeout elapses.
    """
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.services.repo_intel import get_current_intel

    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )

    row = await get_current_intel(session, repo_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "no_intel",
                "message": (
                    "No repo-intel snapshot has been harvested for this "
                    "repo yet. POST .../intel/harvest to trigger one."
                ),
            },
        )
    return RepoIntelOut(
        intel_id=row.id,
        version=row.version,
        is_current=row.is_current,
        languages=dict(row.languages or {}),
        frameworks=list(row.frameworks or []),
        package_managers=list(row.package_managers or []),
        entry_points=list(row.entry_points or []),
        structure=dict(row.structure or {}),
        commit_style=dict(row.commit_style or {}),
        visual_tokens=dict(row.visual_tokens or {}),
        harvested_at=row.harvested_at,
        harvested_by=row.harvested_by,
        harvest_duration_ms=row.harvest_duration_ms,
        harvest_error=row.harvest_error,
    )


class RepoIntelHarvestOut(BaseModel):
    """Dispatch handle returned by ``POST .../intel/harvest`` (P5-09)."""

    enqueued: bool
    job_id: str | None = None
    intel_id: uuid.UUID | None = None


@router.post(
    "/{repo_id}/intel/harvest",
    response_model=RepoIntelHarvestOut,
)
async def trigger_repo_intel_harvest(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RepoIntelHarvestOut:
    """Manually re-trigger the intel harvest (P5-09 retry button).

    Reuses :func:`_dispatch_intel_harvest` for parity with the wizard's
    own enqueue path, so the FE doesn't need to branch on whether the
    deployment runs an arq worker. Admin-only — re-harvesting touches
    GitHub APIs and writes a new ``RepoIntel`` row.
    """
    from backend.app.db.models.integrations import WorkspaceRepo

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )

    handle = await _dispatch_intel_harvest(
        request=request,
        session=session,
        workspace_id=workspace_id,
        repo_id=repo_row.id,
    )
    return RepoIntelHarvestOut(
        enqueued=handle.enqueued,
        job_id=handle.job_id,
        intel_id=handle.intel_id,
    )


# ---------------------------------------------------------------------------
# Disconnect (B6)
# ---------------------------------------------------------------------------


class DisconnectRepoOut(BaseModel):
    """Summary of what the disconnect call wiped, for the UI toast."""

    repo_id: uuid.UUID
    full_name: str
    deleted_pipelines: int
    deleted_runs: int


@router.delete("/{repo_id}", response_model=DisconnectRepoOut)
async def disconnect_repo(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> DisconnectRepoOut:
    """Unwire Ship from ``repo`` — deletes the row and every lane bound to it.

    Admin-only. The deletion order matters because ``Pipeline.repo_id``
    is declared ``ondelete=SET NULL`` (so we can keep workspace-level
    lanes alive when a *different* repo in the same workspace gets
    disconnected). When the operator explicitly disconnects a repo
    they want the Ship state gone, not nulled out — so we:

    1. Collect every ``Pipeline`` with ``repo_id == repo_id``.
    2. Delete those pipelines (cascades to ``PipelineRun``).
    3. Delete the ``WorkspaceRepo`` row itself.
    4. Record an :class:`AuditLog` entry with the tallies.

    We deliberately do **not** touch github.com:

    - Removing the repo from the App's ``selected_repositories`` list
      requires a user-initiated flow in GitHub's UI.
    - The workflow YAMLs our install PR added live under version
      control in the customer repo — the customer owns them now.

    The UI makes both caveats obvious in the confirmation modal.
    """
    from backend.app.db.models.pipelines import Pipeline, PipelineRun

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )

    full_name = repo_row.full_name
    pipeline_ids = (
        await session.execute(
            select(Pipeline.id).where(Pipeline.repo_id == repo_row.id)
        )
    ).scalars().all()

    run_count = 0
    if pipeline_ids:
        run_count = (
            await session.execute(
                select(PipelineRun).where(PipelineRun.pipeline_id.in_(pipeline_ids))
            )
        ).scalars().all()
        run_count = len(run_count)

        # SQLAlchemy's async session doesn't play well with DELETE …
        # RETURNING under some drivers; do it with the ORM so cascades
        # still fire for dependent ``PipelineRun`` rows.
        pipelines_to_delete = (
            await session.execute(
                select(Pipeline).where(Pipeline.id.in_(pipeline_ids))
            )
        ).scalars().all()
        for p in pipelines_to_delete:
            await session.delete(p)

    await session.delete(repo_row)
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.disconnect",
            target_kind="workspace_repo",
            target_id=str(repo_id),
            payload={
                "full_name": full_name,
                "deleted_pipelines": len(pipeline_ids),
                "deleted_runs": run_count,
            },
        )
    )
    await session.flush()

    return DisconnectRepoOut(
        repo_id=repo_id,
        full_name=full_name,
        deleted_pipelines=len(pipeline_ids),
        deleted_runs=run_count,
    )


# ---------------------------------------------------------------------------
# Lanes config editor — single-file read + propose-PR write
# ---------------------------------------------------------------------------
#
# The console Library tab round-trips ``.ship/config.yml`` through
# these two endpoints:
#
# - ``GET /config`` — live fetch of the raw YAML plus a best-effort
#   parse and the vendor's blob SHA (for optimistic locking). The
#   DB projection (``Lane`` rows) is intentionally *not* used here
#   because the editor needs the author's exact bytes — whitespace,
#   comments, key order — not the normalised view ``lanes_sync``
#   produces.
# - ``POST /config/propose`` — accepts an edited ``lanes`` mapping,
#   re-emits the YAML via ``catalog_service.emit_config_yaml``, and
#   opens a single-file PR with ``commit_bundle_pr``. The request
#   carries the blob SHA the editor loaded against so we can reject
#   drift with 409 (the operator re-loads and re-bases their edits).


class LaneTriggerIn(BaseModel):
    """One lane's trigger block on ``POST /config/propose``.

    Mirrors what ``lanes_sync`` expects to find under
    ``.ship/config.yml → lanes.<lane_id>``: exactly one of
    ``once`` / ``event`` / ``schedule`` is required. We keep both
    ``cron``-style (``schedule``) and pattern/idempotency metadata
    flat here so the emitter can copy them straight through.

    RFC-0008 C3.1 introduced the canonical ``patterns: [ids]`` list
    for multi-pattern lanes; ``pattern: <id>`` stays as the
    single-pattern alias. The console still posts ``pattern`` for
    single-pattern lanes — that keeps existing diffs minimal — and
    the emitter prefers ``patterns`` only when the list has more
    than one entry.
    """

    once: str | None = None
    event: str | None = None
    schedule: str | None = None
    pattern: str | None = None
    patterns: list[str] | None = None
    # RFC-0008 C3.2 — fan-out strategy for multi-pattern lanes. Only
    # meaningful when ``patterns`` has ≥2 entries; validator below
    # surfaces a clear error if an unknown value is sent.
    fanout: str | None = None
    idempotency_key: str | None = None


class RepoConfigOut(BaseModel):
    """Shape returned by ``GET /{repo_id}/config``."""

    repo_id: uuid.UUID
    repo_full_name: str
    default_branch: str
    # ``sha`` is the GitHub blob SHA for ``.ship/config.yml``.
    # ``null`` iff ``exists=False`` — the client must post a
    # ``base_sha`` of ``null`` in that case to signal "create this
    # file for the first time".
    exists: bool
    sha: str | None
    raw_yaml: str | None
    # ``parsed`` is the best-effort ``yaml.safe_load`` result; the
    # editor uses this for the "draft state" seed but always writes
    # back via the raw bytes generated by ``emit_config_yaml`` to
    # keep round-tripping safe. ``null`` when YAML fails to parse —
    # ``parse_error`` is set in that case.
    parsed: dict[str, Any] | None
    parse_error: str | None = None


class RepoConfigProposeIn(BaseModel):
    """Body for ``POST /{repo_id}/config/propose``.

    ``base_sha`` is what the editor saw when it rendered the form —
    used for optimistic locking. Pass ``null`` to create the file
    from scratch. ``lanes`` is the authoritative edited mapping.
    ``change_summary`` is a short human note rendered into the PR
    body so reviewers get context without opening the editor.
    """

    lanes: dict[str, LaneTriggerIn]
    process: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional top-level ``process:`` FSM editor payload. When omitted, "
            "the generated config keeps the legacy lanes-only shape."
        ),
    )
    base_sha: str | None
    change_summary: str = Field(
        default="",
        max_length=1024,
        description=(
            "One-line description of why this change was made — shown "
            "in the PR body so reviewers get context."
        ),
    )
    preset: str | None = Field(
        default=None,
        description=(
            "Override the preset label in ``.ship/config.yml``. Omit "
            "to keep whatever the repo has persisted today."
        ),
    )


class RepoConfigProposeOut(BaseModel):
    pr_url: str
    pr_number: int
    branch: str


_LANES_CONFIG_PATH = ".ship/config.yml"
_PROCESS_AGENT_PROFILES = frozenset(
    {
        "auto",
        "main",
        "cheaper",
        "cursor_agent",
        "codex_cli",
        "ship_cloud_agent",
        "local_cli",
    }
)
_PROCESS_TRIGGER_TYPES = frozenset({"manual", "event", "schedule"})


def _validate_process_config(process: dict[str, Any]) -> None:
    """Minimal guardrail for the repo-backed FSM editor payload."""

    if not isinstance(process.get("id"), str) or not process["id"].strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": "process.id must be a non-empty string",
            },
        )
    if not isinstance(process.get("name"), str) or not process["name"].strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": "process.name must be a non-empty string",
            },
        )
    if not isinstance(process.get("primary"), bool):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": "process.primary must be a boolean",
            },
        )
    states = process.get("states")
    if not isinstance(states, list) or not states:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": "process.states must contain at least one state",
            },
        )
    state_ids: set[str] = set()
    for index, state_obj in enumerate(states):
        if not isinstance(state_obj, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"process.states[{index}] must be an object",
                },
            )
        state_id = state_obj.get("id")
        if not isinstance(state_id, str) or not state_id.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"process.states[{index}].id must be a non-empty string",
                },
            )
        if state_id in state_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"process.states contains duplicate id {state_id!r}",
                },
            )
        state_ids.add(state_id)
        state_name = state_obj.get("name")
        if state_name is not None and (
            not isinstance(state_name, str) or not state_name.strip()
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": (
                        f"process.states[{index}].name must be a non-empty string"
                    ),
                },
            )
        specialist = state_obj.get("specialist")
        if specialist is not None:
            if not isinstance(specialist, dict):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": (
                            f"process.states[{index}].specialist must be an object"
                        ),
                    },
                )
            specialist_id = specialist.get("id")
            if specialist_id is not None and (
                not isinstance(specialist_id, str) or not specialist_id.strip()
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": (
                            f"process.states[{index}].specialist.id must be "
                            "a non-empty string"
                        ),
                    },
                )
            specialist_name = specialist.get("name")
            if specialist_name is not None and (
                not isinstance(specialist_name, str) or not specialist_name.strip()
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": (
                            f"process.states[{index}].specialist.name must be "
                            "a non-empty string"
                        ),
                    },
                )
            _validate_process_agent_profile(
                specialist.get("agent_profile"),
                f"process.states[{index}].specialist.agent_profile",
            )
        _validate_process_agent_profile(
            state_obj.get("agent_profile"),
            f"process.states[{index}].agent_profile",
        )
        _validate_process_triggers(
            state_obj.get("triggers"),
            f"process.states[{index}].triggers",
        )
        _validate_process_conditions(
            state_obj.get("exit_conditions"),
            f"process.states[{index}].exit_conditions",
        )
        _validate_process_conditions(
            state_obj.get("block_conditions"),
            f"process.states[{index}].block_conditions",
        )
        layout = state_obj.get("layout")
        if layout is not None:
            x_value = layout.get("x") if isinstance(layout, dict) else None
            y_value = layout.get("y") if isinstance(layout, dict) else None
            if (
                not isinstance(layout, dict)
                or isinstance(x_value, bool)
                or isinstance(y_value, bool)
                or not isinstance(x_value, (int, float))
                or not isinstance(y_value, (int, float))
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": (
                            f"process.states[{index}].layout must contain "
                            "numeric x and y values"
                        ),
                    },
                )

    transitions = process.get("transitions")
    if transitions is not None and not isinstance(transitions, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": "process.transitions must be a list when provided",
            },
        )
    for index, transition in enumerate(transitions or []):
        if not isinstance(transition, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"process.transitions[{index}] must be an object",
                },
            )
        from_state = transition.get("from")
        to_state = transition.get("to")
        if from_state not in state_ids or to_state not in state_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": (
                        f"process.transitions[{index}] must reference existing "
                        "state ids"
                    ),
                },
            )
        condition = transition.get("condition")
        if condition is not None and (
            not isinstance(condition, str) or not condition.strip()
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": (
                        f"process.transitions[{index}].condition must be "
                        "a non-empty string"
                    ),
                },
            )

    _validate_process_routines(process.get("routines"))


def _validate_process_agent_profile(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or value not in _PROCESS_AGENT_PROFILES:
        allowed = "|".join(sorted(_PROCESS_AGENT_PROFILES))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": f"{field} must be one of {allowed}",
            },
        )


def _validate_process_triggers(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": f"{field} must be a list when provided",
            },
        )
    for index, trigger in enumerate(value):
        if not isinstance(trigger, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"{field}[{index}] must be an object",
                },
            )
        trigger_type = trigger.get("type")
        if trigger_type not in _PROCESS_TRIGGER_TYPES:
            allowed = "|".join(sorted(_PROCESS_TRIGGER_TYPES))
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"{field}[{index}].type must be one of {allowed}",
                },
            )
        if trigger_type == "schedule":
            _require_optional_string(
                trigger.get("interval"),
                f"{field}[{index}].interval",
                required=True,
            )
        elif trigger_type == "event":
            _require_optional_string(
                trigger.get("event"),
                f"{field}[{index}].event",
                required=True,
            )


def _validate_process_conditions(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": f"{field} must be a list when provided",
            },
        )
    for index, condition in enumerate(value):
        if not isinstance(condition, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"{field}[{index}] must be an object",
                },
            )
        _require_optional_string(
            condition.get("expression"),
            f"{field}[{index}].expression",
            required=True,
        )


def _validate_process_routines(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": "process.routines must be a list when provided",
            },
        )
    for index, routine in enumerate(value):
        if not isinstance(routine, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"process.routines[{index}] must be an object",
                },
            )
        _require_optional_string(
            routine.get("id"),
            f"process.routines[{index}].id",
            required=True,
        )
        _require_optional_string(
            routine.get("name"),
            f"process.routines[{index}].name",
            required=True,
        )
        _require_optional_string(
            routine.get("cadence"),
            f"process.routines[{index}].cadence",
            required=False,
        )


def _require_optional_string(value: Any, field: str, *, required: bool) -> None:
    if value is None and not required:
        return
    if not isinstance(value, str) or not value.strip():
        requirement = "a non-empty string" if required else "null or a non-empty string"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": f"{field} must be {requirement}",
            },
        )


@router.get("/{repo_id}/config", response_model=RepoConfigOut)
async def read_repo_config(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RepoConfigOut:
    """Return the live ``.ship/config.yml`` for a repo.

    Reads from GitHub (not the DB) so the editor always starts from
    what's on ``default_branch`` right now — drift between the DB
    projection and the YAML on disk is exactly what this surface is
    meant to surface. ``sha`` doubles as the optimistic-locking
    token the write endpoint expects.
    """
    import yaml as _yaml

    from backend.app.integrations.gateway.code_host import RepoRef

    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )
    if repo_row.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repo is not backed by a GitHub App installation.",
        )

    install = await session.get(GitHubInstallation, repo_row.installation_id)
    if install is None or install.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "GitHub App installation for this repo is missing or "
                "suspended. Reinstall the Ship app."
            ),
        )

    owner, _, name = repo_row.full_name.partition("/")
    ref = RepoRef(kind="github", owner=owner, repo=name)
    gateway = GitHubCodeHost(install.installation_id, settings=settings)

    try:
        blob = await gateway.get_blob(
            ref, path=_LANES_CONFIG_PATH, ref_sha=repo_row.default_branch
        )
    except FileNotFoundError:
        return RepoConfigOut(
            repo_id=repo_row.id,
            repo_full_name=repo_row.full_name,
            default_branch=repo_row.default_branch,
            exists=False,
            sha=None,
            raw_yaml=None,
            parsed=None,
        )
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "github_unreachable",
                "upstream_status": exc.response.status_code,
                "message": (
                    "GitHub rejected the request to read "
                    f"{_LANES_CONFIG_PATH}."
                ),
            },
        ) from exc

    raw = blob.content if blob.encoding == "utf-8" else None
    parsed: dict[str, Any] | None = None
    parse_error: str | None = None
    if raw is not None:
        try:
            loaded = _yaml.safe_load(raw) or {}
            if isinstance(loaded, dict):
                parsed = loaded
            else:
                parse_error = "config root is not a mapping"
        except _yaml.YAMLError as exc:
            parse_error = f"yaml parse: {exc}"

    return RepoConfigOut(
        repo_id=repo_row.id,
        repo_full_name=repo_row.full_name,
        default_branch=repo_row.default_branch,
        exists=True,
        sha=blob.sha,
        raw_yaml=raw,
        parsed=parsed,
        parse_error=parse_error,
    )


@router.post("/{repo_id}/config/propose", response_model=RepoConfigProposeOut)
async def propose_repo_config(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: RepoConfigProposeIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> RepoConfigProposeOut:
    """Open a single-file PR that rewrites ``.ship/config.yml``.

    Admin-only. Accepts an edited lane mapping + the blob SHA the
    editor saw when rendering the form. If GitHub's current SHA
    disagrees we return ``409`` with ``code=sha_mismatch`` so the
    console can surface a "HEAD moved — reload" banner and dispatch
    the operator through the editor again.
    """
    # Lazy imports for the same reason ``install_bundle`` uses them:
    # keep the hot read paths (code-map, availability) free of the
    # workflow / YAML helpers.
    from backend.app.integrations.gateway.code_host import RepoRef
    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        commit_bundle_pr,
    )
    from backend.app.services import catalog as catalog_service

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )
    if repo_row.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App isn't installed for this repo. "
                    "Reconnect it before opening a config PR."
                ),
            },
        )
    install_row = await session.get(GitHubInstallation, repo_row.installation_id)
    if install_row is None or install_row.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App installation is missing or "
                    "suspended. Reinstall the Ship app."
                ),
            },
        )

    # ------ validate the edited mapping before any network calls ----
    if not payload.lanes and not payload.process:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "empty_lanes",
                "message": (
                    "Proposal has no lanes or process definition — use a "
                    "delete-config flow if that's intentional."
                ),
            },
        )
    normalised: dict[str, dict[str, Any]] = {}
    for lane_id, trigger in payload.lanes.items():
        if not re.match(r"^[a-z][a-z0-9_-]{0,62}$", lane_id or ""):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_lane_id",
                    "message": f"lane id {lane_id!r} does not match slug rule",
                },
            )
        trigger_kinds = [
            k
            for k, v in (
                ("once", trigger.once),
                ("event", trigger.event),
                ("schedule", trigger.schedule),
            )
            if v
        ]
        if len(trigger_kinds) != 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_trigger",
                    "message": (
                        f"lane {lane_id!r} must set exactly one of "
                        "once/event/schedule"
                    ),
                },
            )
        # ``patterns`` (list) and ``pattern`` (single alias) are
        # mutually exclusive; reject a payload that sends both so
        # downstream YAML can't silently drop one.
        patterns_list: list[str] | None = None
        if trigger.patterns is not None and trigger.pattern is not None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_pattern_shape",
                    "message": (
                        f"lane {lane_id!r}: send either 'pattern' "
                        "(single) or 'patterns' (list), not both"
                    ),
                },
            )
        if trigger.patterns is not None:
            patterns_list = [
                p.strip() for p in trigger.patterns if isinstance(p, str) and p.strip()
            ]
            if not patterns_list:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_patterns",
                        "message": (
                            f"lane {lane_id!r}: 'patterns' must contain "
                            "at least one pattern id"
                        ),
                    },
                )

        # Preserve insertion order: trigger first, then pattern(s),
        # then idempotency_key so diffs stay minimal and predictable.
        # Emit ``pattern:`` (single) for the common one-entry case so
        # existing configs get byte-identical round-trips; only
        # surface ``patterns:`` (list) when the lane really has >1
        # pattern. A caller that explicitly sent ``patterns: [only]``
        # gets the single-form back — the list shape has no value on
        # disk for one entry and churn-free diffs win.
        flat: dict[str, Any] = {trigger_kinds[0]: getattr(trigger, trigger_kinds[0])}
        if patterns_list is not None:
            if len(patterns_list) == 1:
                flat["pattern"] = patterns_list[0]
            else:
                flat["patterns"] = list(patterns_list)
        elif trigger.pattern is not None:
            flat["pattern"] = trigger.pattern
        # RFC-0008 C3.2 — fanout applies only to multi-pattern lanes.
        # Skip it for single-pattern lanes (even when the caller sent
        # one) to keep diffs clean; reject unknown modes with a 422 so
        # the editor doesn't silently produce a config the CLI will
        # refuse to validate.
        if trigger.fanout is not None:
            if trigger.fanout not in ("matrix", "sequential", "concurrent"):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_fanout",
                        "message": (
                            f"lane {lane_id!r}: 'fanout' must be one of "
                            "matrix|sequential|concurrent"
                        ),
                    },
                )
            effective_pattern_count = (
                len(patterns_list)
                if patterns_list is not None
                else (1 if trigger.pattern is not None else 0)
            )
            # Only emit fanout when it's meaningful (≥2 patterns) and
            # not the default ``matrix``. A single-pattern lane that
            # sets fanout=matrix renders to a no-op key, so omit it.
            if effective_pattern_count >= 2 and trigger.fanout != "matrix":
                flat["fanout"] = trigger.fanout
        if trigger.idempotency_key is not None:
            flat["idempotency_key"] = trigger.idempotency_key
        normalised[lane_id] = flat

    if payload.process is not None:
        _validate_process_config(payload.process)

    # ------ optimistic-locking check against the live blob ---------
    owner, _, name = repo_row.full_name.partition("/")
    ref = RepoRef(kind="github", owner=owner, repo=name)
    gateway = GitHubCodeHost(install_row.installation_id, settings=settings)
    current_sha: str | None = None
    try:
        current_blob = await gateway.get_blob(
            ref, path=_LANES_CONFIG_PATH, ref_sha=repo_row.default_branch
        )
        current_sha = current_blob.sha
    except FileNotFoundError:
        current_sha = None
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "github_unreachable",
                "upstream_status": exc.response.status_code,
                "message": "GitHub rejected the base-SHA check.",
            },
        ) from exc

    if current_sha != payload.base_sha:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "sha_mismatch",
                "message": (
                    "``.ship/config.yml`` moved since the editor loaded "
                    "it. Reload and reapply your edits."
                ),
                "current_sha": current_sha,
                "base_sha": payload.base_sha,
            },
        )

    # ------ emit + PR -----------------------------------------------
    new_yaml = catalog_service.emit_config_yaml(
        preset_id=payload.preset or repo_row.preset,
        repo_full_name=repo_row.full_name,
        lanes=normalised,
        process=payload.process,
    )

    pr_body_header = (
        "This PR updates your Ship lane configuration. Merge to take "
        "effect; the dashboard re-syncs automatically via the push "
        "webhook."
    )
    if payload.change_summary:
        pr_body_header += f"\n\n> {payload.change_summary}"

    try:
        result = await commit_bundle_pr(
            repo_row,
            install_row,
            files=[(_LANES_CONFIG_PATH, new_yaml)],
            title="Ship: update lane configuration",
            branch_label="lanes-config",
            pr_body_header=pr_body_header,
            settings=settings,
            return_url=(
                f"{settings.console_url.rstrip('/')}/lanes?tab=library"
                "&reason=pr_opened"
            ),
        )
    except WorkflowDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "propose_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
            },
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.config_propose",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload={
                "pr_number": result.pr_number,
                "pr_url": result.pr_url,
                "branch": result.branch,
                "lanes": sorted(normalised.keys()),
                "process": payload.process.get("id") if payload.process else None,
                "base_sha": payload.base_sha,
                "change_summary": payload.change_summary or None,
            },
        )
    )
    await session.flush()

    return RepoConfigProposeOut(
        pr_url=result.pr_url,
        pr_number=result.pr_number,
        branch=result.branch,
    )


# ---------------------------------------------------------------------------
# Custom lane author (Phase 3 of RFC-0007 lanes/requests)
# ---------------------------------------------------------------------------


class CustomLaneProposeIn(BaseModel):
    """Body for ``POST /{repo_id}/lanes/propose``.

    The Console's ``/lanes?tab=new`` form collects:

    - ``lane_id`` — slug that becomes the key under ``lanes:``, the
      workflow filename (``ship-<slug>.yml``), and the prompt file
      path (``.ship/lanes/<slug>.md``).
    - ``agent_slug`` — currently informational (surfaced to the
      reviewer in the PR body). The custom-lane workflow doesn't
      wire the agent choice directly yet; the prompt file is where
      operators describe what the lane does.
    - ``schedule`` — cron. Required because the MVP only supports
      scheduled lanes; event-driven custom lanes land in a follow-up.
    - ``prompt`` — becomes ``.ship/lanes/<slug>.md``.
    - ``base_sha`` — optimistic lock, same semantics as
      ``RepoConfigProposeIn.base_sha``.
    """

    lane_id: str = Field(..., min_length=1, max_length=63)
    agent_slug: str = Field(..., min_length=1, max_length=64)
    schedule: str = Field(..., min_length=1, max_length=128)
    prompt: str = Field(..., min_length=1, max_length=8192)
    base_sha: str | None = None
    change_summary: str = Field(default="", max_length=1024)


class CustomLaneProposeOut(BaseModel):
    pr_url: str
    pr_number: int
    branch: str


@router.post(
    "/{repo_id}/lanes/propose", response_model=CustomLaneProposeOut
)
async def propose_custom_lane(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: CustomLaneProposeIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CustomLaneProposeOut:
    """Open a PR that adds a brand-new lane to ``.ship/config.yml``.

    Admin-only. Emits three files in a single commit:

    1. ``.github/workflows/ship-<slug>.yml`` — rendered from the
       ``custom-lane.yml`` starter template with ``{{LANE_SLUG}}`` /
       ``{{LANE_SCHEDULE}}`` substitutions.
    2. ``.ship/lanes/<slug>.md`` — the prompt / system instruction.
    3. ``.ship/config.yml`` — appends the new lane to the existing
       mapping (or creates the file if absent). Uses the same
       optimistic-lock semantics as ``config/propose``.
    """
    from backend.app.integrations.gateway.code_host import RepoRef
    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        commit_bundle_pr,
    )
    from backend.app.services import catalog as catalog_service

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    if not re.match(r"^[a-z][a-z0-9_-]{0,62}$", payload.lane_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_lane_id",
                "message": (
                    "lane_id must be a lowercase slug (letters, digits, "
                    "`_`, `-`) starting with a letter."
                ),
            },
        )

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )
    if repo_row.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App isn't installed for this repo. "
                    "Reconnect it before opening a lane PR."
                ),
            },
        )
    install_row = await session.get(GitHubInstallation, repo_row.installation_id)
    if install_row is None or install_row.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App installation is missing or "
                    "suspended. Reinstall the Ship app."
                ),
            },
        )

    # --- Load + merge existing config.yml ------------------------------
    owner, _, name = repo_row.full_name.partition("/")
    ref = RepoRef(kind="github", owner=owner, repo=name)
    gateway = GitHubCodeHost(install_row.installation_id, settings=settings)
    current_sha: str | None = None
    existing_lanes: dict[str, dict[str, Any]] = {}
    try:
        current_blob = await gateway.get_blob(
            ref, path=_LANES_CONFIG_PATH, ref_sha=repo_row.default_branch
        )
        current_sha = current_blob.sha
        import yaml as _yaml

        parsed = _yaml.safe_load(current_blob.decode("utf-8")) or {}
        if isinstance(parsed, dict):
            raw_lanes = parsed.get("lanes") or {}
            if isinstance(raw_lanes, dict):
                for k, v in raw_lanes.items():
                    if isinstance(v, dict):
                        existing_lanes[str(k)] = {str(kk): vv for kk, vv in v.items()}
    except FileNotFoundError:
        current_sha = None
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "github_unreachable",
                "upstream_status": exc.response.status_code,
                "message": "GitHub rejected the base-SHA check.",
            },
        ) from exc

    if current_sha != payload.base_sha:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "sha_mismatch",
                "message": (
                    "``.ship/config.yml`` moved since the editor loaded "
                    "it. Reload and reapply your edits."
                ),
                "current_sha": current_sha,
                "base_sha": payload.base_sha,
            },
        )

    if payload.lane_id in existing_lanes:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "lane_exists",
                "message": (
                    f"lane {payload.lane_id!r} already exists — pick a "
                    "different slug or edit via the Library tab."
                ),
            },
        )

    # --- Render the starter files --------------------------------------
    # ``custom-lane.yml`` is a template (not a registered starter) that
    # lives alongside the other starter YAMLs. Read it raw and
    # substitute the per-lane placeholders.
    from pathlib import Path

    template_path = (
        Path(__file__).resolve().parents[3]
        / "resources"
        / "starter_workflows"
        / "custom-lane.yml"
    )
    if not template_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="custom-lane.yml starter template is missing",
        )
    workflow_body = (
        template_path.read_text(encoding="utf-8")
        .replace("{{LANE_SLUG}}", payload.lane_id)
        .replace("{{LANE_SCHEDULE}}", payload.schedule)
    )
    workflow_path = f".github/workflows/ship-{payload.lane_id}.yml"
    prompt_path = f".ship/lanes/{payload.lane_id}.md"
    prompt_body = (
        f"# Lane: {payload.lane_id}\n\n"
        f"Agent: `{payload.agent_slug}`\n\n"
        "## Instruction\n\n"
        f"{payload.prompt.strip()}\n"
    )

    # Merge existing lanes + new one; emit fresh YAML.
    merged: dict[str, Any] = dict(existing_lanes)
    merged[payload.lane_id] = {
        "schedule": payload.schedule,
        "idempotency_key": f"custom-{payload.lane_id}" "-{{date}}",
    }
    new_config_yaml = catalog_service.emit_config_yaml(
        preset_id=repo_row.preset,
        repo_full_name=repo_row.full_name,
        lanes=merged,
    )

    files = [
        (workflow_path, workflow_body),
        (prompt_path, prompt_body),
        (_LANES_CONFIG_PATH, new_config_yaml),
    ]

    pr_body_header = (
        f"This PR adds a new custom lane `{payload.lane_id}` "
        f"to your Ship configuration.\n\n"
        f"- Workflow: `{workflow_path}`\n"
        f"- Prompt: `{prompt_path}`\n"
        f"- Agent: `{payload.agent_slug}`\n"
        f"- Schedule: `{payload.schedule}`\n"
    )
    if payload.change_summary:
        pr_body_header += f"\n> {payload.change_summary}"

    try:
        result = await commit_bundle_pr(
            repo_row,
            install_row,
            files=files,
            title=f"Ship: add custom lane `{payload.lane_id}`",
            branch_label=f"custom-lane-{payload.lane_id}",
            pr_body_header=pr_body_header,
            settings=settings,
            return_url=(
                f"{settings.console_url.rstrip('/')}/lanes?tab=active"
            ),
        )
    except WorkflowDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "propose_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
            },
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.custom_lane_propose",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload={
                "pr_number": result.pr_number,
                "pr_url": result.pr_url,
                "branch": result.branch,
                "lane_id": payload.lane_id,
                "agent_slug": payload.agent_slug,
                "schedule": payload.schedule,
                "base_sha": payload.base_sha,
            },
        )
    )
    await session.flush()

    return CustomLaneProposeOut(
        pr_url=result.pr_url,
        pr_number=result.pr_number,
        branch=result.branch,
    )
