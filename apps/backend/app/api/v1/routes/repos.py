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
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
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
from backend.app.integrations.gateway.code_host import RepoSummary
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.services.seed_bundle import BUNDLE_VERSION as _BUNDLE_VERSION


router = APIRouter(
    prefix="/workspaces/{workspace_id}/repos",
    tags=["repos"],
)

logger = logging.getLogger(__name__)


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
    # Multi-workspace per install (migration 0076): a repo activated
    # in a sibling workspace under the same org. ``None`` = free to
    # activate here; a string = workspace slug that already owns it
    # (operator sees "already in <ws>" + the picker disables the row
    # so two workspaces don't fight for one PR cache stream).
    claimed_by_workspace_slug: str | None = None


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
    installed_bundle_version: str | None = None
    current_bundle_version: str = _BUNDLE_VERSION
    # Persisted deploy-planner preference (wizard sets it, deploy modal
    # prefills + edits it). ``NULL`` → backend default at deploy time.
    deploy_planner_provider: str | None = None
    deploy_planner_model: str | None = None


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
            "Catalog preset id attached to the activated repo(s). "
            "Ship knows only one preset (``\"default\"``); any non-null "
            "string passed here normalises to it. ``None`` keeps any "
            "existing preset on the WorkspaceRepo row."
        ),
    )


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
        # Sibling-attached install (migration 0076 — multi-workspace per
        # install): the workspace activated repos under ANOTHER
        # workspace's install via the headless attach path, so no
        # install row is owned by this workspace. Resolve it through the
        # workspace's activated repos' installation_id FK. Additive —
        # only runs when the direct lookup found nothing, so normal
        # single-workspace installs are unaffected.
        sibling_stmt = (
            select(GitHubInstallation)
            .join(
                WorkspaceRepo,
                WorkspaceRepo.installation_id == GitHubInstallation.id,
            )
            .where(WorkspaceRepo.workspace_id == workspace_id)
            .where(GitHubInstallation.suspended_at.is_(None))
        )
        install = (await session.execute(sibling_stmt)).scalars().first()
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
    summary: RepoSummary,
    *,
    activated_ids: set[int],
    claimed_elsewhere: dict[int, str] | None = None,
) -> AvailableRepoOut:
    claimed_by = (claimed_elsewhere or {}).get(summary.external_id)
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
        claimed_by_workspace_slug=claimed_by,
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
        deploy_planner_provider=row.deploy_planner_provider,
        deploy_planner_model=row.deploy_planner_model,
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

    # Cross-workspace claim map (migration 0076 — multi-workspace per
    # install). A repo activated in a sibling workspace under the same
    # GitHub App install gets surfaced as ``claimed_by_workspace_slug``
    # so the picker can disable it and the operator doesn't accidentally
    # double-wire the same PR stream into two workspaces. Filtered by
    # the current install's GitHub ``installation_id`` so only true
    # sibling claims surface — not unrelated installs.
    from backend.app.db.models.tenancy import Workspace as _Workspace
    claim_stmt = (
        select(WorkspaceRepo.external_id, _Workspace.slug)
        .join(
            GitHubInstallation,
            WorkspaceRepo.installation_id == GitHubInstallation.id,
        )
        .join(_Workspace, WorkspaceRepo.workspace_id == _Workspace.id)
        .where(
            GitHubInstallation.installation_id == install.installation_id,
            WorkspaceRepo.workspace_id != workspace_id,
            WorkspaceRepo.provider == "github",
        )
    )
    claimed_elsewhere: dict[int, str] = {
        ext_id: slug
        for ext_id, slug in (await session.execute(claim_stmt)).all()
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

    return [
        _summary_to_out(
            s, activated_ids=activated_ids, claimed_elsewhere=claimed_elsewhere
        )
        for s in summaries
    ]


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

    # Preset collapsed to ``"default"`` (the only value Ship knows
    # about today). ``None`` passes through unchanged so a payload
    # that omits the field doesn't accidentally lock a preset on the
    # WorkspaceRepo row.
    preset = "default" if payload.preset is not None else None

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

    # Cross-workspace claim guard (migration 0076). Reject activations
    # for repos already activated in a sibling workspace under the
    # same install — the picker disables them client-side but we
    # also gate server-side so a stale picker payload or a direct
    # API call can't double-wire the same PR stream.
    if desired_ids:
        from backend.app.db.models.tenancy import Workspace as _Workspace
        claim_rows = (
            await session.execute(
                select(WorkspaceRepo.external_id, _Workspace.slug)
                .join(
                    GitHubInstallation,
                    WorkspaceRepo.installation_id == GitHubInstallation.id,
                )
                .join(_Workspace, WorkspaceRepo.workspace_id == _Workspace.id)
                .where(
                    GitHubInstallation.installation_id == install.installation_id,
                    WorkspaceRepo.workspace_id != workspace_id,
                    WorkspaceRepo.provider == "github",
                    WorkspaceRepo.external_id.in_(desired_ids),
                )
            )
        ).all()
        if claim_rows:
            collisions = {
                ext_id: slug for ext_id, slug in claim_rows
            }
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "repo_claimed_elsewhere",
                    "message": (
                        "Some repos are already activated in a sibling "
                        "workspace under the same GitHub App install. "
                        "Deactivate them there first, or pick different "
                        "repos."
                    ),
                    "collisions": [
                        {"external_id": ext_id, "workspace_slug": slug}
                        for ext_id, slug in sorted(collisions.items())
                    ],
                },
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

    # Activation now only records repository membership. The old flow
    # also materialised default Pipeline rows and mirrored committed
    # `.ship/knowledge/*.md` files into `knowledge_buckets`; both are
    # intentionally owned by the unified wizard seed + post-merge
    # bootstrap process now.

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
                "preset": preset,
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



# ---------------------------------------------------------------------------
# Per-repo preset picker (B9)
# ---------------------------------------------------------------------------


