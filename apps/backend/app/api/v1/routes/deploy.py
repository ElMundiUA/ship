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
from typing import Any

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
from backend.app.services.deploy.health import (
    health_check_path,
    probe,
    probe_with_grace,
)
from backend.app.services.deploy.logs import LOG_TYPES, fetch_deploy_logs
from backend.app.services.deploy.teardown import teardown_repo_app
from backend.app.db.models.deploy import DeploymentEventKind
from backend.app.services.deploy.plan import DeployConnection, DeployPlan
from backend.app.services.deploy.llm import PlannerLLMCredentials
from backend.app.services.deploy.model_catalog import (
    PROVIDER_DEFAULT_MODEL,
    list_planner_models,
)
from backend.app.services.deploy.planner import DeployPlanningError, plan_deployment
from backend.app.services.deploy.providers.digitalocean import (
    DigitalOceanAppPlatform,
)
from backend.app.services.deploy.providers.base import ProviderRef
from backend.app.services.deploy.providers.capabilities import can_native_rollback
from backend.app.services.deploy.providers.operations import (
    ProviderOperationUnsupported,
    get_provider_token,
    rollback_provider_deployment,
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
# Friendly error translation
# ---------------------------------------------------------------------------
# Deploys fail for a handful of recurring, recognisable reasons. Each gets a
# canonical plain-language message (so a non-dev knows what to do) plus an
# ``error_kind`` the console can branch on for tailored recovery UI. We don't
# add a DB column for the kind — instead ``error_kind`` is derived from the
# stored ``error_message`` by matching a stable lead substring. So the rule is:
# whatever we store at a failure site MUST contain the matching lead below.
_ACTIONS_ACCESS_KIND = "actions_access"
_ACTIONS_ACCESS_LEAD = "can't access GitHub Actions on this repo"

_WORKFLOW_UNREGISTERED_KIND = "workflow_unregistered"
_WORKFLOW_UNREGISTERED_LEAD = "hasn't registered the deploy planner workflow"
_WORKFLOW_UNREGISTERED_HINT = (
    "GitHub hasn't registered the deploy planner workflow on this repo yet, so "
    "it can't be triggered. Fix: push any commit to the default branch to "
    "register .github/workflows/ship-deploy-plan.yml (a one-time GitHub "
    "requirement for workflow_dispatch), or deploy with a manual LLM key "
    "(plans on the backend, no GitHub Actions needed)."
)

_WORKFLOW_MISSING_KIND = "workflow_missing"
_WORKFLOW_MISSING_LEAD = "Deploy planner workflow is not installed"

_BUILD_FAILED_KIND = "build_failed"
_BUILD_FAILED_LEAD = "failed to build on DigitalOcean"
_BUILD_FAILED_HINT = (
    "The app failed to build on DigitalOcean. This is almost always a problem "
    "in the repo's own build (a wrong Dockerfile path, a missing dependency, "
    "or a failing build command) rather than Ship/DO config. Open the build "
    "logs for the failing step."
)

_HEALTH_FAILED_KIND = "health_check_failed"
_HEALTH_FAILED_LEAD = "didn't pass DigitalOcean's health checks"
_HEALTH_FAILED_HINT = (
    "The container started but didn't pass DigitalOcean's health checks, so "
    "the deploy was rolled back. Usual causes: the app isn't listening on "
    "0.0.0.0 or on the expected port, or the health path returns an error. "
    "Check the runtime logs."
)

# DigitalOcean's GitHub integration caches a branch's tip commit and does NOT
# re-resolve it on a Ship-triggered redeploy. A force-push / amend rewrites the
# branch, leaving DO's cache pointing at a rewritten commit that the builder's
# shallow clone can't find ("error checking out commit: object not found").
# A plain Redeploy is useless here (DO keeps the stale tip); only a fresh push
# event refreshes DO. So we tell the user exactly that — push, don't retry.
_GIT_REF_STALE_KIND = "git_ref_stale"
_GIT_REF_STALE_LEAD = "couldn't find the commit to deploy"
_GIT_REF_STALE_HINT = (
    "DigitalOcean couldn't find the commit to deploy — its GitHub integration "
    "is stuck on a commit that was rewritten (a force-push / amend on the "
    "branch). A plain Redeploy won't fix this (DigitalOcean keeps the stale "
    "commit). Push a NEW commit to the branch with a normal push (not "
    "--force), then redeploy — that refreshes DigitalOcean. Tip: avoid "
    "force-pushing the deploy branch."
)
# Build-log markers that mean DO tried a commit it can't check out.
_GIT_REF_STALE_MARKERS = (
    "error checking out commit",
    "object not found",
    "couldn't find remote ref",
    "reference is not a tree",
    "failed to checkout",
)

# (lead substring, error_kind) — checked in order; first hit wins.
_ERROR_KIND_MARKERS: tuple[tuple[str, str], ...] = (
    (_ACTIONS_ACCESS_LEAD, _ACTIONS_ACCESS_KIND),
    (_WORKFLOW_UNREGISTERED_LEAD, _WORKFLOW_UNREGISTERED_KIND),
    (_WORKFLOW_MISSING_LEAD, _WORKFLOW_MISSING_KIND),
    (_GIT_REF_STALE_LEAD, _GIT_REF_STALE_KIND),
    (_BUILD_FAILED_LEAD, _BUILD_FAILED_KIND),
    (_HEALTH_FAILED_LEAD, _HEALTH_FAILED_KIND),
)


def _actions_access_hint(status_code: int) -> str:
    """Ship's GitHub App lacks the Actions permission on this repo."""
    return (
        f"Ship's GitHub App can't access GitHub Actions on this repo "
        f"(HTTP {status_code}), so it can't run the deploy planner workflow. "
        "Fix: deploy with a manual LLM key (plans without GitHub Actions), or "
        "grant Ship's GitHub App the Actions permission on this repo and retry."
    )


def _classify_do_failure(raw: str) -> str:
    """Turn DigitalOcean's raw build/deploy failure text into a friendly,
    actionable message (preserving DO's detail). Unknown shapes pass through."""
    low = (raw or "").lower()
    if any(m in low for m in ("health check", "health-check", "readiness", "did not become healthy", "failed to become healthy")):
        return f"{_HEALTH_FAILED_HINT}\n\nDigitalOcean said: {raw}"
    if "build" in low and any(m in low for m in ("fail", "error", "exit", "non-zero", "unable")):
        return f"{_BUILD_FAILED_HINT}\n\nDigitalOcean said: {raw}"
    return raw


def _structured_failure_reason(do_dep: dict | None) -> str | None:
    """Best human reason from a DO deployment's progress steps (e.g.
    'BuildJobExitNonZero: ...'), falling back to the trigger ``cause``. The
    structured steps are far more useful than ``cause`` ('app spec updated')."""
    msgs: list[str] = []

    def walk(steps: Any) -> None:
        for st in steps or []:
            r = st.get("reason") if isinstance(st, dict) else None
            if isinstance(r, dict) and (r.get("message") or r.get("code")):
                msgs.append(
                    f"{r.get('code', '')}: {r.get('message', '')}".strip(": ").strip()
                )
            if isinstance(st, dict):
                walk(st.get("steps"))

    walk(((do_dep or {}).get("progress") or {}).get("steps"))
    if msgs:
        return " | ".join(dict.fromkeys(msgs))  # dedupe, preserve order
    cause = (do_dep or {}).get("cause")
    return cause if isinstance(cause, str) else None


async def _do_failure_message(
    do_dep: dict | None,
    *,
    app_id: str | None,
    dep_id: str | None,
    token: str,
) -> str | None:
    """Friendly message for a FAILED DO deployment. Scans the build log for a
    stale-commit checkout failure (force-push) first — that needs the log, the
    structured fields only say a generic 'build job failed'. Otherwise uses the
    best structured reason, friendly-classified."""
    if app_id and dep_id:
        try:
            text, _ = await fetch_deploy_logs(
                app_id, dep_id, log_type="BUILD", token=token
            )
            low = (text or "").lower()
            if any(m in low for m in _GIT_REF_STALE_MARKERS):
                return _GIT_REF_STALE_HINT
        except Exception as exc:  # noqa: BLE001 — log scan is best-effort
            logger.info("failure log scan failed for %s: %s", dep_id, exc)
    raw = _structured_failure_reason(do_dep)
    return _classify_do_failure(str(raw)) if raw else None


def _error_kind_for(msg: str | None) -> str | None:
    """Derive ``error_kind`` from a stored ``error_message`` (see note above)."""
    if not msg:
        return None
    if msg == _GITHUB_ACCESS_HINT:
        return _GITHUB_ACCESS_ERROR_KIND
    for lead, kind in _ERROR_KIND_MARKERS:
        if lead in msg:
            return kind
    return None


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class ApiDeployEnvOut(BaseModel):
    key: str
    # Non-secret config values (e.g. HOST=0.0.0.0, VITE_API_URL=$APP_URL) are
    # shown; secret values are always masked to None — we never echo secrets.
    value: str | None
    secret: bool
    required: bool


class ApiDeploymentEnvSettingOut(BaseModel):
    key: str
    value: str | None = None
    secret: bool = False
    set: bool = False


class ApiDeployComponentOut(BaseModel):
    """One component of the planner's DeployPlan, for the console breakdown."""

    name: str
    kind: str
    runtime: str | None = None
    source_dir: str | None = None
    http_port: int | None = None
    routes: list[str] = []
    health_check_path: str | None = None
    dockerfile_path: str | None = None
    env: list[ApiDeployEnvOut] = []


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
    version: int | None = None
    redeployed_from_id: str | None = None
    redeployed_from_version: int | None = None
    rolled_back_from_id: str | None = None
    rolled_back_from_version: int | None = None
    can_provider_rollback: bool = False
    # Git commit this version shipped (pinned at deploy time, best-effort).
    commit_sha: str | None = None
    commit_message: str | None = None
    commit_author: str | None = None
    committed_at: str | None = None
    # Full stored planner output for operator/debug visibility. Secret env
    # values are masked before returning.
    plan_debug: dict[str, Any] | None = None
    # Per-component breakdown from the stored DeployPlan, so the console can
    # show what the planner decided (name · kind · source_dir · runtime + env).
    plan_components: list[ApiDeployComponentOut] = []
    # DigitalOcean's OWN monthly cost estimate (USD) for this deploy's spec,
    # captured at submit via /v2/apps/propose. None if DO didn't return one or
    # propose wasn't reached. We show DO's figure as-is (approximate), never a
    # number we computed ourselves.
    estimated_monthly_usd: float | None = None
    operator_env: list[ApiDeploymentEnvSettingOut] = []


class OperatorEnvIn(BaseModel):
    key: str
    value: str = ""
    secret: bool = False


class TriggerDeployIn(BaseModel):
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None
    env: list[OperatorEnvIn] = []


class RedeployIn(BaseModel):
    env: list[OperatorEnvIn] = []


def _operator_env_value(key: str, value: str) -> str:
    key = key.strip()
    raw = value.strip()
    prefix = f"{key}="
    if key and raw.startswith(prefix):
        return raw[len(prefix) :].strip()
    return value


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


def _plan_components_out(plan: dict | None) -> list[ApiDeployComponentOut]:
    """Project the stored DeployPlan's components for the console, masking
    any secret env values (we never echo secrets back to the UI)."""
    out: list[ApiDeployComponentOut] = []
    for c in (plan or {}).get("components", []) or []:
        if not isinstance(c, dict):
            continue
        envs: list[ApiDeployEnvOut] = []
        for e in c.get("env") or []:
            if not isinstance(e, dict):
                continue
            is_secret = bool(e.get("secret"))
            envs.append(
                ApiDeployEnvOut(
                    key=str(e.get("key") or ""),
                    value=None if is_secret else (e.get("value") or None),
                    secret=is_secret,
                    required=bool(e.get("required")),
                )
            )
        out.append(
            ApiDeployComponentOut(
                name=str(c.get("name") or ""),
                kind=str(c.get("kind") or ""),
                runtime=c.get("runtime"),
                source_dir=c.get("source_dir"),
                http_port=c.get("http_port"),
                routes=[str(r) for r in (c.get("routes") or [])],
                health_check_path=c.get("health_check_path"),
                dockerfile_path=c.get("dockerfile_path"),
                env=envs,
            )
        )
    return out


def _plan_debug_out(plan: dict | None) -> dict[str, Any] | None:
    """Return the stored DeployPlan with secret env values masked."""
    if not isinstance(plan, dict):
        return None
    out: dict[str, Any] = dict(plan)
    components: list[dict[str, Any]] = []
    for c in plan.get("components") or []:
        if not isinstance(c, dict):
            continue
        comp = dict(c)
        envs: list[dict[str, Any]] = []
        for e in c.get("env") or []:
            if not isinstance(e, dict):
                continue
            env = dict(e)
            if bool(env.get("secret")):
                env["value"] = None
            envs.append(env)
        comp["env"] = envs
        components.append(comp)
    out["components"] = components
    return out


_FRONTEND_API_ENV_MARKERS = ("api", "backend", "server", "base", "url")


def _frontend_api_route(plan: DeployPlan) -> str | None:
    """Pick the public route prefix a browser frontend should use for the API."""
    for c in plan.components:
        if c.kind != "service":
            continue
        routes = [str(r) for r in (c.routes or []) if r and r != "/"]
        if not routes:
            continue
        health = (c.health_check_path or "").rstrip("/")
        for route in routes:
            if route.rstrip("/") != health:
                return route.rstrip("/")
        return routes[0].rstrip("/")
    return None


def _connection_target_value(conn: DeployConnection) -> str:
    if conn.value and conn.value.strip():
        return conn.value.strip().rstrip("/")
    path = "/" + conn.public_base_path.strip("/")
    return "$APP_URL" if path == "/" else "$APP_URL" + path


def _find_component(plan: DeployPlan, name: str):
    return next((c for c in plan.components if c.name == name), None)


def _infer_frontend_api_env_key(component) -> str | None:
    for ev in component.env or []:
        key = ev.key.lower()
        if any(marker in key for marker in _FRONTEND_API_ENV_MARKERS):
            return ev.key
    return None


def _normalize_do_frontend_api_base(plan: DeployPlan) -> DeployPlan:
    """Repair frontend API-base envs for DO route-prefix routing.

    The Actions planner can return "$APP_URL" for a frontend's backend base.
    On DigitalOcean, a backend routed at "/api" is publicly reached at
    "$APP_URL/api", while DO strips "/api" before forwarding to the service.
    Normalize this before storing the plan and before building the DO spec so
    both backend-planned and Actions-planned deploys behave the same.
    """
    out = plan.model_copy(deep=True)
    connections = list(out.connections or [])
    if not connections:
        route = _frontend_api_route(out)
        if route:
            frontend = next((c for c in out.components if c.kind == "static_site"), None)
            backend = next((c for c in out.components if c.kind == "service"), None)
            if frontend and backend:
                env_key = _infer_frontend_api_env_key(frontend)
                connections.append(
                    DeployConnection(
                        from_component=frontend.name,
                        to_component=backend.name,
                        env_key=env_key,
                        public_base_path=route,
                        value="$APP_URL" + route,
                    )
                )
                out.connections = connections
    if not connections:
        return plan
    changed = False
    for conn in connections:
        frontend = _find_component(out, conn.from_component)
        if not frontend or frontend.kind != "static_site" or not conn.env_key:
            continue
        target = _connection_target_value(conn)
        for ev in frontend.env or []:
            if ev.key != conn.env_key:
                continue
            if ev.secret:
                continue
            current = (ev.value or "").strip().rstrip("/")
            if current == "" or current.startswith("$APP_URL"):
                ev.value = target
                conn.value = target
                changed = True
    return out if changed or out.connections else plan


async def _deployment_version_map(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    provider: str,
) -> dict[uuid.UUID, int]:
    rows = (
        await session.execute(
            select(Deployment.id)
            .where(
                Deployment.workspace_id == workspace_id,
                Deployment.repo_id == repo_id,
                Deployment.provider == provider,
            )
            .order_by(Deployment.created_at.asc())
        )
    ).scalars().all()
    return {dep_id: i + 1 for i, dep_id in enumerate(rows)}


async def _to_out_async(
    session: AsyncSession,
    d: Deployment,
    *,
    repo_full_name: str | None = None,
) -> ApiDeploymentOut:
    versions = await _deployment_version_map(
        session,
        workspace_id=d.workspace_id,
        repo_id=d.repo_id,
        provider=d.provider,
    )
    out = _to_out(d, repo_full_name=repo_full_name)
    out.version = versions.get(d.id)
    source = (d.provider_ref or {}).get("redeployed_from_id")
    if isinstance(source, str):
        out.redeployed_from_id = source
        try:
            out.redeployed_from_version = versions.get(uuid.UUID(source))
        except ValueError:
            out.redeployed_from_version = None
    rollback_source = (d.provider_ref or {}).get("rolled_back_from_id")
    if isinstance(rollback_source, str):
        out.rolled_back_from_id = rollback_source
        try:
            out.rolled_back_from_version = versions.get(uuid.UUID(rollback_source))
        except ValueError:
            out.rolled_back_from_version = None
    return out


def _to_out(d: Deployment, *, repo_full_name: str | None = None) -> ApiDeploymentOut:
    plan_summary = None
    if d.plan:
        plan_summary = d.plan.get("summary")
    # DO's cost estimate rides along in provider_ref (set by the adapter at
    # submit). We expose ONLY this number, never the rest of provider_ref.
    estimated_monthly_usd = None
    if isinstance(d.provider_ref, dict):
        raw_cost = d.provider_ref.get("estimated_monthly_usd")
        if isinstance(raw_cost, (int, float)):
            estimated_monthly_usd = float(raw_cost)
    can_provider_rollback = can_native_rollback(
        provider=d.provider,
        status=d.status,
        provider_ref=d.provider_ref if isinstance(d.provider_ref, dict) else None,
    )
    commit = (
        d.provider_ref.get("commit") if isinstance(d.provider_ref, dict) else None
    )
    commit = commit if isinstance(commit, dict) else {}
    operator_env = []
    if isinstance(d.provider_ref, dict):
        for raw in d.provider_ref.get("operator_env") or []:
            if not isinstance(raw, dict) or not raw.get("key"):
                continue
            operator_env.append(
                ApiDeploymentEnvSettingOut(
                    key=str(raw["key"]),
                    value=None if raw.get("secret") else (raw.get("value") or None),
                    secret=bool(raw.get("secret")),
                    set=bool(raw.get("set") or raw.get("value")),
                )
            )
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
        error_kind=_error_kind_for(d.error_message),
        started_at=d.started_at.isoformat() if d.started_at else None,
        finished_at=d.finished_at.isoformat() if d.finished_at else None,
        created_at=d.created_at.isoformat(),
        updated_at=d.updated_at.isoformat(),
        plan_summary=plan_summary,
        redeployed_from_id=(
            str(d.provider_ref.get("redeployed_from_id"))
            if isinstance(d.provider_ref, dict)
            and d.provider_ref.get("redeployed_from_id")
            else None
        ),
        rolled_back_from_id=(
            str(d.provider_ref.get("rolled_back_from_id"))
            if isinstance(d.provider_ref, dict)
            and d.provider_ref.get("rolled_back_from_id")
            else None
        ),
        can_provider_rollback=can_provider_rollback,
        commit_sha=commit.get("sha"),
        commit_message=commit.get("message"),
        commit_author=commit.get("author_name"),
        committed_at=commit.get("committed_at"),
        plan_debug=_plan_debug_out(d.plan),
        plan_components=_plan_components_out(d.plan),
        estimated_monthly_usd=estimated_monthly_usd,
        operator_env=operator_env,
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
        dep.error_message = await _do_failure_message(
            do_dep, app_id=app_id, dep_id=dep_id, token=token
        )
    elif phase == do_client.PHASE_ACTIVE:
        dep.status = DS.ACTIVE
        dep.live_url = do_client.app_live_url(app)
        dep.finished_at = dep.finished_at or datetime.now(timezone.utc)
        # Health check — grace-aware: a just-active app may not be reachable
        # for a minute or two (DNS/TLS/cold start); don't flip to "failing"
        # during the grace window (report pending/None instead).
        if dep.live_url:
            dep.healthy = await probe_with_grace(
                dep.live_url, health_check_path(dep.plan), dep.finished_at
            )
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
    redeployed_from_id: uuid.UUID | None = None,
    operator_env: list[OperatorEnvIn] | None = None,
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
                Deployment.status != DS.DELETED,
            )
            .order_by(Deployment.created_at.desc())
        )
    ).scalars().all()
    for d in prior:
        aid = (d.provider_ref or {}).get("app_id")
        if aid:
            existing_app_id = aid
            break

    deploy_plan = _normalize_do_frontend_api_base(deploy_plan)
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
            operator_env=[
                {
                    "key": e.key.strip(),
                    "value": _operator_env_value(e.key, e.value),
                    "secret": e.secret,
                }
                for e in (operator_env or [])
                if e.key.strip()
            ],
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
    env_meta = [
        {
            "key": e.key.strip(),
            "value": None if e.secret else _operator_env_value(e.key, e.value),
            "secret": e.secret,
            "set": bool(e.value),
        }
        for e in (operator_env or [])
        if e.key.strip()
    ]
    if env_meta and "operator_env" not in dep.provider_ref:
        dep.provider_ref["operator_env"] = env_meta
    if redeployed_from_id is not None:
        dep.provider_ref["redeployed_from_id"] = str(redeployed_from_id)
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

    operator_env = [e for e in ((body.env if body else []) or []) if e.key.strip()]
    if operator_env:
        logger.info(
            "deploy: received operator env keys for %s: %s",
            repo.full_name,
            ", ".join(e.key.strip() for e in operator_env),
        )
    has_plaintext_secret_env = any(e.secret and e.value for e in operator_env)
    has_manual_planner_key = bool(body and body.llm_api_key and body.llm_provider)
    if has_plaintext_secret_env and not has_manual_planner_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "Secret env values can only be sent on a manual-key deploy or a "
                "rebuild, because the Actions planner path is asynchronous and "
                "Ship must not store plaintext secrets."
            ),
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
            dep.error_message = _actions_access_hint(exc.status_code)
            dep.finished_at = datetime.now(timezone.utc)
            dep.updated_at = datetime.now(timezone.utc)
            await _event(
                session,
                dep,
                DeploymentEventKind.DEPLOY_FAILED,
                "Ship's GitHub App lacks Actions access on this repo.",
            )
            await session.flush()
            return await _to_out_async(session, dep)
        if _DEPLOY_PLAN_WORKFLOW in files:
            planner_token = _mint_deploy_token(dep.id, settings)
            dep.provider_ref = {"planner_token_hash": _hash_token(planner_token)}
            pending_env = [
                {
                    "key": e.key.strip(),
                    "value": _operator_env_value(e.key, e.value),
                    "secret": False,
                }
                for e in operator_env
                if e.key.strip() and not e.secret
            ]
            if pending_env:
                dep.provider_ref["pending_operator_env"] = pending_env
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
                # A 422 on dispatch means GitHub hasn't registered the workflow
                # yet (workflow_dispatch only works once the file has landed on
                # the default branch) — the single most common dispatch failure.
                if exc.status_code == 422:
                    dep.error_message = _WORKFLOW_UNREGISTERED_HINT
                else:
                    dep.error_message = (
                        f"GitHub couldn't start the deploy planner workflow "
                        f"(HTTP {exc.status_code}): {exc.message[:256]}"
                    )
                dep.finished_at = datetime.now(timezone.utc)
                dep.updated_at = datetime.now(timezone.utc)
                await _event(
                    session, dep, DeploymentEventKind.DEPLOY_FAILED,
                    "Could not start the deploy planner workflow.",
                )
                await session.flush()
            return await _to_out_async(session, dep)
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
        return await _to_out_async(session, dep)

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
        return await _to_out_async(session, dep)

    # Pin this version to the branch HEAD commit (best-effort) so the Versions
    # tab shows what code each version shipped. Manual path only for now; the
    # Actions-planner path would capture it in the plan-result callback.
    commit = await gw.get_branch_commit(repo_ref, repo.default_branch or "main")

    await _submit_to_digitalocean(
        session=session,
        dep=dep,
        repo=repo,
        token=token,
        deploy_plan=deploy_plan,
        operator_env=operator_env,
    )
    if commit and commit.get("sha") and isinstance(dep.provider_ref, dict):
        dep.provider_ref = {**dep.provider_ref, "commit": commit}
        dep.updated_at = datetime.now(timezone.utc)
        await session.flush()
    return await _to_out_async(session, dep)


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
        return await _to_out_async(session, dep)
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
        return await _to_out_async(session, dep)

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
        return await _to_out_async(session, dep)

    await _submit_to_digitalocean(
        session=session,
        dep=dep,
        repo=repo,
        token=token,
        deploy_plan=payload.plan,
        operator_env=[
            OperatorEnvIn(
                key=str(e.get("key") or ""),
                value=str(e.get("value") or ""),
                secret=False,
            )
            for e in ((dep.provider_ref or {}).get("pending_operator_env") or [])
            if isinstance(e, dict)
        ],
    )
    return await _to_out_async(session, dep)


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
        healthy = await probe_with_grace(
            dep.live_url, health_check_path(dep.plan), dep.finished_at
        )
        if healthy != dep.healthy:
            dep.healthy = healthy
            dep.updated_at = datetime.now(timezone.utc)
            await session.flush()

    names = await _repo_name_map(session, [dep.repo_id])
    return await _to_out_async(session, dep, repo_full_name=names.get(dep.repo_id))


