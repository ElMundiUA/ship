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

import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
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
from backend.app.services.default_pipelines import (
    KNOWN_PRESETS,
    seed_default_pipelines,
)
from backend.app.services.seed_bundle import BUNDLE_VERSION as _BUNDLE_VERSION


router = APIRouter(
    prefix="/workspaces/{workspace_id}/repos",
    tags=["repos"],
)


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
            "Catalog preset id to attach to the activated repo(s). One of "
            "``web-app`` / ``api-backend`` / ``mobile-app`` / ``cli`` / "
            "``monorepo`` / ``adoption-minimum``. ``None`` keeps any "
            "existing preset and falls back to ``adoption-minimum``-shaped "
            "defaults for new rows. Setting a preset on a subsequent "
            "activation call updates the stored value but never re-seeds "
            "lanes a tenant already customised."
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

    preset = payload.preset
    if preset is not None and preset not in KNOWN_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown preset {preset!r}. Expected one of: "
                f"{sorted(KNOWN_PRESETS)}"
            ),
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
    # original pick.
    seed_preset = preset
    if seed_preset is None and default_repo_id is not None:
        default_row = await session.get(WorkspaceRepo, default_repo_id)
        if default_row is not None:
            seed_preset = default_row.preset

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
    # list, else fall back to the repo's persisted preset. Validate
    # every id against ``KNOWN_PRESETS`` so a stale UI can't sneak
    # unknown values into the YAML resolver.
    requested = payload.presets if payload and payload.presets else None
    if requested is None:
        requested = [repo_row.preset] if repo_row.preset else []
    cleaned: list[str] = []
    seen: set[str] = set()
    for pid in requested:
        pid = pid.strip()
        if not pid or pid in seen:
            continue
        if pid not in KNOWN_PRESETS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Unknown preset {pid!r}. Expected one of: "
                    f"{sorted(KNOWN_PRESETS)}"
                ),
            )
        cleaned.append(pid)
        seen.add(pid)
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

    # Stamp the bundle version so the dashboard can tell "up to date"
    # from "upgrade available" next render.
    repo_row.installed_bundle_version = _BUNDLE_VERSION

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
                "bundle_version": _BUNDLE_VERSION,
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

    Admin-only. The ``preset`` must be one of ``KNOWN_PRESETS`` (or
    ``None`` to clear the binding and fall back to the Day-3 default
    shape on future seeds).

    Behavioural notes:

    - If ``reshape`` is true the new preset's
      ``PRESET_ENABLED_KINDS`` is applied to every ``Pipeline`` in
      the workspace whose ``repo_id`` matches this row. Workspace-
      level (unbound) lanes are untouched — they keep their hand-
      toggled state because they're shared across repos.
    - The seed helper is invoked afterwards so a tenant that picked
      e.g. ``monorepo`` later gets the ``self_heal`` lane created if
      it didn't exist yet.
    - Every call records an ``AuditLog`` entry with the old / new
      preset + counts so we can trace "why did my code_map lane
      flip on overnight" later.
    """
    from backend.app.db.models.pipelines import Pipeline
    from backend.app.services.default_pipelines import (
        PRESET_ENABLED_KINDS,
        resolve_enabled_kinds,
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

    new_preset = payload.preset
    if new_preset is not None and new_preset not in KNOWN_PRESETS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Unknown preset '{new_preset}'. Expected one of:"
                f" {', '.join(KNOWN_PRESETS)}."
            ),
        )

    old_preset = repo_row.preset
    repo_row.preset = new_preset

    reshape_applied = 0
    if payload.reshape and new_preset is not None:
        # Limit reshape to lanes actually bound to this repo; shared
        # workspace-level lanes stay untouched (they may be driving
        # other repos in the same workspace).
        enabled_kinds = PRESET_ENABLED_KINDS.get(
            new_preset, resolve_enabled_kinds(new_preset)
        )
        bound_lanes = (
            await session.execute(
                select(Pipeline).where(Pipeline.repo_id == repo_row.id)
            )
        ).scalars().all()
        for lane in bound_lanes:
            desired = lane.kind in enabled_kinds
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
# one PR carrying the preset workflows, ``.ship/config.yml``, optional
# knowledge starters, and the tracker FSM doc. Also mints the long-
# lived ``SHIP_RUN_TOKEN`` Actions secret *before* opening the PR so
# the lanes the PR installs can authenticate the moment the merge
# fires their first schedule tick. Plaintext never touches the DB —
# see ``services.repo_tokens`` for the hash-only persistence story.
# ---------------------------------------------------------------------------


class WizardSeedIn(BaseModel):
    """Body for ``POST /workspaces/{ws}/repos/{repo_id}/wizard_seed``."""

    presets: list[str] | None = Field(
        default=None,
        description=(
            "Preset ids to bundle. Defaults to the repo's persisted "
            "preset; pass multiple to combine (same semantics as "
            "install_bundle)."
        ),
    )
    knowledge_slugs: list[str] | None = Field(
        default=None,
        description=(
            "Knowledge starter slugs to seed. ``null`` seeds every "
            "catalog entry; ``[]`` skips knowledge seeding."
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


class WizardSeedOut(BaseModel):
    pr_url: str
    pr_number: int
    branch: str
    files: list[str]
    presets: list[str]
    knowledge_slugs: list[str]
    tracker_kind: str | None = None
    run_token_prefix: str | None = None
    run_token_rotated: bool = False


@router.post("/{repo_id}/wizard_seed", response_model=WizardSeedOut)
async def wizard_seed(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: WizardSeedIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WizardSeedOut:
    """Open the single wizard seed PR for a repo.

    Admin-only. The flow is:

    1. Resolve the preset list (explicit or repo-persisted) and reject
       unknown ids with 422 so a stale wizard tab can't smuggle values
       past ``KNOWN_PRESETS``.
    2. Resolve the tracker kind (body override, else the per-repo
       binding, else the workspace default).
    3. Mint a fresh ``SHIP_RUN_TOKEN`` if one doesn't exist or if the
       caller asked to rotate. Plaintext is PUT to GitHub Actions
       *before* the PR opens so the workflows installed by the PR can
       authenticate on their first tick. On any failure here the PR is
       never opened — a PR without the secret would silently break
       every schedule-triggered lane it installs.
    4. Compose the file list via ``services.seed_bundle.compose_seed_files``.
    5. Open one PR via ``commit_bundle_pr``.
    6. Audit-log the wizard seed with every file path (no plaintext).
    """

    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        commit_bundle_pr,
    )
    from backend.app.services.repo_tokens import mint_repo_callback_token
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

    # ── Resolve presets (wizard override → repo persisted) ────────
    requested = payload.presets if payload.presets else None
    if requested is None:
        requested = [repo_row.preset] if repo_row.preset else []
    cleaned: list[str] = []
    seen: set[str] = set()
    for pid in requested:
        pid = (pid or "").strip()
        if not pid or pid in seen:
            continue
        if pid not in KNOWN_PRESETS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Unknown preset {pid!r}. Expected one of: "
                    f"{sorted(KNOWN_PRESETS)}"
                ),
            )
        cleaned.append(pid)
        seen.add(pid)
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "preset_required",
                "message": (
                    "Pass at least one preset or set it on the repo before "
                    "opening the wizard seed PR."
                ),
            },
        )

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

    # ── Compose the file bundle (pure) ────────────────────────────
    bundle = compose_seed_files(
        presets=cleaned,
        knowledge_slugs=payload.knowledge_slugs,
        tracker_kind=tracker_kind,
        workspace_default_tracker_kind=workspace_default_kind,
        include_fsm=payload.include_fsm,
        repo_full_name=repo_row.full_name,
    )

    if not bundle.files:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "empty_bundle",
                "message": (
                    f"Preset(s) {cleaned!r} + selected options resolved to "
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
        f"**Presets**: {', '.join('`' + p + '`' for p in cleaned)}\n"
        f"{tracker_line}\n"
        f"**Knowledge**: {', '.join('`' + s + '`' for s in bundle.knowledge_slugs) or '_none_'}\n\n"
        "Merge once. Ship's first scheduled lanes will start running "
        "against this repo as soon as GitHub picks the new workflows up."
    )
    try:
        result = await commit_bundle_pr(
            repo_row,
            install_row,
            files=bundle.files,
            title=f"Ship: wizard seed ({', '.join(cleaned)})",
            branch_label=f"wizard-{'-'.join(cleaned)}",
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