class RepoPresetPatchIn(BaseModel):
    """Payload for ``PATCH /v1/workspaces/{ws}/repos/{id}``.

    Only the ``preset`` field is mutable today; future fields (e.g.
    ``default_branch`` or per-repo config) can land here without a new
    endpoint. ``reshape`` is retained for legacy clients but ignored
    because repository activation no longer materializes Pipeline rows.
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
    """Mutate the preset bound to ``repo`` without touching runtime rows.

    Admin-only. Ship knows only one preset (``"default"``); any
    non-null string normalises to it before persistence. ``None``
    clears the binding and falls back to the canonical default
    shape on future seeds.
    """
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

    # ``"default"`` is the only preset Ship recognises today; ``None``
    # keeps its semantic of "clear the binding".
    new_preset = "default" if payload.preset is not None else None

    old_preset = repo_row.preset
    repo_row.preset = new_preset

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
                "reshape_applied": 0,
            },
        )
    )
    await session.flush()
    return _row_to_out(repo_row)


# ---------------------------------------------------------------------------
# SDLC bootstrap readiness (BS1)
# ---------------------------------------------------------------------------


class CapabilityStatusOut(BaseModel):
    """One blueprint capability and whether the repo already has it."""

    capability: str
    required: bool
    satisfied: bool
    matched_by: str | None = None


class SecretStatusOut(BaseModel):
    """One expected secret and whether it's present on the repo."""

    name: str
    required: bool
    present: bool


class SdlcReadinessOut(BaseModel):
    """Bootstrap readiness of a repo against its project-type blueprint.

    ``has_blueprint=False`` with a ``detail`` covers the degraded cases
    (intel not harvested yet, or no blueprint for the classified type —
    e.g. backend / library / unknown). The console card renders
    ``detail`` instead of the capability table in that case.
    """

    repo_id: uuid.UUID
    intel_id: uuid.UUID | None
    project_type: str | None
    has_blueprint: bool
    ready: bool
    detail: str | None = None
    delivery: str | None = None
    environments: list[str] = []
    capabilities: list[CapabilityStatusOut] = []
    gaps: list[str] = []
    secrets: list[SecretStatusOut] = []
    missing_required_secrets: list[str] = []
    external_checklist: list[str] = []


@router.get("/{repo_id}/sdlc-readiness", response_model=SdlcReadinessOut)
async def read_sdlc_readiness(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SdlcReadinessOut:
    """Assess a repo's SDLC bootstrap readiness against its blueprint.

    Picks the blueprint by the repo's classified ``project_type`` (from
    the latest ``repo_intel`` harvest), then checks which required
    capabilities are present (file/dep probes against the live tree),
    which expected secrets are configured, and what the operator still
    has to do by hand. Read-only; safe to poll from the console card.
    """
    from backend.app.services.sdlc_readiness import build_readiness

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

    result = await build_readiness(
        session=session, repo=repo_row, settings=settings
    )
    rep = result.report
    return SdlcReadinessOut(
        repo_id=result.repo_id,
        intel_id=result.intel_id,
        project_type=result.project_type,
        has_blueprint=result.has_blueprint,
        ready=bool(rep.ready) if rep else False,
        detail=result.detail,
        delivery=rep.delivery if rep else None,
        environments=list(rep.environments) if rep else [],
        capabilities=[
            CapabilityStatusOut(
                capability=c.capability,
                required=c.required,
                satisfied=c.satisfied,
                matched_by=c.matched_by,
            )
            for c in (rep.capabilities if rep else ())
        ],
        gaps=list(rep.gaps) if rep else [],
        secrets=[
            SecretStatusOut(name=s.name, required=s.required, present=s.present)
            for s in (rep.secrets if rep else ())
        ],
        missing_required_secrets=(
            list(rep.missing_required_secrets) if rep else []
        ),
        external_checklist=list(rep.external_checklist) if rep else [],
    )


# ---------------------------------------------------------------------------
# SDLC bootstrap plan generation (BS3)
# ---------------------------------------------------------------------------


class BootstrapTicketOut(BaseModel):
    capability: str
    display_id: str
    url: str


class BootstrapPlanOut(BaseModel):
    """Result of generating a bootstrap epic for a repo."""

    repo_id: uuid.UUID
    project_url: str
    project_native_id: str
    tickets: list[BootstrapTicketOut]


@router.post("/{repo_id}/bootstrap/generate-plan", response_model=BootstrapPlanOut)
async def generate_bootstrap_plan_route(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> BootstrapPlanOut:
    """Generate the bootstrap epic: a tracker project + one infra ticket
    per readiness gap, bound to this repo so its tickets dispatch here.

    Admin-only. 409s when the repo is already ready, has no blueprint, or
    the tree can't be assessed — there's nothing to bootstrap. The infra
    tickets flow through planning → DevOps (BS0.1) to scaffold the setup.
    """
    from backend.app.services.bootstrap_plan import generate_bootstrap_plan
    from backend.app.services.sdlc_readiness import build_readiness
    from backend.app.services.tracker_resolver import resolve_for_workspace

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

    result = await build_readiness(
        session=session, repo=repo_row, settings=settings
    )
    if not result.has_blueprint or result.report is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                result.detail
                or "No bootstrap blueprint for this repo's project type."
            ),
        )
    if result.report.ready:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Repo is already SDLC-ready — nothing to bootstrap.",
        )
    if not result.report.gaps:
        # ready=False only because of missing required secrets — there's
        # nothing for the DevOps agent to scaffold; the operator adds the
        # secrets by hand (see the readiness external checklist).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No capability gaps to bootstrap — only required secrets "
                "are missing. Add them to the repo by hand "
                "(Settings → Secrets); see the readiness checklist."
            ),
        )

    resolved = await resolve_for_workspace(
        session=session, settings=settings, workspace_id=workspace_id
    )
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "No tracker (Linear) is connected for this workspace, so "
                "Ship can't create the bootstrap epic."
            ),
        )

    plan = await generate_bootstrap_plan(
        session=session,
        tracker=resolved.gateway,
        repo=repo_row,
        report=result.report,
        actor_user_id=auth.user.id,
    )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.bootstrap.generate_plan",
            target_kind="workspace_repo",
            target_id=str(repo_id),
            payload={
                "full_name": repo_row.full_name,
                "project_native_id": plan.project_native_id,
                "ticket_count": len(plan.tickets),
                "gaps": [t.capability for t in plan.tickets],
            },
        )
    )
    await session.flush()
    return BootstrapPlanOut(
        repo_id=repo_row.id,
        project_url=plan.project_url,
        project_native_id=plan.project_native_id,
        tickets=[
            BootstrapTicketOut(
                capability=t.capability,
                display_id=t.display_id,
                url=t.url,
            )
            for t in plan.tickets
        ],
    )


