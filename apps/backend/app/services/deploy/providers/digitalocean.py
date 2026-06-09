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
    ProviderRollbackResult,
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


def _operator_env_map(operator_env: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in operator_env or []:
        key = str(raw.get("key") or "").strip()
        if not key:
            continue
        value = str(raw.get("value") or "")
        prefix = f"{key}="
        if value.strip().startswith(prefix):
            value = value.strip()[len(prefix) :].strip()
        out[key] = {
            "key": key,
            "value": value,
            "secret": bool(raw.get("secret")),
        }
    return out


def _env_entry(key: str, value: str, *, secret: bool) -> dict[str, str]:
    return {
        "key": key,
        "scope": "RUN_AND_BUILD_TIME",
        "type": "SECRET" if secret else "GENERAL",
        "value": _render_env_value(value),
    }


def _build_envs(env_specs: list, operator: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    out = []
    for ev in env_specs or []:
        op = operator.get(ev.key)
        if op is not None:
            if (bool(op.get("secret")) or bool(ev.secret)) and not op.get("value"):
                continue
            out.append(
                _env_entry(
                    ev.key,
                    str(op.get("value") or ""),
                    secret=bool(op.get("secret")) or bool(ev.secret),
                )
            )
            continue
        # Secrets never carry a value here (the operator supplies them via
        # the Ship secret store / DO dashboard). Non-secret config can carry
        # a planner-inferred value — notably HOST=0.0.0.0 so the container
        # binds to all interfaces, and $APP_URL so a frontend reaches the
        # backend on the same domain.
        if ev.secret:
            continue
        out.append(_env_entry(ev.key, getattr(ev, "value", None) or "", secret=False))
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
    operator: dict[str, dict[str, Any]],
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
    envs = _build_envs(comp.env, operator)
    if envs:
        svc["envs"] = envs
    return svc


def _build_static_site(
    comp,
    *,
    source_key: str,
    source_dict: dict[str, Any],
    operator: dict[str, dict[str, Any]],
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
    envs = _build_envs(comp.env, operator)
    if envs:
        site["envs"] = envs
    return site


def _build_worker(
    comp,
    *,
    source_key: str,
    source_dict: dict[str, Any],
    operator: dict[str, dict[str, Any]],
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
    envs = _build_envs(comp.env, operator)
    if envs:
        worker["envs"] = envs
    return worker


def _estimate_spec_cost(
    spec: dict[str, Any], price_map: dict[str, float]
) -> float | None:
    """Sum DO's per-size monthly prices for a spec's run components (services +
    workers). Static sites are excluded (DO's free/cheap tier). Returns None if
    we couldn't price anything (so we show nothing rather than a wrong $0)."""
    total = 0.0
    priced = False
    for key in ("services", "workers"):
        for comp in spec.get(key, []) or []:
            slug = comp.get("instance_size_slug")
            count = comp.get("instance_count", 1) or 1
            price = price_map.get(slug)
            if price is not None:
                total += price * count
                priced = True
    return round(total, 2) if priced else None


def build_app_spec(
    plan: DeployPlan,
    *,
    repo_clone_url: str,
    full_name: str,
    branch: str,
    private: bool,
    region: str = "nyc",
    operator_env: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministically translate a :class:`DeployPlan` into a DO app spec."""
    operator = _operator_env_map(operator_env)
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
    declared_keys: set[str] = set()
    for comp in plan.components:
        declared_keys.update(ev.key for ev in (comp.env or []))
        if comp.kind == "service":
            services.append(
                _build_service(
                    comp,
                    source_key=source_key,
                    source_dict=source_dict,
                    operator=operator,
                )
            )
        elif comp.kind == "static_site":
            static_sites.append(
                _build_static_site(
                    comp,
                    source_key=source_key,
                    source_dict=source_dict,
                    operator=operator,
                )
            )
        elif comp.kind == "worker":
            workers.append(
                _build_worker(
                    comp,
                    source_key=source_key,
                    source_dict=source_dict,
                    operator=operator,
                )
            )
        # "job" components — skip for now (no standard App Platform job trigger)

    if services:
        spec["services"] = services
    if static_sites:
        spec["static_sites"] = static_sites
    if workers:
        spec["workers"] = workers
    app_envs = [
        _env_entry(key, str(op.get("value") or ""), secret=bool(op.get("secret")))
        for key, op in operator.items()
        if key not in declared_keys
        and (not bool(op.get("secret")) or bool(op.get("value")))
    ]
    if app_envs:
        spec["envs"] = app_envs
    return spec


def _env_key(entry: dict[str, Any]) -> str:
    return str(entry.get("key") or "")


def _merge_env_list(
    desired: list[dict[str, Any]] | None,
    current: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    desired_out = [dict(e) for e in (desired or []) if _env_key(e)]
    by_key = {_env_key(e): e for e in desired_out}
    for cur in current or []:
        key = _env_key(cur)
        if not key:
            continue
        cur_type = str(cur.get("type") or "").upper()
        encrypted = isinstance(cur.get("value"), str) and cur["value"].startswith("EV[")
        if cur_type != "SECRET" and key in by_key:
            continue
        if key in by_key:
            fresh = by_key[key]
            fresh_value = fresh.get("value")
            if fresh_value:
                continue
            if encrypted:
                fresh["value"] = cur["value"]
            continue
        if cur_type == "SECRET" and encrypted:
            desired_out.append(dict(cur))
        elif cur_type == "GENERAL":
            desired_out.append(dict(cur))
    return desired_out


def _component_bucket(spec: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {
        str(comp.get("name")): comp
        for comp in (spec.get(key) or [])
        if isinstance(comp, dict) and comp.get("name")
    }


def merge_current_envs(spec: dict[str, Any], current_spec: dict[str, Any]) -> dict[str, Any]:
    """Preserve existing DO encrypted secrets on update.

    DigitalOcean secrets are write-only. On PUT, unchanged secrets must be sent
    back as their EV[1:...] ciphertext; omitting or sending an empty value wipes
    them.
    """
    merged = dict(spec)
    app_envs = _merge_env_list(merged.get("envs"), current_spec.get("envs"))
    if app_envs:
        merged["envs"] = app_envs
    for bucket in ("services", "static_sites", "workers", "jobs"):
        current_by_name = _component_bucket(current_spec, bucket)
        out_components = []
        for comp in merged.get(bucket) or []:
            if not isinstance(comp, dict):
                out_components.append(comp)
                continue
            cur = current_by_name.get(str(comp.get("name")))
            if cur:
                comp = dict(comp)
                envs = _merge_env_list(comp.get("envs"), cur.get("envs"))
                if envs:
                    comp["envs"] = envs
            out_components.append(comp)
        if out_components:
            merged[bucket] = out_components
    return merged


def spec_env_settings(spec: dict[str, Any]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}

    def add_many(envs: list[dict[str, Any]] | None) -> None:
        for env in envs or []:
            key = _env_key(env)
            if not key:
                continue
            secret = str(env.get("type") or "").upper() == "SECRET"
            value = env.get("value")
            by_key[key] = {
                "key": key,
                "value": None if secret else (value if isinstance(value, str) else None),
                "secret": secret,
                "set": bool(value),
            }

    add_many(spec.get("envs"))
    for bucket in ("services", "static_sites", "workers", "jobs"):
        for comp in spec.get(bucket) or []:
            if isinstance(comp, dict):
                add_many(comp.get("envs"))
    return list(by_key.values())


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
        operator_env: list[dict[str, Any]] | None = None,
    ) -> ProviderRef:
        spec = build_app_spec(
            plan,
            repo_clone_url=repo_clone_url,
            full_name=self._full_name,
            branch=branch,
            private=self._private,
            region=self._region,
            operator_env=operator_env,
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
        # Fallback when propose returned no figure: sum DO's PUBLISHED per-size
        # prices for the spec's run components. Still DO's own numbers.
        if estimated_monthly_usd is None:
            try:
                sizes = await do.instance_sizes(token=self._token, client=self._client)
                estimated_monthly_usd = _estimate_spec_cost(
                    spec, do.instance_price_map(sizes)
                )
            except Exception as exc:  # noqa: BLE001 — cost is non-critical
                logger.info("DO instance_sizes cost fallback failed: %s", exc)
        dep_id: str | None = None
        if existing_app_id:
            # Redeploy: update the existing DO app in place. CRITICAL: a PUT
            # spec update does NOT re-pull source code — DO reuses its cached
            # branch tip, so after a force-push it keeps building a stale/dead
            # commit ("error checking out commit: object not found"). So after
            # applying the spec we explicitly create_deployment(force_build) to
            # force a fresh source fetch of the branch's real HEAD.
            try:
                logger.info(
                    "Updating DO app %s ('%s')", existing_app_id, plan.app_name
                )
                current_app = await do.get_app(
                    existing_app_id, token=self._token, client=self._client
                )
                spec = merge_current_envs(spec, current_app.get("spec") or {})
                # Region IS changeable on DO — editing the spec's region field
                # migrates the app and redeploys it. Honor the requested region
                # (the console pre-fills it to the app's current region on a
                # plain redeploy, so this is a no-op unless the operator
                # deliberately picks a different one). NOTE: a managed DB stays
                # in the old region — that's on the operator to move.
                spec["region"] = self._region
                app = await do.update_app(
                    existing_app_id, spec, token=self._token, client=self._client
                )
                app_id = app["id"]
                fresh = await do.create_deployment(
                    app_id, token=self._token, force_build=True, client=self._client
                )
                dep_id = fresh.get("id")
            except do.DigitalOceanAPIError as exc:
                # The recorded app was deleted on DO (torn down in the dashboard
                # or by a prior teardown), so our stored app_id is stale. Don't
                # fail the deploy on the 404 — drop it and create a fresh app
                # below (in the requested region). Other errors still surface.
                if exc.status != 404:
                    raise
                logger.info(
                    "DO app %s no longer exists (404) — creating a fresh app",
                    existing_app_id,
                )
                existing_app_id = None
        if not existing_app_id:
            logger.info(
                "Creating DO app '%s' (%d components)",
                plan.app_name,
                len(plan.components),
            )
            # A create already deploys the branch's current HEAD (fresh).
            app = await do.create_app(spec, token=self._token, client=self._client)
            app_id = app["id"]
            dep = await do.get_latest_deployment(
                app_id, token=self._token, client=self._client
            )
            dep_id = dep["id"] if dep else None
        logger.info("DO app %s, deployment %s", app_id, dep_id)
        extra: dict[str, Any] = {"spec_name": plan.app_name, "region": self._region}
        env_settings = spec_env_settings(spec)
        if env_settings:
            extra["operator_env"] = env_settings
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

    async def delete_app(self, app_id: str) -> bool:
        """Delete one DO app. True if gone, including already-404."""
        try:
            await do.delete_app(app_id, token=self._token, client=self._client)
            return True
        except do.DigitalOceanAPIError as exc:
            if exc.status == 404:
                return True
            logger.warning("DO delete app %s failed: %s", app_id, exc)
            return False
        except httpx.HTTPError as exc:
            logger.warning("DO delete app %s network error: %s", app_id, exc)
            return False

    async def rollback(self, ref: ProviderRef) -> ProviderRollbackResult:
        """Roll back the DO app to ``ref.deployment_id`` using DO native rollback."""
        if not ref.deployment_id:
            raise ValueError("DigitalOcean rollback requires deployment_id")
        validation = await do.validate_rollback(
            ref.app_id,
            ref.deployment_id,
            token=self._token,
            skip_pin=True,
            client=self._client,
        )
        if isinstance(validation, dict) and validation.get("valid") is False:
            raise ValueError("DigitalOcean says this deployment cannot be rolled back to.")
        rollback = await do.rollback_app(
            ref.app_id,
            ref.deployment_id,
            token=self._token,
            skip_pin=True,
            client=self._client,
        )
        dep = (rollback or {}).get("deployment") if isinstance(rollback, dict) else None
        if not dep:
            dep = await do.get_latest_deployment(
                ref.app_id, token=self._token, client=self._client
            )
        deployment_id = dep.get("id") if isinstance(dep, dict) else None
        return ProviderRollbackResult(
            ref=ProviderRef(
                provider=_PROVIDER,
                app_id=ref.app_id,
                deployment_id=deployment_id,
            ),
            status_detail=do.deployment_phase(dep) if dep else None,
        )


__all__ = [
    "DigitalOceanAppPlatform",
    "build_app_spec",
]
