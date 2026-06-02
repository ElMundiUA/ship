"""Deploy routes — trigger a deployment and poll its status.

POST /v1/workspaces/{ws}/repos/{repo_id}/deploy
    Plans the deployment with the LLM, submits to DigitalOcean App
    Platform, and persists a ``Deployment`` row. Returns the row
    immediately with ``status=deploying`` (non-blocking).

GET  /v1/workspaces/{ws}/deployments/{deployment_id}
    Returns the latest state of a deployment. On each call we lazily
    poll the provider (DO API) and update the row before returning.
    This keeps the cloud SaaS path worker-free: the console polls
    every few seconds and each poll does one cheap DO API read.

GET  /v1/workspaces/{ws}/repos/{repo_id}/deployments
    Lists recent deployments for a repo (newest first, limit 20).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Path, status
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.deploy import Deployment
from backend.app.db.models.deploy import DeploymentStatus as DS
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.repo_intel import RepoIntel
from backend.app.db.session import get_session
from backend.app.integrations.digitalocean import client as do_client
from backend.app.integrations.gateway.code_host import RepoRef
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.integrations.github.actions_secrets import (
    list_repo_secrets as list_github_repo_secrets,
)
from backend.app.integrations.github.workflows import (
    WorkflowDispatchError,
    dispatch_workflow,
    list_repo_workflows,
)
from backend.app.services.deploy.credentials import get_do_token
from backend.app.services.deploy.events import list_app_events, record_event
from backend.app.services.deploy.health import health_check_path, probe
from backend.app.services.deploy.teardown import teardown_repo_app
from backend.app.db.models.deploy import DeploymentEventKind
from backend.app.services.deploy.plan import DeployPlan
from backend.app.services.deploy.llm import PlannerLLMCredentials
from backend.app.services.deploy.model_catalog import (
    PROVIDER_DEFAULT_MODEL,
    list_planner_models,
)
from backend.app.services.deploy.planner import DeployPlanningError, plan_deployment
from backend.app.services.deploy.providers.digitalocean import (
    DigitalOceanAppPlatform,
)
from backend.app.services.repo_intel import get_current_intel, _load_install


logger = logging.getLogger(__name__)

router = APIRouter(tags=["deploy"])

_DEPLOY_PLAN_WORKFLOW = "ship-deploy-plan.yml"
_DEPLOY_TOKEN_SUBJECT = "ship.deploy.plan"
_DEPLOY_TOKEN_TTL_SECONDS = 30 * 60
_PLANNER_SECRET_BY_PROVIDER = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}

# Friendly message written when a private-repo deploy fails because
# DigitalOcean can't reach the source (its GitHub app isn't authorized on
# the repo). ``_to_out`` recognises this exact string and stamps
# ``error_kind="github_access"`` so the console can render the
# "Authorize DigitalOcean on GitHub / make repo public" recovery buttons
# instead of a raw provider error. Keep the string and the marker in sync.
_GITHUB_ACCESS_ERROR_KIND = "github_access"
_GITHUB_ACCESS_HINT = (
    "DigitalOcean couldn't access this private repository. Authorize the "
    "DigitalOcean GitHub app on the repo (or make the repo public), then "
    "redeploy."
)
# Substrings that, on a private-repo submit failure, point at a DO↔GitHub
# authorization gap rather than a genuine build error.
_GITHUB_ACCESS_MARKERS = (
    "github",
    "not authenticated",
    "unable to",
    "repository",
    "not found",
    "permission",
    "access",
    "forbidden",
)


def _looks_like_github_access_error(raw: str) -> bool:
    low = raw.lower()
    return any(marker in low for marker in _GITHUB_ACCESS_MARKERS)


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ApiDeploymentOut(BaseModel):
    id: str
    workspace_id: str
    repo_id: str
    repo_full_name: str | None
    provider: str
    status: str
    status_detail: str | None
    live_url: str | None
    healthy: bool | None
    error_message: str | None
    # Coarse failure category for the console to branch on. Currently only
    # "github_access" (private repo not reachable by DO's GitHub app);
    # None for any other / no error.
    error_kind: str | None = None
    started_at: str | None
    finished_at: str | None
    created_at: str
    updated_at: str
    plan_summary: str | None


class TriggerDeployIn(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None


class DeployPlanResultIn(BaseModel):
    status: str
    plan: DeployPlan | None = None
    error: str | None = None


class DeployPlannerOptionsOut(BaseModel):
    providers: list[str]
    missing: list[str]


class DeployPlannerModelsIn(BaseModel):
    provider: str
    # Optional plaintext key pasted in the modal. Used only to call the
    # provider's list-models API for this request; never stored. Repo
    # GitHub-secret keys can't be read back, so this is how an operator
    # pulls a live list without a backend env key.
    api_key: str | None = None


class DeployPlannerPrefIn(BaseModel):
    # Persisted per-repo planner preference. Either may be null to clear.
    provider: str | None = None
    model: str | None = None


class DeployPlannerPrefOut(BaseModel):
    provider: str | None
    model: str | None


class DeployPlannerModelsOut(BaseModel):
    provider: str
    models: list[str]
    default_model: str
    # "live" when ids came from the provider API, "fallback" when we
    # served the curated list (no backend key for the provider, or the
    # upstream call failed). ``error`` carries a short reason on fallback.
    source: str
    error: str | None = None


def _to_out(d: Deployment, *, repo_full_name: str | None = None) -> ApiDeploymentOut:
    plan_summary = None
    if d.plan:
        plan_summary = d.plan.get("summary")
    return ApiDeploymentOut(
        id=str(d.id),
        workspace_id=str(d.workspace_id),
        repo_id=str(d.repo_id),
        repo_full_name=repo_full_name,
        provider=d.provider,
        status=d.status,
        status_detail=d.status_detail,
        live_url=d.live_url,
        healthy=d.healthy,
        error_message=d.error_message,
        error_kind=(
            _GITHUB_ACCESS_ERROR_KIND
            if d.error_message == _GITHUB_ACCESS_HINT
            else None
        ),
        started_at=d.started_at.isoformat() if d.started_at else None,
        finished_at=d.finished_at.isoformat() if d.finished_at else None,
        created_at=d.created_at.isoformat(),
        updated_at=d.updated_at.isoformat(),
        plan_summary=plan_summary,
    )


async def _event(
    session: AsyncSession, dep: Deployment, kind: str, message: str
) -> None:
    """Record an activity event for a deployment's app (best-effort wrapper)."""
    await record_event(
        session,
        workspace_id=dep.workspace_id,
        repo_id=dep.repo_id,
        provider=dep.provider,
        kind=kind,
        message=message,
        deployment_id=dep.id,
    )