# ---------------------------------------------------------------------------
# Wizard v2 unified seed PR (iter 5)
#
# Single-shot replacement for ``install_bundle`` + the retired knowledge seed:
# one PR carrying the preset workflows, ``.ship/config.yml``, bootstrap
# workflow, and the tracker FSM doc. Also mints the long-
# lived ``SHIP_RUN_TOKEN`` Actions secret *before* opening the PR so
# the lanes the PR installs can authenticate the moment the merge
# fires their first schedule tick. Plaintext never touches the DB —
# see ``services.repo_tokens`` for the hash-only persistence story.
# ---------------------------------------------------------------------------


class WizardSeedIn(BaseModel):
    """Body for ``POST /workspaces/{ws}/repos/{repo_id}/wizard_seed``."""

    # ELS-178 (W2) — `presets` + `knowledge_slugs` retired 2026-05-19.
    # Wizard always seeds DEFAULT_BUNDLE; knowledge surface moved to
    # workspace-level buckets. BFF no longer sends either.

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
    """Response shape for ``POST .../wizard_seed``.

    ELS-178 (W2) — `codeowners`, `intel`, `synthetic_lanes_created`,
    `presets`, `knowledge_slugs` retired 2026-05-19. The wizard route
    stopped populating them long ago (always None / 0 / []); the
    accompanying type classes (`WizardSeedCodeownersSummary`,
    `WizardSeedIntelHandle`) + the `wizard_seed_routing` service +
    the synthetic-lane sync flavor went with them.
    """

    pr_url: str
    pr_number: int
    branch: str
    files: list[str]
    tracker_kind: str | None = None
    run_token_prefix: str | None = None
    run_token_rotated: bool = False
    # ELS-182 (W6) — true once the seed PR has been merged (either via
    # the App's installation token in the activation modal or by the
    # operator on github.com). Populated by /wizard_seed/latest from
    # a sibling ``pr_merge.tracker_done`` audit row matched on
    # pr_number — the live endpoint always returns False because the
    # PR is fresh.
    merged: bool = False


class WizardSeedActivateIn(BaseModel):
    """Body for ``POST .../wizard_seed/activate``.

    The pre-Wave-8c "open seed PR → done step" flow asked the operator
    to merge the PR themselves on github.com. PO-friendly Wave-8c FE
    pops a modal right after the PR opens with a "Turn Ship on now"
    CTA — accepting it calls this endpoint, which merges the PR via
    the App's installation token.
    """

    pr_number: int = Field(ge=1)


class WizardSeedActivateOut(BaseModel):
    """Response shape for ``POST .../wizard_seed/activate``."""

    merged: bool
    pr_number: int
    sha: str | None = None
    message: str | None = None



class RoutineRunClaimIn(BaseModel):
    event: str = Field(pattern="^(schedule|manual|pull_request|push)$")
    routine_id: str = Field(min_length=1, max_length=128)
    window_key: str = Field(min_length=1, max_length=255)
    scheduled_for: datetime
    window_start: datetime
    window_end: datetime
    github: dict[str, Any] = Field(default_factory=dict)


class RoutineRunClaimOut(BaseModel):
    status: str
    routine_id: str
    window_key: str


class PipelinePickIn(BaseModel):
    event: str = Field(default="schedule", pattern="^(schedule|manual)$")
    github: dict[str, Any] = Field(default_factory=dict)


class PipelinePickCandidateOut(BaseModel):
    specialist: str
    downstream_index: int
    last_picked_at: str


class PipelinePickOut(BaseModel):
    action: str = Field(pattern="^(pipeline_pick|noop)$")
    specialist: str | None = None
    reason: str | None = None
    candidates: list[PipelinePickCandidateOut] = Field(default_factory=list)


