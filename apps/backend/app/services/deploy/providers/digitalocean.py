"""DigitalOcean App Platform deploy provider.

Translates a provider-agnostic :class:`DeployPlan` into a DO App Platform
app spec and drives the create → poll → health flow. All provider-specific
knowledge (field names, slug values, source shapes) is confined here.

Spec mapping rules
------------------
* ``service`` → ``services`` component
* ``static_site`` → ``static_sites`` component
* ``worker`` → ``workers`` component  (no HTTP, no health check)
* ``job`` → ``jobs`` component         (run-to-completion)

Source
------
Public repo (``private=False``) uses the ``git`` source (clone URL + branch).
No DigitalOcean↔GitHub authorisation required — works immediately.

Private repo uses the ``github`` source (``owner/repo`` + branch). DO requires
the user to have authorised the DigitalOcean GitHub App on the repo; the
adapter adds a warning to the plan so the UI can surface this requirement.
The caller is responsible for deciding whether to proceed.
"""

from __future__ import annotations

import logging
from typing import Any, Final

import httpx

from backend.app.integrations.digitalocean import client as do
from backend.app.services.deploy.plan import DeployPlan
from backend.app.services.deploy.providers.base import (
    DeploymentStatus,
    DeployProvider,
    ProviderRef,
)


logger = logging.getLogger(__name__)

_PROVIDER: Final[str] = "digitalocean"

# DO App Platform buildpack environment slugs we know how to emit.
_RUNTIME_SLUG: Final[dict[str, str]] = {
    "python": "python",
    "node-js": "node-js",
    "go": "go",
    "ruby": "ruby",
    "php": "php",
    "static": "html",
    "docker": "",   # Dockerfile drive; no environment_slug needed
}

# Generous initial delay for Streamlit (cold start ~30 s).
_DEFAULT_HC_INITIAL_DELAY: Final[int] = 30
_DEFAULT_HC_PERIOD: Final[int] = 10
_DEFAULT_HC_TIMEOUT: Final[int] = 5
_DEFAULT_HC_FAILURE_THRESHOLD: Final[int] = 9


def _build_source(
    *,
    repo_clone_url: str,
    branch: str,
    full_name: str,
    private: bool,
) -> tuple[str, dict[str, Any]]:
    """Return (source_key, source_dict) for the app spec.

    Public → ``git`` (clone URL, no auth needed).
    Private → ``github`` (owner/repo slug, requires DO↔GitHub integration).
    """
    if private:
        return "github", {
            "repo": full_name,
            "branch": branch,
            "deploy_on_push": False,
        }
    return "git", {
        "repo_clone_url": repo_clone_url,
        "branch": branch,
    }


def _render_env_value(raw: str) -> str:
    """Translate provider-neutral tokens in a planner env value to DO syntax.

    The planner emits the neutral token ``$APP_URL`` to mean "this app's own
    public URL" (e.g. to point a frontend's build-time API base at the
    backend on the same domain). DigitalOcean exposes that as the bindable
    variable ``${APP_URL}``. Kept tiny + provider-local so the plan stays
    neutral and Azure/etc. adapters render their own equivalent.
    """
    return raw.replace("$APP_URL", "${APP_URL}")


def _build_envs(env_specs: list) -> list[dict[str, str]]:
    out = []
    for ev in env_specs or []:
        # Secrets never carry a value here (the operator supplies them via
        # the Ship secret store / DO dashboard). Non-secret config can carry
        # a planner-inferred value — notably HOST=0.0.0.0 so the container
        # binds to all interfaces, and $APP_URL so a frontend reaches the
        # backend on the same domain.
        value = "" if ev.secret else _render_env_value(getattr(ev, "value", None) or "")
        entry: dict[str, str] = {
            "key": ev.key,
            "scope": "RUN_AND_BUILD_TIME",
            "type": "SECRET" if ev.secret else "GENERAL",
            "value": value,
        }
        out.append(entry)
    return out


def _build_health_check(component) -> dict[str, Any] | None:
    if not component.health_check_path:
        return None
    return {
        "http_path": component.health_check_path,
        "initial_delay_seconds": _DEFAULT_HC_INITIAL_DELAY,
        "period_seconds": _DEFAULT_HC_PERIOD,
        "timeout_seconds": _DEFAULT_HC_TIMEOUT,
        "failure_threshold": _DEFAULT_HC_FAILURE_THRESHOLD,
    }