def _mint_deploy_token(deployment_id: uuid.UUID, settings: Settings) -> str:
    issued_at = int(time.time())
    claims = {
        "sub": _DEPLOY_TOKEN_SUBJECT,
        "did": str(deployment_id),
        "nonce": secrets.token_urlsafe(8),
        "iat": issued_at,
        "exp": issued_at + _DEPLOY_TOKEN_TTL_SECONDS,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm="HS256")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _parse_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="empty bearer token")
    return token


def _verify_deploy_token(
    *,
    raw_token: str,
    deployment_id: uuid.UUID,
    dep: Deployment,
    settings: Settings,
) -> None:
    try:
        claims = jwt.decode(
            raw_token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub"]},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="deploy token is invalid or expired",
        ) from exc
    if claims.get("sub") != _DEPLOY_TOKEN_SUBJECT or claims.get("did") != str(deployment_id):
        raise HTTPException(status_code=401, detail="deploy token has wrong subject")
    expected = (dep.provider_ref or {}).get("planner_token_hash")
    if not expected or not secrets.compare_digest(expected, _hash_token(raw_token)):
        raise HTTPException(status_code=401, detail="deploy token mismatch")


async def _repo_name_map(
    session: AsyncSession, repo_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Resolve {repo_id: full_name} for a set of deployment rows."""
    if not repo_ids:
        return {}
    rows = (
        await session.execute(
            select(WorkspaceRepo.id, WorkspaceRepo.full_name).where(
                WorkspaceRepo.id.in_(set(repo_ids))
            )
        )
    ).all()
    return {rid: name for rid, name in rows}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_repo(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
) -> WorkspaceRepo:
    repo = await session.get(WorkspaceRepo, repo_id)
    if repo is None or repo.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Repo not found")
    return repo


async def _refresh_deployment_status(
    dep: Deployment,
    *,
    token: str,
    session: AsyncSession,
) -> None:
    """Lazily poll DO and update the Deployment row in-place.

    Only called when the deployment is not already in a terminal state.
    """
    ref_data = dep.provider_ref or {}
    app_id = ref_data.get("app_id")
    dep_id = ref_data.get("deployment_id")
    if not app_id:
        return

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as http:
            app = await do_client.get_app(app_id, token=token, client=http)
            if dep_id:
                do_dep = await do_client.get_deployment(app_id, dep_id, token=token, client=http)
            else:
                do_dep = await do_client.get_latest_deployment(app_id, token=token, client=http)
    except do_client.DigitalOceanAPIError as exc:
        logger.warning("deploy status poll failed for %s: %s", dep.id, exc)
        dep.status = DS.FAILED
        dep.error_message = str(exc)
        dep.finished_at = datetime.now(timezone.utc)
        dep.updated_at = datetime.now(timezone.utc)
        await session.flush()
        return

    phase = do_client.deployment_phase(do_dep) if do_dep else "UNKNOWN"
    dep.status_detail = phase

    if do_client.is_failed(phase):
        dep.status = DS.FAILED
        dep.finished_at = dep.finished_at or datetime.now(timezone.utc)
        cause = (do_dep or {}).get("cause") or (do_dep or {}).get("progress") or {}
        if isinstance(cause, dict):
            dep.error_message = cause.get("error_steps") or cause.get("reason")
        elif isinstance(cause, str):
            dep.error_message = cause
    elif phase == do_client.PHASE_ACTIVE:
        dep.status = DS.ACTIVE
        dep.live_url = do_client.app_live_url(app)
        dep.finished_at = dep.finished_at or datetime.now(timezone.utc)
        # Health check
        if dep.live_url:
            dep.healthy = await probe(dep.live_url, health_check_path(dep.plan))
    else:
        dep.status = DS.DEPLOYING

    dep.updated_at = datetime.now(timezone.utc)
    await session.flush()


async def _submit_to_digitalocean(
    *,
    session: AsyncSession,
    dep: Deployment,
    repo: WorkspaceRepo,
    token: str,
    deploy_plan: DeployPlan,
) -> None:
    existing_app_id: str | None = None
    prior = (
        await session.execute(
            select(Deployment)
            .where(
                Deployment.workspace_id == dep.workspace_id,
                Deployment.repo_id == dep.repo_id,
                Deployment.provider == "digitalocean",
                Deployment.id != dep.id,
            )
            .order_by(Deployment.created_at.desc())
        )
    ).scalars().all()
    for d in prior:
        aid = (d.provider_ref or {}).get("app_id")
        if aid:
            existing_app_id = aid
            break

    dep.plan = deploy_plan.model_dump()
    dep.status = DS.DEPLOYING
    dep.updated_at = datetime.now(timezone.utc)
    await session.flush()

    clone_url = repo.html_url + ".git" if repo.html_url else f"https://github.com/{repo.full_name}.git"
    adapter = DigitalOceanAppPlatform(
        token=token,
        full_name=repo.full_name,
        private=repo.private,
    )
    try:
        provider_ref = await adapter.apply(
            deploy_plan,
            repo_clone_url=clone_url,
            branch=repo.default_branch,
            existing_app_id=existing_app_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("DO app creation failed for deployment %s: %s", dep.id, exc)
        raw = str(exc)
        # A private repo failing at submit almost always means DO's GitHub
        # app isn't authorized on it — translate the cryptic provider error
        # into actionable guidance the console can turn into recovery
        # buttons (see _GITHUB_ACCESS_HINT / error_kind).
        if repo.private and _looks_like_github_access_error(raw):
            dep.error_message = _GITHUB_ACCESS_HINT
        else:
            dep.error_message = raw
        dep.status = DS.FAILED
        dep.finished_at = datetime.now(timezone.utc)
        dep.updated_at = datetime.now(timezone.utc)
        await _event(
            session, dep, DeploymentEventKind.DEPLOY_FAILED,
            "Deploy failed while creating the app on DigitalOcean.",
        )
        await session.flush()
        return

    dep.provider_ref = {
        "provider": provider_ref.provider,
        "app_id": provider_ref.app_id,
        "deployment_id": provider_ref.deployment_id,
        **provider_ref.extra,
    }
    dep.updated_at = datetime.now(timezone.utc)
    await _event(
        session, dep, DeploymentEventKind.DEPLOYED,
        deploy_plan.summary or "Deployment started on DigitalOcean.",
    )
    await session.flush()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/workspaces/{workspace_id}/repos/{repo_id}/deploy",
    response_model=ApiDeploymentOut,
    status_code=202,
)
async def trigger_deploy(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    body: TriggerDeployIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ApiDeploymentOut:
    """Plan + submit a deployment. Returns immediately with status=deploying."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    repo = await _require_repo(session, workspace_id, repo_id)

    # Check DO token exists
    token = await get_do_token(session, workspace_id)
    if not token:
        raise HTTPException(
            status_code=409,
            detail="DigitalOcean is not connected for this workspace. "
            "Connect it first via the Integrations page.",
        )

    # Build gateway for planning
    install = await _load_install(session, repo)
    if install is None:
        raise HTTPException(
            status_code=409,
            detail="Repo has no GitHub installation; cannot read files for planning.",
        )

    # Create Deployment row in PLANNING state
    dep = Deployment(
        workspace_id=workspace_id,
        repo_id=repo_id,
        provider="digitalocean",
        status=DS.PLANNING,
        started_at=datetime.now(timezone.utc),
    )
    session.add(dep)
    await session.flush()

    # Effective planner provider/model. Resolution: explicit modal choice
    # → persisted per-repo preference (set in the wizard / last deploy) →
    # backend default. We also persist a fresh modal choice back onto the
    # repo so the next deploy starts from it ("change in modal sticks").
    eff_provider = (
        (body.llm_provider if body else None)
        or repo.deploy_planner_provider
        or ""
    )
    eff_model = (
        (body.llm_model if body else None)
        or repo.deploy_planner_model
        or ""
    )
    if body and (body.llm_provider or body.llm_model):
        repo.deploy_planner_provider = eff_provider or None
        repo.deploy_planner_model = eff_model or None
        repo.updated_at = datetime.now(timezone.utc)
        await session.flush()

    llm_credentials = None
    if body and body.llm_api_key and eff_provider:
        llm_credentials = PlannerLLMCredentials(
            provider=eff_provider,
            api_key=body.llm_api_key,
            model=eff_model or None,
        )

    if llm_credentials is None:
        try:
            files = await list_repo_workflows(repo, install, settings=settings)
        except WorkflowDispatchError as exc:
            # Ship's GitHub App can't read Actions on this repo (commonly a
            # 403 "Resource not accessible by integration" — the App lacks
            # the Actions permission). The Actions path is impossible here;
            # point the operator at the two real fixes rather than leaking
            # the raw GitHub error.
            logger.info(
                "deploy: list workflows failed for %s (%s)",
                repo.full_name,
                exc.status_code,
            )
            dep.status = DS.FAILED
            dep.error_message = (
                "Ship's GitHub App can't access GitHub Actions on this repo "
                f"(HTTP {exc.status_code}), so it can't run the deploy "
                "planner workflow. Fix: deploy with a manual LLM key (plans "
                "without GitHub Actions), or grant Ship's GitHub App the "
                "Actions permission on this repo and retry."
            )
            dep.finished_at = datetime.now(timezone.utc)
            dep.updated_at = datetime.now(timezone.utc)
            await _event(
                session,
                dep,
                DeploymentEventKind.DEPLOY_FAILED,
                "Ship's GitHub App lacks Actions access on this repo.",
            )
            await session.flush()
            return _to_out(dep)
        if _DEPLOY_PLAN_WORKFLOW in files:
            planner_token = _mint_deploy_token(dep.id, settings)
            dep.provider_ref = {"planner_token_hash": _hash_token(planner_token)}
            await session.flush()
            # Resolve the model the Actions planner should use. When the
            # operator didn't pick one (modal or saved preference), pin the
            # backend's current default for the provider rather than leaving
            # the workflow to fall back to its own hard-coded default — that
            # way bumping the default is a backend redeploy, not a per-repo
            # re-seed of the workflow file.
            planner_provider = eff_provider
            planner_model = eff_model
            if not planner_model and planner_provider:
                planner_model = PROVIDER_DEFAULT_MODEL.get(planner_provider, "")
            try:
                await dispatch_workflow(
                    repo,
                    install,
                    _DEPLOY_PLAN_WORKFLOW,
                    inputs={
                        "ship_deployment_id": str(dep.id),
                        "ship_callback_url": (
                            f"{settings.public_url.rstrip('/')}/v1/deployments/{dep.id}/plan-result"
                        ),
                        "ship_deploy_token": planner_token,
                        "repo_private": "true" if repo.private else "false",
                        "default_branch": repo.default_branch or "main",
                        "planner_provider": planner_provider,
                        "planner_model": planner_model,
                    },
                    settings=settings,
                )
            except WorkflowDispatchError as exc:
                dep.status = DS.FAILED
                dep.error_message = f"GitHub dispatch failed ({exc.status_code}): {exc.message[:256]}"
                dep.finished_at = datetime.now(timezone.utc)
                dep.updated_at = datetime.now(timezone.utc)
                await _event(
                    session, dep, DeploymentEventKind.DEPLOY_FAILED,
                    "Could not start the deploy planner workflow.",
                )
                await session.flush()
            return _to_out(dep)
        dep.status = DS.FAILED
        dep.error_message = (
            "Deploy planner workflow is not installed in this repo. "
            "Update the Ship seed bundle so .github/workflows/ship-deploy-plan.yml exists."
        )
        dep.finished_at = datetime.now(timezone.utc)
        dep.updated_at = datetime.now(timezone.utc)
        await _event(
            session, dep, DeploymentEventKind.DEPLOY_FAILED,
            "Deploy planner workflow is not installed in this repo.",
        )
        await session.flush()
        return _to_out(dep)

    owner, _, name = (repo.full_name or "").partition("/")
    repo_ref = RepoRef(kind="github", owner=owner, repo=name)
    gw = GitHubCodeHost(install.installation_id, settings=settings)
    intel: RepoIntel | None = await get_current_intel(session, repo_id)
    try:
        deploy_plan: DeployPlan = await plan_deployment(
            gateway=gw,
            repo_ref=repo_ref,
            private=repo.private,
            default_branch=repo.default_branch,
            intel=intel,
            settings=settings,
            llm_credentials=llm_credentials,
        )
    except DeployPlanningError as exc:
        dep.status = DS.FAILED
        dep.error_message = str(exc)
        dep.finished_at = datetime.now(timezone.utc)
        dep.updated_at = datetime.now(timezone.utc)
        await _event(
            session, dep, DeploymentEventKind.DEPLOY_FAILED,
            "Could not analyze the repo for deployment.",
        )
        await session.flush()
        return _to_out(dep)

    await _submit_to_digitalocean(
        session=session,
        dep=dep,
        repo=repo,
        token=token,
        deploy_plan=deploy_plan,
    )
    return _to_out(dep)


@router.get(
    "/workspaces/{workspace_id}/repos/{repo_id}/deploy/planner-options",
    response_model=DeployPlannerOptionsOut,
)
async def get_deploy_planner_options(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DeployPlannerOptionsOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    repo = await _require_repo(session, workspace_id, repo_id)
    install = await _load_install(session, repo)
    if install is None:
        return DeployPlannerOptionsOut(
            providers=[],
            missing=list(_PLANNER_SECRET_BY_PROVIDER),
        )
    try:
        names = set(await list_github_repo_secrets(repo, install, settings=settings))
    except WorkflowDispatchError as exc:
        # Listing Actions secrets can 404/403 (Actions disabled on the repo,
        # App missing the Secrets permission, token revoked). That's not a
        # server error — it just means we can't see any planner keys. Degrade
        # to "no providers" so the wizard shows "add a key" instead of a 500.
        logger.info(
            "planner-options: secrets listing failed for %s (%s) — "
            "treating as no keys",
            repo.full_name,
            exc.status_code,
        )
        return DeployPlannerOptionsOut(
            providers=[],
            missing=list(_PLANNER_SECRET_BY_PROVIDER),
        )
    providers = [
        provider
        for provider, secret_name in _PLANNER_SECRET_BY_PROVIDER.items()
        if secret_name in names
    ]
    return DeployPlannerOptionsOut(
        providers=providers,
        missing=[
            provider
            for provider in _PLANNER_SECRET_BY_PROVIDER
            if provider not in providers
        ],
    )


@router.post(
    "/workspaces/{workspace_id}/repos/{repo_id}/deploy/planner-models",
    response_model=DeployPlannerModelsOut,
)
async def get_deploy_planner_models(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    body: DeployPlannerModelsIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DeployPlannerModelsOut:
    """List selectable planner models for a provider.

    Backs the model dropdown in the "New deployment" modal. Calls the
    provider's list-models API with the pasted ``api_key`` (preferred)
    or the backend-configured env key; on a missing key or upstream
    failure it returns a curated fallback list with ``source="fallback"``
    (never errors), so the picker always has options. POST (not GET) so
    the optional key rides in the body, never a logged URL. ``repo_id``
    is required only for the membership/ownership check — the listing
    itself is provider-global.
    """

    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _require_repo(session, workspace_id, repo_id)
    listing = await list_planner_models(
        body.provider, api_key=body.api_key, settings=settings
    )
    return DeployPlannerModelsOut(
        provider=listing.provider,
        models=listing.models,
        default_model=listing.default_model,
        source=listing.source,
        error=listing.error,
    )


@router.put(
    "/workspaces/{workspace_id}/repos/{repo_id}/deploy/planner-pref",
    response_model=DeployPlannerPrefOut,
)
async def set_deploy_planner_pref(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    body: DeployPlannerPrefIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> DeployPlannerPrefOut:
    """Persist the repo's deploy-planner preference (provider + model).

    Set from the onboarding wizard and re-set from the New deployment
    modal whenever the operator changes the model, so the next deploy
    starts from their last choice. Either field may be null to clear.
    """

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    repo = await _require_repo(session, workspace_id, repo_id)
    provider = (body.provider or "").strip().lower() or None
    if provider is not None and provider not in _PLANNER_SECRET_BY_PROVIDER:
        raise HTTPException(status_code=400, detail="Unknown planner provider.")
    repo.deploy_planner_provider = provider
    repo.deploy_planner_model = (body.model or "").strip() or None
    repo.updated_at = datetime.now(timezone.utc)
    await session.flush()
    return DeployPlannerPrefOut(
        provider=repo.deploy_planner_provider,
        model=repo.deploy_planner_model,
    )


@router.post("/deployments/{deployment_id}/plan-result", response_model=ApiDeploymentOut)
async def report_deploy_plan_result(
    deployment_id: uuid.UUID = Path(...),
    payload: DeployPlanResultIn = ...,
    authorization: str | None = Header(default=None, alias="Authorization"),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ApiDeploymentOut:
    dep = await session.get(Deployment, deployment_id)
    if dep is None:
        raise HTTPException(status_code=404, detail="Deployment not found")
    _verify_deploy_token(
        raw_token=_parse_bearer(authorization),
        deployment_id=deployment_id,
        dep=dep,
        settings=settings,
    )
    if dep.status in DS.TERMINAL or dep.status == DS.DEPLOYING:
        return _to_out(dep)
    if payload.status != "succeeded" or payload.plan is None:
        dep.status = DS.FAILED
        dep.error_message = payload.error or "Deploy planner workflow failed."
        dep.finished_at = datetime.now(timezone.utc)
        dep.updated_at = datetime.now(timezone.utc)
        await _event(
            session, dep, DeploymentEventKind.DEPLOY_FAILED,
            "Could not analyze the repo for deployment.",
        )
        await session.flush()
        return _to_out(dep)

    repo = await session.get(WorkspaceRepo, dep.repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repo not found")
    token = await get_do_token(session, dep.workspace_id)
    if not token:
        dep.status = DS.FAILED
        dep.error_message = "DigitalOcean is no longer connected for this workspace."
        dep.finished_at = datetime.now(timezone.utc)
        dep.updated_at = datetime.now(timezone.utc)
        await session.flush()
        return _to_out(dep)

    await _submit_to_digitalocean(
        session=session,
        dep=dep,
        repo=repo,
        token=token,
        deploy_plan=payload.plan,
    )
    return _to_out(dep)


@router.get(
    "/workspaces/{workspace_id}/deployments/{deployment_id}",
    response_model=ApiDeploymentOut,
)
async def get_deployment(
    workspace_id: uuid.UUID,
    deployment_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ApiDeploymentOut:
    """Get deployment status, lazily polling DO if still in-flight."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    dep = await session.get(Deployment, deployment_id)
    if dep is None or dep.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Deployment not found")

    if dep.status not in DS.TERMINAL:
        token = await get_do_token(session, workspace_id)
        if token:
            await _refresh_deployment_status(dep, token=token, session=session)
    elif dep.status == DS.ACTIVE and dep.live_url:
        # On-demand health re-check for a live app (health can change after
        # it first went ACTIVE; terminal rows aren't re-polled otherwise).
        healthy = await probe(dep.live_url, health_check_path(dep.plan))
        if healthy != dep.healthy:
            dep.healthy = healthy
            dep.updated_at = datetime.now(timezone.utc)
            await session.flush()

    names = await _repo_name_map(session, [dep.repo_id])
    return _to_out(dep, repo_full_name=names.get(dep.repo_id))


@router.get(
    "/workspaces/{workspace_id}/deployments",
    response_model=list[ApiDeploymentOut],
)
async def list_workspace_deployments(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ApiDeploymentOut]:
    """List all recent deployments for the workspace (newest first, max 50)."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    rows = (
        await session.execute(
            select(Deployment)
            .where(Deployment.workspace_id == workspace_id)
            .order_by(Deployment.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    names = await _repo_name_map(session, [r.repo_id for r in rows])
    return [_to_out(r, repo_full_name=names.get(r.repo_id)) for r in rows]


@router.get(
    "/workspaces/{workspace_id}/repos/{repo_id}/deployments",
    response_model=list[ApiDeploymentOut],
)
async def list_repo_deployments(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ApiDeploymentOut]:
    """List recent deployments for a repo (newest first, max 20)."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    repo = await _require_repo(session, workspace_id, repo_id)
    rows = (
        await session.execute(
            select(Deployment)
            .where(
                Deployment.workspace_id == workspace_id,
                Deployment.repo_id == repo_id,
            )
            .order_by(Deployment.created_at.desc())
            .limit(20)
        )
    ).scalars().all()
    return [_to_out(r, repo_full_name=repo.full_name) for r in rows]


class ApiDeployEvent(BaseModel):
    kind: str
    message: str
    created_at: str


@router.get(
    "/workspaces/{workspace_id}/repos/{repo_id}/deploy/events",
    response_model=list[ApiDeployEvent],
)
async def list_app_activity(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    provider: str = "digitalocean",
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ApiDeployEvent]:
    """Newest-first activity feed for one app (deployed / failed / removed)."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    await _require_repo(session, workspace_id, repo_id)
    events = await list_app_events(
        session, workspace_id=workspace_id, repo_id=repo_id, provider=provider
    )
    return [
        ApiDeployEvent(
            kind=e.kind, message=e.message, created_at=e.created_at.isoformat()
        )
        for e in events
    ]


@router.delete("/workspaces/{workspace_id}/repos/{repo_id}/deploy")
async def teardown_deploy(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Tear down an app: really delete it from DigitalOcean (stops billing)
    and remove its deployment rows. Idempotent — a DO 404 means it's already
    gone. Admin only.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    await _require_repo(session, workspace_id, repo_id)

    result = await teardown_repo_app(session, workspace_id, repo_id, delete_rows=True)
    if not result.ok:
        # DO delete failed — keep our rows (so we can retry) and surface it,
        # rather than silently orphaning a still-billing app.
        raise HTTPException(
            status_code=502,
            detail="Could not delete the app from DigitalOcean — try again.",
        )
    return {
        "ok": True,
        "deleted_app_ids": result.deleted_app_ids,
        "removed": result.rows_removed,
    }


# ---------------------------------------------------------------------------
# Provider readiness — drives the "Connect DigitalOcean" gate in the UI
# ---------------------------------------------------------------------------


class ApiDeployProvider(BaseModel):
    provider: str
    label: str
    connected: bool
    # Path the console POSTs to start the OAuth connect (relative to /v1).
    connect_start_path: str | None


@router.get(
    "/workspaces/{workspace_id}/deploy/providers",
    response_model=list[ApiDeployProvider],
)
async def list_deploy_providers(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ApiDeployProvider]:
    """Return deploy providers and whether each is connected for this workspace.

    Today only DigitalOcean. ``connected`` reflects a READY native
    installation with a decryptable access token. The console uses this to
    decide whether to show "Deploy" or the "Connect DigitalOcean" gate.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    do_token = await get_do_token(session, workspace_id)
    return [
        ApiDeployProvider(
            provider="digitalocean",
            label="DigitalOcean",
            connected=bool(do_token),
            connect_start_path=(
                f"/integrations/digitalocean/install/start?workspace_id={workspace_id}"
            ),
        )
    ]


__all__ = ["router"]