# Downstream-first canonical order. The pick rotates through these
# across ticks (oldest last_picked first); within ties, downstream
# wins so WIP drains before new intake.
_PIPELINE_SPECIALIST_ORDER: tuple[str, ...] = (
    "reviewer",
    "qa-automation",
    "qa-engineer",
    "developer",
    "qa-architect",
    "tech-architect",
    "ba",
    "intake",
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
        merge_pull_request,
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

    # ── Idempotency gate ─────────────────────────────────────────
    # Wizard was opening a fresh PR on every operator click even when
    # the bundle was already up to date — 11 sequential
    # ``ship/bundle-wizard-default-<ts>`` PRs piled up on askslayer/
    # visitor-web in two weeks, all with identical content. Check
    # ``installed_bundle_version`` and, if the most recent seed audit
    # for this repo records the **current** ``BUNDLE_VERSION``,
    # short-circuit with the existing PR URL (if it's still open) or
    # a 200 "no-op" payload so the FE can show "already up to date"
    # instead of opening another draft.
    #
    # Bumping ``BUNDLE_VERSION`` server-side (e.g. 0.35 → 0.36 in this
    # commit) is what *forces* a fresh seed PR — the gate compares
    # against the canonical constant, not against the last audit's
    # version, so a server-side bundle fix doesn't get masked by a
    # stale recorded version.
    if (
        repo_row.installed_bundle_version is not None
        and repo_row.installed_bundle_version == _BUNDLE_VERSION
    ):
        last_seed = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.workspace_id == workspace_id,
                    AuditLog.action == "repo.wizard_seed",
                    AuditLog.target_id == str(repo_row.id),
                )
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
        ).scalars().first()
        if last_seed is not None:
            # ELS-181 (W5/B3) — even on idempotent short-circuit, repair
            # SHIP_WORKSPACE_ID. The secret was added in commit 2e488df;
            # repos seeded before that bump (and still on _BUNDLE_VERSION)
            # would otherwise never get it pushed because we skip the
            # full setup path. put_repo_secret is idempotent at GH side.
            try:
                from backend.app.integrations.github.actions_secrets import (
                    put_repo_secret,
                )

                install_row_short = (
                    await session.execute(
                        select(GitHubInstallation).where(
                            GitHubInstallation.id == repo_row.installation_id
                        )
                    )
                ).scalars().first()
                if install_row_short is not None:
                    await put_repo_secret(
                        repo_row,
                        install_row_short,
                        name="SHIP_WORKSPACE_ID",
                        plaintext=str(workspace_id),
                        settings=settings,
                        client=None,
                    )
            except Exception:  # noqa: BLE001 — best-effort repair
                logger.warning(
                    "wizard_seed short-circuit: SHIP_WORKSPACE_ID re-push "
                    "skipped for repo=%s — operator may need to wizard-reseed",
                    repo_row.full_name,
                )

            seed_pr_url = (last_seed.payload or {}).get("pr_url")
            seed_pr_number = (last_seed.payload or {}).get("pr_number")
            try:
                seed_pr_number_int = int(seed_pr_number) if seed_pr_number else 0
            except (TypeError, ValueError):
                seed_pr_number_int = 0
            seed_branch = (last_seed.payload or {}).get("branch") or ""
            return WizardSeedOut(
                pr_url=str(seed_pr_url or ""),
                pr_number=seed_pr_number_int,
                branch=str(seed_branch),
                files=[],
                tracker_kind=(last_seed.payload or {}).get("tracker_kind"),
                run_token_prefix=repo_row.run_token_prefix,
                run_token_rotated=False,
            )

    # ── Workspace defaults gate ──────────────────────────────────
    # Three workspace-level invariants must hold before any repo can
    # be seeded. Each missing piece flips a stable error code so the
    # FE can deeplink straight at the right step instead of bouncing
    # through a generic "seed failed" banner.
    #
    # 1. ``Workspace.default_agent_profile`` — non-NULL. The /process
    #    editor and shipctl agent dispatch both need it to pick which
    #    coding agent gets invoked per state. Seeding without it
    #    produces a config the runtime can't resolve.
    # 2. Workspace-level tracker — at least one ``Integration`` row
    #    of kind linear/github/jira at workspace scope. Linear/Notion
    #    additionally need ``secret_ciphertext`` (OAuth token);
    #    GitHub rides on the App installation token (already enforced
    #    by the github_app_missing check above) and needs no secret.
    # 3. Orchestrator — only ``github`` is supported today; covered
    #    automatically by the github_app_missing check.
    from backend.app.db.models.tenancy import (  # local import, no circular
        Integration,
        Workspace,
    )

    workspace_row = await session.get(Workspace, workspace_id)
    if workspace_row is None or not workspace_row.default_agent_profile:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "workspace_default_agent_required",
                "message": (
                    "Pick a workspace-level default_agent_profile before "
                    "seeding. PATCH /v1/workspaces/{ws} with one of: auto / "
                    "main / cheaper / cursor_agent / codex_cli / "
                    "ship_cloud_agent / local_cli."
                ),
            },
        )

    has_workspace_tracker = (
        await session.execute(
            select(Integration.id)
            .where(
                Integration.workspace_id == workspace_id,
                Integration.repo_id.is_(None),
                Integration.kind.in_(("linear", "github", "jira")),
                # GitHub trackers ride on the App installation token,
                # so no ``secret_ciphertext`` is fine for kind=github
                # (the github_app_missing check above already enforces
                # a real install). Linear / Jira need a token present.
                or_(
                    Integration.kind == "github",
                    Integration.secret_ciphertext.is_not(None),
                ),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if has_workspace_tracker is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "workspace_tracker_required",
                "message": (
                    "Connect a workspace-level tracker before seeding. "
                    "Linear/Notion: POST "
                    "/v1/integrations/{kind}/install/start; Jira: PUT "
                    "/v1/integrations/jira; GitHub Issues: pick "
                    "kind=github at the workspace tracker step."
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
        # Real reason has been swallowed by HTTPException all summer.
        # Operators (and Sentry, when wired) need the traceback
        # surfaced loud the moment the secret-push step fails —
        # repo permission drift / App-scope drift / libsodium boot
        # issues all land here and the 502 body alone is useless.
        logger.exception(
            "wizard_seed: ship_ci_secret_push_failed for repo=%s install=%s",
            repo_row.full_name,
            install_row.installation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "ship_ci_secret_push_failed",
                "message": (
                    "Couldn't push Ship CI secrets (SHIP_API_BASE / "
                    "SHIP_API_TOKEN) to GitHub Actions. The seed PR was not opened."
                ),
                "error_class": exc.__class__.__name__,
                "error": str(exc)[:512],
            },
        ) from exc

    # ── Compose the file bundle (pure) ────────────────────────────
    # Always DEFAULT_BUNDLE post-P5-06 — see WizardSeedIn.presets
    # deprecation. The bundle / knowledge counts still flow through
    # the audit log so we can tell apart "old tiny seed" from
    # "P5-06 full seed" on a wizard-replay.
    # Phase 2.5 — agent rule files baked into the seed PR. Map the
    # workspace's ``default_agent_profile`` (cursor_agent / codex_cli /
    # ship_cloud_agent / …) to the rule-file slug list the seed
    # bundle understands. ``auto`` / ``main`` / ``cheaper`` don't pin
    # a specific runtime, so they fall back to the generic
    # ``claude-md`` rule that Claude Code + most CLAUDE.md-aware
    # tools recognise.
    _seed_agents_for_profile = {
        "cursor_agent": ("cursor",),
        "ship_cloud_agent": ("cursor-cloud",),
        "codex_cli": ("codex",),
        "local_cli": ("claude-md",),
        "auto": ("claude-md",),
        "main": ("claude-md",),
        "cheaper": ("claude-md",),
    }
    seed_agents = _seed_agents_for_profile.get(
        workspace_row.default_agent_profile or "", ("claude-md",)
    )

    bundle = compose_seed_files(
        bundle=DEFAULT_BUNDLE,
        knowledge_slugs=[],
        tracker_kind=tracker_kind,
        workspace_default_tracker_kind=workspace_default_kind,
        include_fsm=payload.include_fsm,
        repo_intel_placeholder=False,
        repo_full_name=repo_row.full_name,
        agents=seed_agents,
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
        "**Knowledge**: indexed server-side after merge — Ship's webhook "
        "consumes the new `.ship/config.yml` and updates the workspace "
        "knowledge index out-of-band.\n\n"
        "Merge once. The `.github/workflows/ship-agent-run.yml` workflow "
        "handles every Ship agent dispatch — Linear state changes trigger "
        "it via Ship's webhook, no cron required."
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
        logger.exception(
            "wizard_seed: commit_bundle_pr failed for repo=%s install=%s status=%s body=%s",
            repo_row.full_name,
            install_row.installation_id,
            exc.status_code,
            exc.message[:512],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "wizard_seed_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
            },
        ) from exc

    # Auto-merge the seed PR so the operator never has to touch GitHub —
    # they just see "Ship updated". The version stamp is the load-bearing
    # signal both the dashboard ("up to date" vs "update available") and
    # the dispatcher (which workflow_dispatch inputs are safe to send)
    # read, so we ONLY stamp AFTER the merge actually lands.
    #
    # Stamping on PR *creation* (the old behaviour) was a latent bug: an
    # unmerged seed PR made the repo look "up to date", which (a) tripped
    # the idempotency gate above so the operator could never re-seed, and
    # (b) made the dispatcher send version-gated inputs (`ship_run_id`,
    # `file_overlap_warnings`) the still-old workflow file didn't declare
    # → GitHub 422 on every dispatch → self-heal re-fire storm.
    seed_merged = False
    try:
        await merge_pull_request(
            repo_row,
            install_row,
            pr_number=result.pr_number,
            settings=settings,
            commit_title=f"Ship: wizard seed (#{result.pr_number})",
            merge_method="squash",
        )
        seed_merged = True
        repo_row.installed_bundle_version = _BUNDLE_VERSION
    except WorkflowDispatchError as exc:
        # Branch protection or a required check still pending. Leave the PR
        # open and the version UNstamped (truthful "update available").
        # The seed_auto_merge reconciler retries it on the next tick, and
        # a later wizard click returns this same open PR and re-attempts
        # the merge — either way the stamp only lands once the merge does.
        logger.warning(
            "wizard seed PR #%s opened but auto-merge deferred (%s): %s",
            result.pr_number,
            exc.status_code,
            exc.message[:200],
        )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.wizard_seed",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload={
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
                "seed_merged": seed_merged,
            },
        )
    )
    await session.flush()

    return WizardSeedOut(
        pr_url=result.pr_url,
        pr_number=result.pr_number,
        branch=result.branch,
        files=[p for p, _ in bundle.files],
        tracker_kind=tracker_kind,
        run_token_prefix=repo_row.run_token_prefix,
        run_token_rotated=rotated,
        merged=seed_merged,
    )



