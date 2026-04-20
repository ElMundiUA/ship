"""Pipelines API — list, toggle, run, install workflow, run callback (Day-4 Phase-1).

Day 3 of the pilot landed the dashboard with a *stub* "Run now" that
just inserted a ``succeeded`` row. Day-4 Phase-1 turns that into an
honest GitHub Actions ``workflow_dispatch`` for the ``pr_review``
pipeline kind end-to-end:

- ``POST /workspaces/{ws}/pipelines/{id}/runs`` (Run now) probes the
  customer repo for the starter workflow and either dispatches it
  (returning a ``running`` row) or returns ``412 workflow_not_installed``
  with an install hint.
- ``POST /workspaces/{ws}/pipelines/{id}/install`` opens a PR in the
  customer repo via the App's ``contents:write`` permission, adding
  the starter workflow YAML so Run now becomes available once merged.
- ``POST /v1/pipelines/runs/{run_id}/result`` (no session — bearer
  ``run_token`` only) is the callback the dispatched workflow hits to
  report success/failure. Token verification compares against the
  SHA-256 stored on the ``PipelineRun`` row + a 5-minute JWT exp, so
  a stolen ``run_id`` alone can't fake a result.

All four other pipeline kinds (``daily_standup``, ``code_map``,
``tech_debt``, ``self_heal``) keep their seed rows but the dispatcher
returns ``412 kind_not_supported_yet`` — the dashboard renders a
"Coming with presets" badge for them. Phase 2 lifts that restriction.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Final

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from jose import JWTError, jwt
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import Pipeline, PipelineRun
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.github.app_auth import GitHubAppMisconfigured
from backend.app.integrations.github.workflows import (
    StarterWorkflowPR,
    WorkflowDispatchError,
    commit_starter_workflow,
    dispatch_workflow,
    invalidate_workflow_list_cache,
    list_repo_workflows,
)
from backend.app.services import catalog as catalog_service
from backend.app.services.default_pipelines import DEFAULT_PIPELINES


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline kind ↔ catalog workflow mapping
# ---------------------------------------------------------------------------

# ``DEFAULT_PIPELINES`` is the authoritative kind → workflow_id map
# (``pr_review`` → ``pr-and-ci-gate``, ``self_heal`` → ``pipeline-self-heal``,
# …). Everything filename-related comes from the workflow's catalog entry
# so adding a new lane = dropping a new ARTIFACT.md + workflow.yml pair
# and extending ``DEFAULT_PIPELINES`` — no code churn in this module.
_KIND_TO_WORKFLOW_ID: Final[dict[str, str]] = {
    spec.kind: spec.workflow_id for spec in DEFAULT_PIPELINES
}


def _workflow_file_for_kind(kind: str) -> str | None:
    """Basename the customer repo will contain, via catalog ``install_target``.

    Returns ``None`` when the kind has no catalog-backed workflow (e.g.
    ``code_map`` — resolver-only) so callers know to fall back to the
    "Coming with presets" state instead of 412-ing the user.
    """
    workflow_id = _KIND_TO_WORKFLOW_ID.get(kind)
    if workflow_id is None:
        return None
    return catalog_service.workflow_install_filename(workflow_id)


def _supports_run(kind: str) -> bool:
    """True iff we can both dispatch and (re)install the workflow for ``kind``."""
    workflow_id = _KIND_TO_WORKFLOW_ID.get(kind)
    if workflow_id is None:
        return False
    entry = catalog_service.get_workflow(workflow_id)
    if entry is None or entry.install_filename is None:
        return False
    return catalog_service.read_starter_yaml(workflow_id) is not None


# ---------------------------------------------------------------------------
# Run-token (callback bearer) helpers
# ---------------------------------------------------------------------------

# Subject baked into the callback JWT so a stray Auth0 / install-state
# token can't accidentally satisfy the verifier — defence in depth.
_RUN_TOKEN_SUBJECT: Final[str] = "ship.pipeline.run"
# Workflow runs typically finish in seconds–minutes; 30 minutes is
# plenty of head-room for a slow runner without leaving a long replay
# window if the inputs leak out of the runner logs.
_RUN_TOKEN_TTL_SECONDS: Final[int] = 30 * 60


def _mint_run_token(run_id: uuid.UUID, settings: Settings) -> str:
    issued_at = int(time.time())
    claims = {
        "sub": _RUN_TOKEN_SUBJECT,
        "rid": str(run_id),
        "nonce": secrets.token_urlsafe(8),
        "iat": issued_at,
        "exp": issued_at + _RUN_TOKEN_TTL_SECONDS,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def _decode_run_token(token: str, settings: Settings) -> uuid.UUID:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token is invalid or expired",
        ) from exc
    if claims.get("sub") != _RUN_TOKEN_SUBJECT:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token has wrong subject",
        )
    raw = claims.get("rid")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token missing rid",
        )
    try:
        return uuid.UUID(str(raw))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token has malformed rid",
        ) from exc


def _hash_run_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read_starter_yaml(kind: str) -> str:
    """Return the YAML body for the kind's starter workflow or 412/500.

    ``kind_not_supported_yet`` when we have no catalog mapping for the
    pipeline kind (e.g. ``code_map`` — resolver-only, no workflow
    artefact today). ``500`` only when the mapping exists but the YAML
    file on disk is missing — a release-packaging bug the operator
    needs to see, not a tenant-actionable condition.
    """
    workflow_id = _KIND_TO_WORKFLOW_ID.get(kind)
    if workflow_id is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "kind_not_supported_yet",
                "message": (
                    f"Pipeline kind {kind!r} has no catalog workflow. "
                    "Coming with Phase 3 presets."
                ),
            },
        )
    content = catalog_service.read_starter_yaml(workflow_id)
    if content is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "kind_not_supported_yet",
                "message": (
                    f"Workflow {workflow_id!r} for kind {kind!r} has no "
                    "installable YAML yet. Coming with presets."
                ),
            },
        )
    return content


# ---------------------------------------------------------------------------
# Routers — workspace-scoped (RBAC) + global (callback only)
# ---------------------------------------------------------------------------


router = APIRouter(
    prefix="/workspaces/{workspace_id}/pipelines",
    tags=["pipelines"],
)

# Separate router for endpoints that don't fit the workspace prefix.
# Mounted under ``/v1`` directly so the dispatched workflow only needs
# the run id + bearer token (no session, no workspace path component).
public_router = APIRouter(prefix="/pipelines", tags=["pipelines"])


_RUNS_PAGE_LIMIT = 20
_RUNS_PAGE_DEFAULT = 10


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PipelineOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    workflow_id: str
    enabled: bool
    config: dict
    last_run_at: datetime | None
    last_run_status: str | None
    created_at: datetime
    updated_at: datetime
    # Day-4 Phase-1 additions — drive the dashboard card states
    # (installed / not-installed / coming-soon) without a second
    # round-trip per render.
    repo_id: uuid.UUID | None
    repo_full_name: str | None
    workflow_installed: bool | None
    workflow_file: str | None
    supports_run: bool


class PipelineToggleIn(BaseModel):
    enabled: bool


class PipelineRunOut(BaseModel):
    id: uuid.UUID
    pipeline_id: uuid.UUID
    trigger: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    summary: str | None
    payload: dict
    created_at: datetime


class PipelineRunIn(BaseModel):
    """Optional client-supplied payload for a manual run."""

    note: str | None = Field(
        default=None,
        max_length=500,
        description="Optional human note shown in the run history.",
    )


class PipelineInstallOut(BaseModel):
    pr_url: str
    pr_number: int
    branch: str


class PipelineRunResultIn(BaseModel):
    """Body of the ``POST /pipelines/runs/{run_id}/result`` callback."""

    status: str = Field(
        ...,
        description=(
            "Terminal status of the run. One of ``succeeded`` / ``failed`` "
            "/ ``cancelled``. Anything else is rejected with 422."
        ),
    )
    summary: str | None = Field(default=None, max_length=1024)
    metrics: dict = Field(default_factory=dict)


_TERMINAL_STATUSES: Final[set[str]] = {"succeeded", "failed", "cancelled"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_out(
    row: Pipeline,
    *,
    repo: WorkspaceRepo | None = None,
    workflow_installed: bool | None = None,
) -> PipelineOut:
    workflow_file = _workflow_file_for_kind(row.kind)
    return PipelineOut(
        id=row.id,
        kind=row.kind,
        name=row.name,
        workflow_id=row.workflow_id,
        enabled=row.enabled,
        config=row.config or {},
        last_run_at=row.last_run_at,
        last_run_status=row.last_run_status,
        created_at=row.created_at,
        updated_at=row.updated_at,
        repo_id=row.repo_id,
        repo_full_name=repo.full_name if repo else None,
        workflow_installed=workflow_installed,
        workflow_file=workflow_file,
        supports_run=_supports_run(row.kind),
    )


def _run_to_out(row: PipelineRun) -> PipelineRunOut:
    return PipelineRunOut(
        id=row.id,
        pipeline_id=row.pipeline_id,
        trigger=row.trigger,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        summary=row.summary,
        payload=row.payload or {},
        created_at=row.created_at,
    )


async def _load_pipeline(
    session: AsyncSession, workspace_id: uuid.UUID, pipeline_id: uuid.UUID
) -> Pipeline:
    stmt = select(Pipeline).where(
        Pipeline.workspace_id == workspace_id, Pipeline.id == pipeline_id
    )
    row = (await session.execute(stmt)).scalars().first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return row


async def _auto_bind_pipeline_to_repo(
    session: AsyncSession, pipeline: Pipeline
) -> WorkspaceRepo | None:
    """If the workspace has exactly one GH-backed repo, bind and return it.

    Phase-3 convenience: legacy pipelines (seeded before Day-4 added
    the binding FK) or pipelines created under edge-cases can have
    ``repo_id=None``. In the common case — a single activated repo —
    the "right" binding is obvious and asking the user "pick a repo"
    is just friction on a pilot with one-repo tenants. We bind
    in-place and let the caller re-check. For workspaces with multiple
    activated repos the answer isn't obvious so we return ``None`` and
    the 412 picker flow kicks in.

    Idempotent (caller already knows ``pipeline.repo_id`` is ``None``).
    The mutation is flushed so subsequent ``session.get`` by callers
    in the same request see the new binding.
    """
    stmt = (
        select(WorkspaceRepo)
        .where(WorkspaceRepo.workspace_id == pipeline.workspace_id)
        .where(WorkspaceRepo.installation_id.is_not(None))
        .order_by(WorkspaceRepo.full_name)
    )
    rows = (await session.execute(stmt)).scalars().all()
    if len(rows) != 1:
        return None
    repo = rows[0]
    pipeline.repo_id = repo.id
    # Pin ``updated_at`` client-side — same rationale as
    # :func:`enrich_pipelines` (avoid the ORM expiring the attribute
    # after the onupdate server-default fires).
    pipeline.updated_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info(
        "auto-bound pipeline pipeline_id=%s workspace_id=%s repo_id=%s (%s)",
        pipeline.id,
        pipeline.workspace_id,
        repo.id,
        repo.full_name,
    )
    return repo


async def _load_repo_and_install(
    session: AsyncSession, pipeline: Pipeline
) -> tuple[WorkspaceRepo, GitHubInstallation]:
    """Resolve the repo + install backing a pipeline or raise 412.

    Centralised so the dispatcher and the install endpoint reuse the
    same precondition story. The 412s carry a ``code`` field the
    console can switch on to decide which CTA to show (re-bind, install
    workflow, reinstall the App).

    If the pipeline row carries no ``repo_id`` yet (legacy seed, or
    first run before the Day-4 backfill caught up) we try to
    auto-bind to the workspace's sole activated repo. Most pilot
    tenants have exactly one repo, and the "obvious" binding there is
    the friction we want to avoid. Only when auto-bind can't decide
    (zero or multiple repos) do we return ``pipeline_not_bound``.
    """
    if pipeline.repo_id is None:
        repo = await _auto_bind_pipeline_to_repo(session, pipeline)
        if repo is None:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail={
                    "code": "pipeline_not_bound",
                    "message": (
                        "No activated repo to bind this pipeline to. "
                        "Open the onboarding wizard and activate a repo, "
                        "or pick one from the Repos tab."
                    ),
                },
            )
        # Auto-bound path — fall through using the freshly-resolved repo
        # instead of doing a redundant session.get.
        if repo.installation_id is None:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail={
                    "code": "github_app_missing",
                    "message": (
                        "Repo isn't backed by a GitHub App installation. "
                        "Reinstall the Ship App."
                    ),
                },
            )
        install = await session.get(GitHubInstallation, repo.installation_id)
        if install is None or install.suspended_at is not None:
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail={
                    "code": "github_app_missing",
                    "message": (
                        "GitHub App installation is suspended or gone. "
                        "Reinstall the Ship App."
                    ),
                },
            )
        return repo, install
    repo = await session.get(WorkspaceRepo, pipeline.repo_id)
    if repo is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "pipeline_not_bound",
                "message": (
                    "Bound repo no longer exists. Re-activate a repo "
                    "from the wizard."
                ),
            },
        )
    if repo.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Repo isn't backed by a GitHub App installation. "
                    "Reinstall the Ship App."
                ),
            },
        )
    install = await session.get(GitHubInstallation, repo.installation_id)
    if install is None or install.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "GitHub App installation is suspended or gone. "
                    "Reinstall the Ship App."
                ),
            },
        )
    return repo, install


def _callback_url(settings: Settings, run_id: uuid.UUID) -> str:
    """Build the absolute callback URL the dispatched workflow hits."""
    base = settings.public_url.rstrip("/")
    return f"{base}/v1/pipelines/runs/{run_id}/result"


# ---------------------------------------------------------------------------
# Routes — workspace-scoped
# ---------------------------------------------------------------------------


async def enrich_pipelines(
    session: AsyncSession,
    pipelines: list[Pipeline],
    *,
    settings: Settings,
) -> list[PipelineOut]:
    """Resolve repo + workflow-installed flags for a batch of pipelines.

    Shared between :func:`list_pipelines` and
    :func:`backend.app.api.v1.routes.dashboard.get_dashboard` so both
    surfaces present the dashboard's three card states (Run-now /
    Install-workflow / Coming-soon) consistently. Probe errors are
    swallowed per (install, repo) pair so one bad GitHub call doesn't
    blank an unrelated card.
    """
    # Phase-3 convenience: before the probe, opportunistically bind any
    # still-unbound pipelines to the workspace's sole activated repo.
    # This is the same rule the dispatcher applies on Run-now/Install,
    # but doing it at dashboard-load time means the UI renders
    # "Install workflow PR" instead of a confusing "Coming with presets"
    # badge on refresh. Only touches pipelines that share the same
    # workspace_id (the caller already filtered by workspace).
    unbound = [p for p in pipelines if p.repo_id is None]
    if unbound:
        now_utc = datetime.now(timezone.utc)
        workspace_ids = {p.workspace_id for p in unbound}
        mutated = False
        for workspace_id in workspace_ids:
            repo_candidates = (
                await session.execute(
                    select(WorkspaceRepo)
                    .where(WorkspaceRepo.workspace_id == workspace_id)
                    .where(WorkspaceRepo.installation_id.is_not(None))
                    .order_by(WorkspaceRepo.full_name)
                )
            ).scalars().all()
            if len(repo_candidates) != 1:
                continue
            default_repo = repo_candidates[0]
            for pipeline in unbound:
                if pipeline.workspace_id == workspace_id:
                    pipeline.repo_id = default_repo.id
                    # Set ``updated_at`` client-side so the server-side
                    # ``onupdate=now()`` doesn't expire the attribute on
                    # the ORM row — otherwise the sync ``_row_to_out``
                    # below tries to lazy-reload it and explodes in the
                    # async context.
                    pipeline.updated_at = now_utc
                    mutated = True
        if mutated:
            await session.flush()

    repo_ids = {r.repo_id for r in pipelines if r.repo_id is not None}
    repos: dict[uuid.UUID, WorkspaceRepo] = {}
    if repo_ids:
        repo_rows = (
            await session.execute(
                select(WorkspaceRepo).where(WorkspaceRepo.id.in_(repo_ids))
            )
        ).scalars().all()
        repos = {r.id: r for r in repo_rows}

    install_ids = {r.installation_id for r in repos.values() if r.installation_id}
    installs: dict[uuid.UUID, GitHubInstallation] = {}
    if install_ids:
        inst_rows = (
            await session.execute(
                select(GitHubInstallation).where(
                    GitHubInstallation.id.in_(install_ids)
                )
            )
        ).scalars().all()
        installs = {r.id: r for r in inst_rows}

    workflow_sets: dict[tuple[int, str], frozenset[str]] = {}
    out: list[PipelineOut] = []
    for row in pipelines:
        repo = repos.get(row.repo_id) if row.repo_id else None
        workflow_file = _workflow_file_for_kind(row.kind)
        if repo is None or workflow_file is None or not _supports_run(row.kind):
            out.append(_row_to_out(row, repo=repo, workflow_installed=None))
            continue
        install = installs.get(repo.installation_id) if repo.installation_id else None
        if install is None or install.suspended_at is not None:
            out.append(_row_to_out(row, repo=repo, workflow_installed=False))
            continue
        cache_key = (install.installation_id, repo.full_name)
        if cache_key not in workflow_sets:
            try:
                workflow_sets[cache_key] = await list_repo_workflows(
                    repo, install, settings=settings
                )
            except (
                WorkflowDispatchError,
                httpx.HTTPError,
                GitHubAppMisconfigured,
            ) as exc:
                # Probe is best-effort — dashboard should still render if
                # GitHub is unreachable or the App creds went missing.
                logger.warning(
                    "workflow probe failed repo=%s err=%s", repo.full_name, exc
                )
                workflow_sets[cache_key] = frozenset()
        files = workflow_sets[cache_key]
        installed = workflow_file in files
        out.append(_row_to_out(row, repo=repo, workflow_installed=installed))

    return out


@router.get("", response_model=list[PipelineOut])
async def list_pipelines(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[PipelineOut]:
    """All pipelines for the workspace, with workflow-availability flags."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    stmt = (
        select(Pipeline)
        .where(Pipeline.workspace_id == workspace_id)
        .order_by(Pipeline.created_at)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return await enrich_pipelines(session, list(rows), settings=settings)