@router.post(
    "/workspaces/{workspace_id}/deployments/{deployment_id}/redeploy",
    response_model=ApiDeploymentOut,
)
async def redeploy_version(
    workspace_id: uuid.UUID,
    deployment_id: uuid.UUID,
    body: RedeployIn | None = None,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ApiDeploymentOut:
    """Rebuild a previous version by reusing its stored Ship plan.

    Unlike the wizard's "Redeploy" (which re-plans), this re-applies the exact
    plan that ``deployment_id`` captured — no LLM, deterministic — and submits
    it to the SAME DigitalOcean app (``_submit_to_digitalocean`` finds the prior
    app_id). This is a rebuild, not provider artifact rollback.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    src = await session.get(Deployment, deployment_id)
    if src is None or src.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Deployment not found")
    if not src.plan:
        raise HTTPException(
            status_code=400,
            detail="This version has no stored plan to redeploy.",
        )
    try:
        deploy_plan = DeployPlan(**src.plan)
    except Exception:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail="Stored plan for this version is invalid."
        )

    token = await get_do_token(session, workspace_id)
    if not token:
        raise HTTPException(
            status_code=409,
            detail="DigitalOcean is not connected for this workspace.",
        )
    repo = await _require_repo(session, workspace_id, src.repo_id)

    dep = Deployment(
        workspace_id=workspace_id,
        repo_id=src.repo_id,
        provider=src.provider,
        status=DS.DEPLOYING,
        started_at=datetime.now(timezone.utc),
    )
    session.add(dep)
    await session.flush()
    # No start event here: _submit_to_digitalocean emits DEPLOYED on success
    # (and DEPLOY_FAILED on failure) — emitting DEPLOYED up front would be both
    # premature and a duplicate.
    await _submit_to_digitalocean(
        session=session,
        dep=dep,
        repo=repo,
        token=token,
        deploy_plan=deploy_plan,
        redeployed_from_id=src.id,
        operator_env=(body.env if body else []),
    )
    names = await _repo_name_map(session, [dep.repo_id])
    return await _to_out_async(session, dep, repo_full_name=names.get(dep.repo_id))


@router.post(
    "/workspaces/{workspace_id}/deployments/{deployment_id}/rollback",
    response_model=ApiDeploymentOut,
    status_code=202,
)
async def rollback_version(
    workspace_id: uuid.UUID,
    deployment_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ApiDeploymentOut:
    """Roll back the live app via the provider's native rollback primitive."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    src = await session.get(Deployment, deployment_id)
    if src is None or src.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Deployment not found")
    src_ref = src.provider_ref or {}
    app_id = src_ref.get("app_id")
    target_deployment_id = src_ref.get("deployment_id")
    if not app_id or not target_deployment_id:
        raise HTTPException(
            status_code=400,
            detail="This version has no provider deployment id to roll back to.",
        )
    if src.status != DS.ACTIVE:
        raise HTTPException(
            status_code=400,
            detail="Only successful active versions can be used as rollback targets.",
        )

    try:
        token = await get_provider_token(session, workspace_id, src.provider)
    except ProviderOperationUnsupported as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not token:
        raise HTTPException(
            status_code=409,
            detail=f"{src.provider} is not connected for this workspace.",
        )

    try:
        rollback = await rollback_provider_deployment(
            token=token,
            ref=ProviderRef(
                provider=src.provider,
                app_id=app_id,
                deployment_id=target_deployment_id,
            ),
        )
    except HTTPException:
        raise
    except ProviderOperationUnsupported as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    now = datetime.now(timezone.utc)
    dep = Deployment(
        workspace_id=workspace_id,
        repo_id=src.repo_id,
        provider=src.provider,
        status=DS.DEPLOYING,
        status_detail=rollback.status_detail,
        plan=src.plan or {},
        provider_ref={
            "provider": rollback.ref.provider,
            "app_id": rollback.ref.app_id,
            "deployment_id": rollback.ref.deployment_id,
            "rolled_back_from_id": str(src.id),
            "rolled_back_from_provider_deployment_id": target_deployment_id,
            "rollback_kind": "provider",
        },
        started_at=now,
        updated_at=now,
    )
    if src_ref.get("estimated_monthly_usd") is not None:
        dep.provider_ref["estimated_monthly_usd"] = src_ref.get(
            "estimated_monthly_usd"
        )
    session.add(dep)
    await session.flush()
    await _event(
        session,
        dep,
        DeploymentEventKind.DEPLOYED,
        "Rollback started on provider.",
    )
    names = await _repo_name_map(session, [dep.repo_id])
    return await _to_out_async(session, dep, repo_full_name=names.get(dep.repo_id))


class ApiDeployLogsOut(BaseModel):
    type: str
    text: str
    truncated: bool = False


@router.get(
    "/workspaces/{workspace_id}/deployments/{deployment_id}/logs",
    response_model=ApiDeployLogsOut,
)
async def get_deployment_logs(
    workspace_id: uuid.UUID,
    deployment_id: uuid.UUID,
    type: str = "BUILD",
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ApiDeployLogsOut:
    """Fetch one log stream (BUILD/DEPLOY/RUN) for a deployment from DO.

    Read-only and best-effort: returns empty text if DO has no log for that
    stream yet (or the pointer expired), rather than erroring.
    """
    log_type = (type or "BUILD").upper()
    if log_type not in LOG_TYPES:
        raise HTTPException(status_code=400, detail="type must be BUILD, DEPLOY, or RUN")
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    dep = await session.get(Deployment, deployment_id)
    if dep is None or dep.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="Deployment not found")

    ref = dep.provider_ref or {}
    app_id, dep_ref = ref.get("app_id"), ref.get("deployment_id")
    if not app_id or not dep_ref:
        # No DO app/deployment yet (e.g. failed before submit, or the
        # Actions-planning path) — there are simply no provider logs to show.
        return ApiDeployLogsOut(type=log_type, text="", truncated=False)

    token = await get_do_token(session, workspace_id)
    if not token:
        raise HTTPException(
            status_code=409,
            detail="DigitalOcean is not connected for this workspace.",
        )
    text, truncated = await fetch_deploy_logs(
        app_id, dep_ref, log_type=log_type, token=token
    )
    return ApiDeployLogsOut(type=log_type, text=text, truncated=truncated)


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
    return [
        await _to_out_async(session, r, repo_full_name=names.get(r.repo_id))
        for r in rows
    ]


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
    return [await _to_out_async(session, r, repo_full_name=repo.full_name) for r in rows]


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
    and soft-delete its deployment rows. Idempotent — a DO 404 means it's
    already gone. Admin only.
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
        "soft_deleted": result.rows_soft_deleted,
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