@router.post(
    "/{repo_id}/routine-runs/claim",
    response_model=RoutineRunClaimOut,
)
async def claim_routine_run(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: RoutineRunClaimIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> RoutineRunClaimOut:
    """Claim a routine schedule window computed locally by ``shipctl``.

    The backend no longer parses ``.ship/config.yml`` to decide what is due.
    It only validates workspace/repo membership and records the window claim so
    repeated GitHub schedule ticks don't run the same routine window twice.
    """

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

    if await _routine_window_seen(
        session,
        workspace_id=workspace_id,
        repo_id=repo_row.id,
        routine_id=payload.routine_id,
        window_key=payload.window_key,
    ):
        return RoutineRunClaimOut(
            status="already_claimed",
            routine_id=payload.routine_id,
            window_key=payload.window_key,
        )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.routine_run_claim",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload={
                "event": payload.event,
                "routine_id": payload.routine_id,
                "window_key": payload.window_key,
                "scheduled_for": payload.scheduled_for.isoformat(),
                "window_start": payload.window_start.isoformat(),
                "window_end": payload.window_end.isoformat(),
                "github": payload.github,
                "status": "claimed",
            },
        )
    )
    await session.flush()
    return RoutineRunClaimOut(
        status="claimed",
        routine_id=payload.routine_id,
        window_key=payload.window_key,
    )