@router.patch("/{pipeline_id}", response_model=PipelineOut)
async def toggle_pipeline(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    payload: PipelineToggleIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PipelineOut:
    """Flip a pipeline on or off. Admin-only."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _load_pipeline(session, workspace_id, pipeline_id)
    if row.enabled == payload.enabled:
        return _row_to_out(row)
    row.enabled = payload.enabled
    row.updated_at = datetime.now(timezone.utc)

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="pipeline.toggle",
            target_kind="pipeline",
            target_id=str(row.id),
            payload={"kind": row.kind, "enabled": row.enabled},
        )
    )
    await session.flush()
    return _row_to_out(row)


@router.post(
    "/{pipeline_id}/runs",
    response_model=PipelineRunOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_pipeline(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    payload: PipelineRunIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PipelineRunOut:
    """Dispatch a real GitHub Actions ``workflow_dispatch`` for the pipeline.

    Day-4 Phase-1 contract:

    - Returns ``202 Accepted`` with a ``running`` row when GitHub
      acknowledged the dispatch. The actual terminal state arrives
      later via the result callback or the ``workflow_run`` webhook
      (whichever wins the race).
    - Returns ``412`` with a structured ``code`` field when a
      precondition fails — pipeline not bound to a repo
      (``pipeline_not_bound``), GitHub App gone
      (``github_app_missing``), kind not yet supported
      (``kind_not_supported_yet``), or workflow file not yet in the
      customer repo (``workflow_not_installed``). The console reads
      the code to decide which CTA to render (Install / Reinstall /
      Re-bind / Coming-soon).
    - Returns ``502`` when GitHub's dispatch endpoint itself errors.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    pipeline = await _load_pipeline(session, workspace_id, pipeline_id)
    if not pipeline.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Pipeline is disabled. Enable it before running.",
        )
    workflow_file = _workflow_file_for_kind(pipeline.kind)
    if workflow_file is None or not _supports_run(pipeline.kind):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "kind_not_supported_yet",
                "message": (
                    f"Pipeline kind {pipeline.kind!r} doesn't ship with a "
                    "catalog workflow yet. Coming with presets."
                ),
            },
        )

    repo, install = await _load_repo_and_install(session, pipeline)

    files = await list_repo_workflows(repo, install, settings=settings)
    if workflow_file not in files:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "workflow_not_installed",
                "workflow_file": workflow_file,
                "repo_full_name": repo.full_name,
                "install_endpoint": (
                    f"/v1/workspaces/{workspace_id}/pipelines/{pipeline_id}/install"
                ),
                "message": (
                    f"{repo.full_name!r} doesn't have .github/workflows/"
                    f"{workflow_file}. Open the install PR first."
                ),
            },
        )

    note = (payload.note if payload else None) or None
    now = datetime.now(timezone.utc)

    run = PipelineRun(
        pipeline_id=pipeline.id,
        workspace_id=workspace_id,
        trigger="manual",
        status="queued",
        started_at=now,
        summary=note or f"Dispatched {pipeline.name} for {repo.full_name}",
        payload={"note": note} if note else {},
    )
    session.add(run)
    # Need ``run.id`` to mint the callback token + URL; flush before
    # talking to GitHub so the FK exists when the callback lands.
    await session.flush()

    token = _mint_run_token(run.id, settings)
    run.run_token_hash = _hash_run_token(token)

    callback_url = _callback_url(settings, run.id)
    inputs = {
        "ship_run_id": str(run.id),
        "ship_callback_url": callback_url,
        "ship_run_token": token,
    }
    try:
        await dispatch_workflow(
            repo,
            install,
            workflow_file,
            inputs=inputs,
            settings=settings,
        )
    except WorkflowDispatchError as exc:
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.summary = (
            f"GitHub dispatch failed (HTTP {exc.status_code}): {exc.message[:256]}"
        )
        pipeline.last_run_status = "failed"
        pipeline.last_run_at = run.finished_at
        await session.flush()
        # Map upstream-level failures to a 502 so the console can
        # surface "GitHub said no" without confusing the user with our
        # 412 precondition vocabulary.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "dispatch_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
            },
        ) from exc

    run.status = "running"
    pipeline.last_run_at = now
    pipeline.last_run_status = "running"
    pipeline.updated_at = now

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="pipeline.run",
            target_kind="pipeline",
            target_id=str(pipeline.id),
            payload={
                "kind": pipeline.kind,
                "trigger": "manual",
                "note": note,
                "run_id": str(run.id),
                "repo_full_name": repo.full_name,
                "workflow_file": workflow_file,
            },
        )
    )
    await session.flush()
    return _run_to_out(run)