def _root_relative_dockerfile(source_dir: str | None, dockerfile_path: str) -> str:
    """Make ``dockerfile_path`` relative to the repo root for DigitalOcean.

    DO resolves ``dockerfile_path`` from the **repository root**, but the
    planner specifies it relative to the component's ``source_dir`` (the
    intuitive contract). For a monorepo component — ``source_dir`` =
    ``apps/backend``, ``dockerfile_path`` = ``Dockerfile`` — DO would
    otherwise look for ``/Dockerfile`` at the root and fail with "no such
    file". Joining them yields the real ``apps/backend/Dockerfile``. Idempotent
    if the planner already gave a root-relative path.
    """
    sd = (source_dir or "").strip("/")
    df = (dockerfile_path or "").strip("/")
    if not sd or df == sd or df.startswith(sd + "/"):
        return df
    return f"{sd}/{df}"


def _build_service(
    comp,
    *,
    source_key: str,
    source_dict: dict[str, Any],
) -> dict[str, Any]:
    slug = _RUNTIME_SLUG.get(comp.runtime, comp.runtime)
    svc: dict[str, Any] = {
        "name": comp.name,
        source_key: source_dict,
        "source_dir": comp.source_dir or "/",
        "instance_size_slug": comp.instance_size or "basic-xxs",
        "instance_count": 1,
    }
    if slug:
        svc["environment_slug"] = slug
    if comp.dockerfile_path:
        svc["dockerfile_path"] = _root_relative_dockerfile(
            comp.source_dir, comp.dockerfile_path
        )
    if comp.build_command:
        svc["build_command"] = comp.build_command
    if comp.run_command:
        svc["run_command"] = comp.run_command
    if comp.http_port:
        svc["http_port"] = comp.http_port
    routes = comp.routes or ["/"]
    svc["routes"] = [{"path": r} for r in routes]
    hc = _build_health_check(comp)
    if hc:
        svc["health_check"] = hc
    envs = _build_envs(comp.env)
    if envs:
        svc["envs"] = envs
    return svc


def _build_static_site(
    comp,
    *,
    source_key: str,
    source_dict: dict[str, Any],
) -> dict[str, Any]:
    site: dict[str, Any] = {
        "name": comp.name,
        source_key: source_dict,
        "source_dir": comp.source_dir or "/",
    }
    if comp.build_command:
        site["build_command"] = comp.build_command
    if comp.output_dir:
        site["output_dir"] = comp.output_dir
    routes = comp.routes or ["/"]
    site["routes"] = [{"path": r} for r in routes]
    envs = _build_envs(comp.env)
    if envs:
        site["envs"] = envs
    return site


def _build_worker(
    comp,
    *,
    source_key: str,
    source_dict: dict[str, Any],
) -> dict[str, Any]:
    slug = _RUNTIME_SLUG.get(comp.runtime, comp.runtime)
    worker: dict[str, Any] = {
        "name": comp.name,
        source_key: source_dict,
        "source_dir": comp.source_dir or "/",
        "instance_size_slug": comp.instance_size or "basic-xxs",
        "instance_count": 1,
    }
    if slug:
        worker["environment_slug"] = slug
    if comp.dockerfile_path:
        worker["dockerfile_path"] = _root_relative_dockerfile(
            comp.source_dir, comp.dockerfile_path
        )
    if comp.build_command:
        worker["build_command"] = comp.build_command
    if comp.run_command:
        worker["run_command"] = comp.run_command
    envs = _build_envs(comp.env)
    if envs:
        worker["envs"] = envs
    return worker


def build_app_spec(
    plan: DeployPlan,
    *,
    repo_clone_url: str,
    full_name: str,
    branch: str,
    private: bool,
    region: str = "nyc",
) -> dict[str, Any]:
    """Deterministically translate a :class:`DeployPlan` into a DO app spec."""
    source_key, source_dict = _build_source(
        repo_clone_url=repo_clone_url,
        branch=branch,
        full_name=full_name,
        private=private,
    )
    spec: dict[str, Any] = {
        "name": plan.app_name,
        "region": region,
    }
    services, static_sites, workers, jobs = [], [], [], []
    for comp in plan.components:
        if comp.kind == "service":
            services.append(_build_service(comp, source_key=source_key, source_dict=source_dict))
        elif comp.kind == "static_site":
            static_sites.append(_build_static_site(comp, source_key=source_key, source_dict=source_dict))
        elif comp.kind == "worker":
            workers.append(_build_worker(comp, source_key=source_key, source_dict=source_dict))
        # "job" components — skip for now (no standard App Platform job trigger)

    if services:
        spec["services"] = services
    if static_sites:
        spec["static_sites"] = static_sites
    if workers:
        spec["workers"] = workers
    return spec