@router.post("/{repo_id}/pipeline-pick", response_model=PipelinePickOut)
async def pipeline_pick(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: PipelinePickIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PipelinePickOut:
    """Pick the next pipeline specialist to run.

    The trigger workflow calls this *after* the routine sweep when no
    cron routine is due — the tick spends its budget on a pipeline
    specialist instead. One pick per tick (workflow concurrency at
    GitHub level serialises ticks, so the picked specialist can't
    collide with itself).

    Rotation: the specialist with the oldest ``last_picked_at`` from
    the audit log goes first; ties break downstream-first via
    :data:`_PIPELINE_SPECIALIST_ORDER` so WIP drains before refilling
    intake. Specialists never picked yet score as epoch and so come up
    in the first sweep before any rotation kicks in.
    """

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

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    rows = (
        await session.execute(
            select(AuditLog.payload, AuditLog.created_at).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "repo.pipeline_pick_dispatched",
                AuditLog.target_kind == "workspace_repo",
                AuditLog.target_id == str(repo_row.id),
                AuditLog.created_at >= cutoff,
            )
        )
    ).all()

    last_picked: dict[str, datetime] = {}
    for log_payload, created_at in rows:
        if not isinstance(log_payload, dict):
            continue
        slug = log_payload.get("specialist")
        if not isinstance(slug, str):
            continue
        prev = last_picked.get(slug)
        if prev is None or created_at > prev:
            last_picked[slug] = created_at

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    candidates = [
        PipelinePickCandidateOut(
            specialist=slug,
            downstream_index=idx,
            last_picked_at=last_picked.get(slug, epoch).isoformat(),
        )
        for idx, slug in enumerate(_PIPELINE_SPECIALIST_ORDER)
    ]
    candidates.sort(key=lambda c: (c.last_picked_at, c.downstream_index))
    chosen = candidates[0].specialist

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.pipeline_pick_dispatched",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload={
                "event": payload.event,
                "specialist": chosen,
                "github": payload.github,
            },
        )
    )
    await session.flush()

    return PipelinePickOut(
        action="pipeline_pick",
        specialist=chosen,
        candidates=candidates,
    )


async def _routine_window_seen(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    routine_id: str,
    window_key: str,
) -> bool:
    rows = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "repo.routine_run_claim",
                AuditLog.target_kind == "workspace_repo",
                AuditLog.target_id == str(repo_id),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(500)
        )
    ).scalars().all()
    return any(
        (row.payload or {}).get("routine_id") == routine_id
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

    # ELS-182 (W6) — detect whether the seed PR has merged so the Done
    # page badge flips without sessionStorage's help. We look for a
    # sibling ``pr_merge.tracker_done`` audit row whose pr_number
    # matches. Cheap: one indexed query on (workspace_id, action,
    # created_at).
    seed_pr_number = int(payload.get("pr_number") or 0)
    merged = False
    if seed_pr_number:
        from sqlalchemy import cast as _sa_cast, Integer as _SaInteger

        merged_row = (
            await session.execute(
                select(AuditLog.id)
                .where(
                    AuditLog.workspace_id == workspace_id,
                    AuditLog.action == "pr_merge.tracker_done",
                    _sa_cast(
                        AuditLog.payload["pr_number"].astext, _SaInteger
                    )
                    == seed_pr_number,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        merged = merged_row is not None

    return WizardSeedOut(
        pr_url=payload.get("pr_url") or "",
        pr_number=seed_pr_number,
        branch=payload.get("branch") or "",
        files=list(payload.get("files") or []),
        tracker_kind=payload.get("tracker_kind"),
        run_token_prefix=payload.get("run_token_prefix"),
        run_token_rotated=bool(payload.get("run_token_rotated") or False),
        merged=merged,
    )


# ---------------------------------------------------------------------------
# Wizard seed activation (Wave-8c "Activate Ship now" modal)
# ---------------------------------------------------------------------------
#
# Merge the just-opened wizard seed PR on the operator's behalf. Admin-
# only; uses the App's installation token (same one that opened the PR
# so we have ``contents:write``). Branch protection / required checks
# bubble up as a 409 so the FE can fall back to the "I'll merge it
# myself" path without pretending it worked.


@router.post(
    "/{repo_id}/wizard_seed/activate",
    response_model=WizardSeedActivateOut,
)
async def activate_wizard_seed(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: WizardSeedActivateIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> WizardSeedActivateOut:
    """Merge the seed PR opened by ``POST .../wizard_seed`` for ``repo_id``.

    Caller passes ``pr_number`` (returned by the seed call). We resolve
    the App installation for the repo and call GitHub's merge endpoint
    with that token. The operator never has to leave the wizard.

    Failure modes the FE has to handle:

    - ``404`` — repo not in this workspace.
    - ``412 github_app_missing`` — the App was uninstalled between
      seed and activate (rare but possible).
    - ``409 merge_blocked`` — branch protection / required status
      checks aren't satisfied yet. The PR stays open for manual review.
    - ``502 github_upstream_error`` — anything else GitHub says no to;
      the upstream message is included so ops can triage.
    """
    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        merge_pull_request,
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
                    "Reinstall it and try again, or merge the PR yourself."
                ),
            },
        )

    try:
        result = await merge_pull_request(
            repo_row,
            install_row,
            pr_number=payload.pr_number,
            settings=settings,
            commit_title=f"ship: activate Ship in {repo_row.full_name}",
        )
    except WorkflowDispatchError as exc:
        # Branch-protection / required-status failures come back as 405
        # ("Method Not Allowed" — "Pull Request is not mergeable") or
        # 409 ("Head branch was modified"). Either way the operator
        # can still merge in the GitHub UI; we surface a stable code
        # so the FE can render the "merge it yourself" fallback.
        if exc.status_code in (405, 409):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "merge_blocked",
                    "message": (
                        "GitHub wouldn't merge the PR — branch protection or "
                        "required status checks aren't satisfied yet. Open "
                        "the PR on GitHub to finish merging."
                    ),
                    "github_message": exc.message[:200],
                },
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "github_upstream_error",
                "message": "GitHub rejected the merge call.",
                "github_message": exc.message[:200],
            },
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="repo.wizard_seed_activate",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload={
                "pr_number": payload.pr_number,
                "merged": result.merged,
                "sha": result.sha,
            },
        )
    )
    await session.commit()

    return WizardSeedActivateOut(
        merged=result.merged,
        pr_number=payload.pr_number,
        sha=result.sha,
        message=result.message,
    )


