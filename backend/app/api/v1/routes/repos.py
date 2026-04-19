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
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.gateway.code_host import RepoRef, RepoSummary
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost


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
            updated.append(ext_id)

    for ext_id, row in existing_by_ext.items():
        if ext_id in desired_ids:
            continue
        await session.delete(row)
        removed.append(ext_id)

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