@router.post(
    "/{pipeline_id}/install",
    response_model=PipelineInstallOut,
    status_code=status.HTTP_201_CREATED,
)
async def install_pipeline_workflow(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PipelineInstallOut:
    """Open a PR in the bound repo that installs the starter workflow.

    Admin-only. Idempotent in the sense that the customer can press
    Install repeatedly — each call opens a fresh PR with a unique
    branch name (``ship/install-<kind>-<unix>``); merging any of
    them is enough to unlock Run now. We don't look up the prior PR
    because GitHub's "find PR by head ref" API requires either a
    paginated list or a search call, and the timestamped branches
    keep the cost on the customer side (close stale PRs manually).
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    pipeline = await _load_pipeline(session, workspace_id, pipeline_id)
    workflow_file = _workflow_file_for_kind(pipeline.kind)
    if workflow_file is None or not _supports_run(pipeline.kind):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "kind_not_supported_yet",
                "message": (
                    f"Pipeline kind {pipeline.kind!r} doesn't ship with a "
                    "starter workflow yet. Coming with presets."
                ),
            },
        )
    repo, install = await _load_repo_and_install(session, pipeline)
    content = _read_starter_yaml(pipeline.kind)
    try:
        result: StarterWorkflowPR = await commit_starter_workflow(
            repo,
            install,
            workflow_file=workflow_file,
            content=content,
            pipeline_kind=pipeline.kind,
            settings=settings,
        )
    except WorkflowDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "install_pr_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
            },
        ) from exc

    # Bust the workflow-list cache so the next dashboard render
    # immediately picks up the (eventually) merged file. We also bust
    # in the webhook handler when the PR is merged, but invalidating
    # here means the customer's "I just merged it" refresh isn't held
    # back by our 60-second TTL.
    invalidate_workflow_list_cache(
        installation_id=install.installation_id, full_name=repo.full_name
    )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="pipeline.install",
            target_kind="pipeline",
            target_id=str(pipeline.id),
            payload={
                "kind": pipeline.kind,
                "repo_full_name": repo.full_name,
                "workflow_file": workflow_file,
                "pr_url": result.pr_url,
                "pr_number": result.pr_number,
                "branch": result.branch,
            },
        )
    )
    await session.flush()
    return PipelineInstallOut(
        pr_url=result.pr_url,
        pr_number=result.pr_number,
        branch=result.branch,
    )


@router.get("/{pipeline_id}/runs", response_model=list[PipelineRunOut])
async def list_pipeline_runs(
    workspace_id: uuid.UUID,
    pipeline_id: uuid.UUID,
    limit: int = _RUNS_PAGE_DEFAULT,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[PipelineRunOut]:
    """Most-recent-first page of run history. Members can read."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _load_pipeline(session, workspace_id, pipeline_id)
    capped = max(1, min(limit, _RUNS_PAGE_LIMIT))
    stmt = (
        select(PipelineRun)
        .where(PipelineRun.pipeline_id == pipeline_id)
        .order_by(desc(PipelineRun.started_at), desc(PipelineRun.created_at))
        .limit(capped)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_run_to_out(r) for r in rows]


# ---------------------------------------------------------------------------
# Routes — global (callback only, bearer-token auth)
# ---------------------------------------------------------------------------


@public_router.post(
    "/runs/{run_id}/result",
    response_model=PipelineRunOut,
)
async def report_run_result(
    run_id: uuid.UUID = Path(...),
    payload: PipelineRunResultIn = ...,
    authorization: str | None = Header(default=None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PipelineRunOut:
    """Callback endpoint dispatched workflows hit to report their result.

    Authentication is *only* the bearer ``run_token`` we minted at
    dispatch time. We verify in three layers:

    1. JWT signature + ``sub`` + ``exp`` (rejects a stale token from
       a previous dispatch).
    2. ``rid`` claim matches the path ``run_id`` (rejects a token
       being replayed against a different run).
    3. SHA-256 of the bearer matches ``PipelineRun.run_token_hash``
       (rejects a forged JWT signed with a leaked ``JWT_SECRET`` —
       belt-and-braces; if the secret leaks we have bigger problems,
       but the hash check costs us nothing).

    Once accepted we mark the run terminal and mirror the status onto
    the parent ``Pipeline`` row for the dashboard's "last run" badge.
    Idempotent: a duplicate callback for an already-terminal run
    returns 200 with the existing row instead of erroring.
    """
    if payload.status not in _TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"status must be one of {sorted(_TERMINAL_STATUSES)}; "
                f"got {payload.status!r}"
            ),
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    raw_token = authorization.split(" ", 1)[1].strip()
    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="empty bearer token",
        )

    decoded_run_id = _decode_run_token(raw_token, settings)
    if decoded_run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token does not match path run_id",
        )

    run = await session.get(PipelineRun, run_id)
    if run is None or run.run_token_hash is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not secrets.compare_digest(run.run_token_hash, _hash_run_token(raw_token)):
        # JWT was structurally fine but the hash didn't match — either
        # a leaked secret was used to mint a fake token, or we already
        # rotated the run_token for this run (we don't, but document
        # the safety net).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="run token hash mismatch",
        )

    if run.status in _TERMINAL_STATUSES:
        # Idempotent: workflow's `if: always()` reporter sometimes
        # races with our webhook reconciliation; the first writer wins
        # and the second call is a no-op echo.
        return _run_to_out(run)

    now = datetime.now(timezone.utc)
    run.status = payload.status
    run.finished_at = now
    if payload.summary:
        run.summary = payload.summary[:1024]
    metrics_payload = dict(payload.metrics or {})
    if metrics_payload:
        existing = dict(run.payload or {})
        existing.setdefault("metrics", {}).update(metrics_payload)
        run.payload = existing
    run.updated_at = now

    pipeline = await session.get(Pipeline, run.pipeline_id)
    if pipeline is not None:
        pipeline.last_run_status = payload.status
        pipeline.last_run_at = now
        pipeline.updated_at = now

    session.add(
        AuditLog(
            workspace_id=run.workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action="pipeline.run.callback",
            target_kind="pipeline_run",
            target_id=str(run.id),
            payload={
                "status": payload.status,
                "metrics": metrics_payload,
            },
        )
    )
    await session.flush()
    return _run_to_out(run)


__all__ = ["router", "public_router"]