class DigitalOceanAppPlatform:
    """Deploy provider adapter for DigitalOcean App Platform."""

    def __init__(
        self,
        *,
        token: str,
        full_name: str,
        private: bool,
        region: str = "nyc",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._full_name = full_name
        self._private = private
        self._region = region
        self._client = http_client

    async def apply(
        self,
        plan: DeployPlan,
        *,
        repo_clone_url: str,
        branch: str,
        existing_app_id: str | None = None,
    ) -> ProviderRef:
        spec = build_app_spec(
            plan,
            repo_clone_url=repo_clone_url,
            full_name=self._full_name,
            branch=branch,
            private=self._private,
            region=self._region,
        )
        # Ask DO for ITS OWN monthly cost estimate for this exact spec before we
        # create/update. Best-effort: a propose failure must never block the
        # deploy (we just won't have a number to show). We surface DO's figure
        # as-is — no homemade pricing math.
        estimated_monthly_usd: float | None = None
        try:
            proposal = await do.propose_app(spec, token=self._token, client=self._client)
            estimated_monthly_usd = do.propose_monthly_cost(proposal)
        except Exception as exc:  # noqa: BLE001 — cost is non-critical
            logger.info("DO propose (cost estimate) failed, continuing: %s", exc)
        if existing_app_id:
            # Redeploy: update the existing DO app in place (new deployment
            # under the SAME app) rather than spawning a duplicate.
            logger.info("Updating DO app %s ('%s')", existing_app_id, plan.app_name)
            app = await do.update_app(
                existing_app_id, spec, token=self._token, client=self._client
            )
        else:
            logger.info(
                "Creating DO app '%s' (%d components)",
                plan.app_name,
                len(plan.components),
            )
            app = await do.create_app(spec, token=self._token, client=self._client)
        app_id = app["id"]
        # A deployment is created automatically on create/update.
        dep = await do.get_latest_deployment(app_id, token=self._token, client=self._client)
        dep_id = dep["id"] if dep else None
        logger.info("DO app %s, deployment %s", app_id, dep_id)
        extra: dict[str, Any] = {"spec_name": plan.app_name, "region": self._region}
        if estimated_monthly_usd is not None:
            extra["estimated_monthly_usd"] = estimated_monthly_usd
        return ProviderRef(
            provider=_PROVIDER,
            app_id=app_id,
            deployment_id=dep_id,
            extra=extra,
        )

    async def status(self, ref: ProviderRef) -> DeploymentStatus:
        try:
            app = await do.get_app(ref.app_id, token=self._token, client=self._client)
        except do.DigitalOceanAPIError as exc:
            return DeploymentStatus(
                phase="ERROR",
                terminal=True,
                succeeded=False,
                error_message=str(exc),
            )
        dep_id = ref.deployment_id
        if dep_id:
            try:
                dep = await do.get_deployment(
                    ref.app_id, dep_id, token=self._token, client=self._client
                )
            except do.DigitalOceanAPIError:
                dep = None
        else:
            dep = await do.get_latest_deployment(
                ref.app_id, token=self._token, client=self._client
            )

        phase = do.deployment_phase(dep) if dep else "UNKNOWN"
        terminal = do.is_terminal(phase)
        succeeded = phase == do.PHASE_ACTIVE
        live_url = do.app_live_url(app) if succeeded else None
        error_msg: str | None = None
        if do.is_failed(phase) and dep:
            # Surface the progress pages error if available.
            cause = (dep.get("cause") or dep.get("progress", {}) or {})
            if isinstance(cause, dict):
                error_msg = cause.get("error_steps") or cause.get("reason")
            elif isinstance(cause, str):
                error_msg = cause

        return DeploymentStatus(
            phase=phase,
            terminal=terminal,
            succeeded=succeeded,
            live_url=live_url,
            error_message=error_msg,
        )

    async def health_check(self, url: str, path: str) -> bool:
        full = url.rstrip("/") + "/" + path.lstrip("/")
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0), follow_redirects=True
            ) as http:
                resp = await http.get(full)
            return resp.status_code < 400
        except Exception as exc:  # noqa: BLE001
            logger.debug("healthcheck %s failed: %s", full, exc)
            return False


__all__ = [
    "DigitalOceanAppPlatform",
    "build_app_spec",
]