# ---------------------------------------------------------------------------
# Disconnect (B6)
# ---------------------------------------------------------------------------


class DisconnectRepoOut(BaseModel):
    """Summary of what the disconnect call wiped, for the UI toast."""

    repo_id: uuid.UUID
    full_name: str
    deleted_routines: int
    deleted_runs: int


@router.delete("/{repo_id}", response_model=DisconnectRepoOut)
async def disconnect_repo(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> DisconnectRepoOut:
    """Unwire Ship from ``repo`` — deletes the row and every routine bound to it.

    Admin-only. When the operator explicitly disconnects a repo they
    want all Ship state gone, so we delete the ``WorkspaceRepo`` row
    and let CASCADE drop every :class:`Routine` bound to it, which in
    turn cascades to :class:`RoutineRun` history.

    We deliberately do **not** touch github.com:

    - Removing the repo from the App's ``selected_repositories`` list
      requires a user-initiated flow in GitHub's UI.
    - The workflow YAMLs our install PR added live under version
      control in the customer repo — the customer owns them now.
    """
    from backend.app.db.models.lanes import Routine, RoutineRun

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
    routine_ids = (
        await session.execute(
            select(Routine.id).where(Routine.repo_id == repo_row.id)
        )
    ).scalars().all()

    run_count = 0
    if routine_ids:
        run_count = len(
            (
                await session.execute(
                    select(RoutineRun.id).where(
                        RoutineRun.routine_id.in_(routine_ids)
                    )
                )
            ).scalars().all()
        )

    # Orphan-billing guard: tear down live cloud deployments BEFORE the cascade
    # drops deployment rows. This is billing-critical, so disconnect is blocked
    # if provider teardown cannot be confirmed.
    try:
        from backend.app.services.deploy.teardown import teardown_repo_app

        res = await teardown_repo_app(
            session, workspace_id, repo_id, delete_rows=False
        )
        if res.failed_app_ids:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "Could not delete this repo's cloud app. "
                    "Repo disconnect was stopped so billing handles are not lost."
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "repo %s disconnect: deploy teardown raised; blocking disconnect",
            repo_id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Could not confirm cloud teardown for this repo. "
                "Repo disconnect was stopped so billing handles are not lost."
            ),
        ) from exc

    # Routine.repo_id is ON DELETE CASCADE, so deleting the WorkspaceRepo
    # row also drops every Routine bound to it, which cascades to
    # RoutineRun. No explicit per-routine delete loop needed.
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
                "deleted_routines": len(routine_ids),
                "deleted_runs": run_count,
            },
        )
    )
    await session.flush()

    return DisconnectRepoOut(
        repo_id=repo_id,
        full_name=full_name,
        deleted_routines=len(routine_ids),
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
        _validate_ticket_contract(
            state_obj.get("ticket_contract"),
            f"process.states[{index}].ticket_contract",
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
    seen_transition_pairs: set[tuple[str, str]] = set()
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
        pair = (from_state, to_state)
        if pair in seen_transition_pairs:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": (
                        "process.transitions must not contain duplicate "
                        "(from, to) pairs; split different outcomes into different "
                        "target states"
                    ),
                },
            )
        seen_transition_pairs.add(pair)
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
        if transition.get("requires_human") is not None and not isinstance(
            transition.get("requires_human"), bool
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": (
                        f"process.transitions[{index}].requires_human must be "
                        "a boolean when provided"
                    ),
                },
            )

    _validate_process_schedule(process.get("schedule"))
    _validate_process_routines(process.get("routines"))
    _validate_process_gates(process.get("gates"))


# Where the operator wants to interject. ``after_pr`` (default) is
# fully autonomous through the agent reviewer — human only approves +
# merges. ``after_arch`` pauses for human approval after tech/qa
# architects (catches scope/architecture mistakes early).
# ``after_ba`` pauses earlier, after BA writes the spec — useful when
# requirements drift is the main risk. Phase 3 lands the schema +
# default; the FSM finish-endpoint enforcement that actually moves
# tickets to a "needs review" state instead of straight to dev is
# Phase 3.5.
_PROCESS_GATES: Final[tuple[str, ...]] = ("after_ba", "after_arch", "after_pr")


def _validate_process_gates(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, str) or value not in _PROCESS_GATES:
        allowed = "|".join(_PROCESS_GATES)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": f"process.gates must be one of {allowed}",
            },
        )


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


def _validate_ticket_contract(value: Any, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": f"{field} must be an object when provided",
            },
        )
    for key in ("input_state", "claim_state", "success_state"):
        _require_optional_string(value.get(key), f"{field}.{key}", required=True)
    for key in ("blocked_state", "needs_info_state", "approval_state"):
        _require_optional_string(value.get(key), f"{field}.{key}", required=False)


def _validate_process_schedule(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": "process.schedule must be an object when provided",
            },
        )
    _require_optional_string(
        value.get("time_zone"),
        "process.schedule.time_zone",
        required=False,
    )
    trigger = value.get("trigger")
    if trigger is not None:
        if not isinstance(trigger, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": "process.schedule.trigger must be an object",
                },
            )
        kind = trigger.get("kind")
        if kind is not None and kind not in {"schedule", "event", "manual"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": "process.schedule.trigger.kind must be schedule|event|manual",
                },
            )
        _require_optional_string(
            trigger.get("event"),
            "process.schedule.trigger.event",
            required=False,
        )
    slots = value.get("slots")
    if slots is None:
        return
    if not isinstance(slots, list):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": "process.schedule.slots must be a list when provided",
            },
        )
    seen_slot_ids: set[str] = set()
    for index, slot in enumerate(slots):
        field = f"process.schedule.slots[{index}]"
        if not isinstance(slot, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_process", "message": f"{field} must be an object"},
            )
        slot_id = slot.get("id")
        _require_optional_string(slot_id, f"{field}.id", required=True)
        if isinstance(slot_id, str):
            if slot_id in seen_slot_ids:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": "process.schedule.slots must not contain duplicate ids",
                    },
                )
            seen_slot_ids.add(slot_id)
        local_time = slot.get("local_time")
        if not isinstance(local_time, str) or not re.match(r"^\d{2}:\d{2}$", local_time):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"{field}.local_time must use HH:MM local time",
                },
            )
        weekdays = slot.get("weekdays")
        if weekdays is not None and (
            not isinstance(weekdays, list)
            or any(not isinstance(day, int) or day < 0 or day > 6 for day in weekdays)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"{field}.weekdays must be weekday numbers 0-6",
                },
            )
        specialists = slot.get("specialist_ids", slot.get("specialists"))
        if not isinstance(specialists, list) or not specialists:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"{field}.specialist_ids must contain at least one specialist",
                },
            )
        normalized = []
        for specialist in specialists:
            if not isinstance(specialist, str) or not specialist.strip():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": f"{field}.specialist_ids must be non-empty strings",
                    },
                )
            normalized.append(specialist.strip())
        if len(normalized) != len(set(normalized)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": (
                        f"{field} contains the same specialist more than once; "
                        "split duplicate capacity into a different slot"
                    ),
                },
            )


def _validate_process_routines(value: Any) -> None:
    if value is None:
        return
    if isinstance(value, list):
        entries = [(str(index), routine, f"process.routines[{index}]", True) for index, routine in enumerate(value)]
    elif isinstance(value, dict):
        entries = [(str(key), routine, f"process.routines.{key}", False) for key, routine in value.items()]
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_process",
                "message": "process.routines must be a map or list when provided",
            },
        )
    for routine_id, routine, field, requires_inline_id in entries:
        if not isinstance(routine, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"{field} must be an object",
                },
            )
        if requires_inline_id:
            _require_optional_string(
                routine.get("id"),
                f"{field}.id",
                required=True,
            )
        elif not re.match(r"^[a-z0-9][a-z0-9_-]{0,63}$", routine_id):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"{field} has an invalid routine id",
                },
            )
        _require_optional_string(
            routine.get("name"),
            f"{field}.name",
            required=True,
        )
        _require_optional_string(
            routine.get("cadence"),
            f"{field}.cadence",
            required=False,
        )
        en = routine.get("enabled")
        if en is not None and not isinstance(en, bool):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"{field}.enabled must be a boolean when set",
                },
            )
        for opt_key in (
            "description",
            "instructions",
            "prompt",
            "pattern",
            "pattern_version",
            "agent_profile",
            "specialist_id",
            "specialist_name",
            "window",
            "event",
        ):
            _require_optional_string(
                routine.get(opt_key),
                f"{field}.{opt_key}",
                required=False,
            )
        mutation_text = " ".join(
            str(routine.get(key) or "")
            for key in ("description", "instructions", "prompt", "name")
        )
        if re.search(
            r"\b(pick|claim|move|transition|work on next|take next)\b.*\b(ticket|issue|task)\b",
            mutation_text,
            flags=re.IGNORECASE,
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": (
                        f"{field} looks like ticket-processing work; model it as "
                        "a scheduled process step, not a routine"
                    ),
                },
            )
        trigger = routine.get("trigger")
        if trigger is not None:
            if not isinstance(trigger, dict):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": f"{field}.trigger must be an object when provided",
                    },
                )
            trigger_type = trigger.get("type")
            if trigger_type is not None and trigger_type not in {"schedule", "event", "manual"}:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": f"{field}.trigger.type must be schedule|event|manual",
                    },
                )
            for key in ("cron", "event", "window", "catchup"):
                _require_optional_string(
                    trigger.get(key),
                    f"{field}.trigger.{key}",
                    required=False,
                )
        for object_key in ("scope", "output"):
            obj = routine.get(object_key)
            if obj is not None and not isinstance(obj, dict):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": f"{field}.{object_key} must be an object when provided",
                    },
                )
        prompt_record = routine.get("prompt_record")
        if prompt_record is not None:
            if not isinstance(prompt_record, dict):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": f"{field}.prompt_record must be an object when provided",
                    },
                )
            _require_optional_string(
                prompt_record.get("id"),
                f"{field}.prompt_record.id",
                required=True,
            )
            if not isinstance(prompt_record.get("version"), int) or prompt_record["version"] < 1:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": f"{field}.prompt_record.version must be a positive integer",
                    },
                )
            _require_optional_string(
                prompt_record.get("source"),
                f"{field}.prompt_record.source",
                required=True,
            )
            assumptions = prompt_record.get("assumptions")
            if assumptions is not None and (
                not isinstance(assumptions, list)
                or any(not isinstance(item, str) or not item.strip() for item in assumptions)
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": f"{field}.prompt_record.assumptions must be strings",
                    },
                )
        output = routine.get("output")
        if isinstance(output, dict):
            destination = output.get("destination")
            if destination is not None and destination not in {
                "inbox",
                "digest",
                "tracker_comment",
                "pr_comment",
                "slack_later",
            }:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "code": "invalid_process",
                        "message": f"{field}.output.destination is not supported",
                    },
                )
        patterns = routine.get("patterns")
        if patterns is not None and (
            not isinstance(patterns, list)
            or any(not isinstance(item, str) or not item.strip() for item in patterns)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"{field}.patterns must be a list of non-empty strings",
                },
            )
        sch = routine.get("schedule")
        if sch is not None and not isinstance(sch, (dict, str)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_process",
                    "message": f"{field}.schedule must be an object or cron string when set",
                },
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
