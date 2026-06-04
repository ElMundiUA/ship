"""DigitalOcean App Platform REST client.

A thin httpx wrapper over the DO v2 API. All callers pass the decrypted
``access_token`` explicitly — this module has no knowledge of how Ship
stores or refreshes credentials; that lives in the OAuth route and the
token-refresh cron.

Endpoints used:
  POST /v2/apps                             — create a new app
  GET  /v2/apps/{app_id}                    — read app state + live_url
  GET  /v2/apps/{app_id}/deployments        — list deployments (newest first)
  GET  /v2/apps/{app_id}/deployments/{did}  — single deployment detail
  POST /v2/apps/{app_id}/rollback/validate  — validate rollback target
  POST /v2/apps/{app_id}/rollback           — rollback to a deployment

DO App Platform deployment phases (DeploymentPhase):
  UNKNOWN / PENDING_BUILD / BUILDING / PENDING_DEPLOY / DEPLOYING
  ACTIVE / SUPERSEDED / ERROR / CANCELED

``ACTIVE`` is the terminal success state; ``ERROR`` / ``CANCELED`` are
terminal failures. Everything else is in-flight.
"""

from __future__ import annotations

import logging
from typing import Any, Final

import httpx


logger = logging.getLogger(__name__)

_BASE: Final[str] = "https://api.digitalocean.com/v2"

# Terminal deployment phases.
PHASE_ACTIVE: Final[str] = "ACTIVE"
PHASE_ERROR: Final[str] = "ERROR"
PHASE_CANCELED: Final[str] = "CANCELED"
TERMINAL_PHASES: Final[frozenset[str]] = frozenset(
    {PHASE_ACTIVE, PHASE_ERROR, PHASE_CANCELED, "SUPERSEDED"}
)
FAILED_PHASES: Final[frozenset[str]] = frozenset({PHASE_ERROR, PHASE_CANCELED})


class DigitalOceanAPIError(RuntimeError):
    """DO API returned a non-2xx response."""

    def __init__(self, method: str, url: str, status: int, body: str) -> None:
        super().__init__(
            f"DigitalOcean API {method} {url} returned HTTP {status}: {body[:300]}"
        )
        self.status = status


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _request(
    method: str,
    path: str,
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
    json: Any = None,
    params: dict[str, Any] | None = None,
) -> Any:
    url = f"{_BASE}{path}"
    owns = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))
    try:
        resp = await http.request(
            method, url, headers=_headers(token), json=json, params=params
        )
    finally:
        if owns:
            await http.aclose()
    if resp.status_code >= 400:
        raise DigitalOceanAPIError(method, url, resp.status_code, resp.text)
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


async def create_app(
    spec: dict[str, Any],
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST /v2/apps — returns the full app object."""
    data = await _request("POST", "/apps", token=token, json={"spec": spec}, client=client)
    return data["app"]


async def update_app(
    app_id: str,
    spec: dict[str, Any],
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """PUT /v2/apps/{app_id} — update an existing app's spec (triggers a new
    deployment under the SAME app instead of creating a new one)."""
    data = await _request(
        "PUT", f"/apps/{app_id}", token=token, json={"spec": spec}, client=client
    )
    return data["app"]


async def propose_app(
    spec: dict[str, Any],
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST /v2/apps/propose — validate a spec and get DigitalOcean's OWN cost
    estimate for it (no deploy). Returns the raw propose response, which
    includes the monthly cost DO computes for this exact spec. We surface DO's
    number as-is rather than inventing our own pricing math."""
    return await _request(
        "POST", "/apps/propose", token=token, json={"spec": spec}, client=client
    )


# Numeric monthly-cost fields DO has used on the propose response, in order of
# preference. We read whatever DO gives and never compute our own estimate.
_COST_KEYS: Final[tuple[str, ...]] = (
    "app_cost",
    "monthly_cost",
    "app_tier_upgrade_cost",
)


async def instance_sizes(
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """GET /v2/apps/tiers/instance_sizes — DO's published per-size monthly
    prices. Used as a cost fallback when ``/apps/propose`` returns no figure;
    still DO's OWN numbers (their price list), not an invented formula."""
    return await _request("GET", "/apps/tiers/instance_sizes", token=token, client=client)


def instance_price_map(sizes: dict[str, Any] | None) -> dict[str, float]:
    """Map ``instance_size_slug`` -> monthly USD from an instance_sizes
    response. Reads DO's ``usd_per_month`` (falls back to a couple of keys)."""
    out: dict[str, float] = {}
    if not isinstance(sizes, dict):
        return out
    for entry in sizes.get("instance_sizes") or []:
        slug = entry.get("slug")
        if not slug:
            continue
        for key in ("usd_per_month", "monthly_price_usd", "price_monthly"):
            raw = entry.get(key)
            if raw is None:
                continue
            try:
                out[slug] = float(raw)
            except (TypeError, ValueError):
                pass
            break
    return out


def propose_monthly_cost(propose: dict[str, Any] | None) -> float | None:
    """Pull the monthly USD cost out of a propose response, or None if DO
    didn't return one (then we simply show nothing — no invented number)."""
    if not isinstance(propose, dict):
        return None
    for key in _COST_KEYS:
        raw = propose.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return round(val, 2)
    return None


async def get_app(
    app_id: str,
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """GET /v2/apps/{app_id} — returns the full app object."""
    data = await _request("GET", f"/apps/{app_id}", token=token, client=client)
    return data["app"]


async def delete_app(
    app_id: str,
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> None:
    """DELETE /v2/apps/{app_id} — tear the app down (App Platform has no
    pause; deleting is how you stop it running and billing). Idempotent
    from the caller's view: a 404 means it's already gone."""
    await _request("DELETE", f"/apps/{app_id}", token=token, client=client)


async def get_latest_deployment(
    app_id: str,
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any] | None:
    """Return the most recent deployment for ``app_id``, or None."""
    data = await _request(
        "GET",
        f"/apps/{app_id}/deployments",
        token=token,
        params={"page": 1, "per_page": 1},
        client=client,
    )
    deployments = (data or {}).get("deployments") or []
    return deployments[0] if deployments else None


async def create_deployment(
    app_id: str,
    *,
    token: str,
    force_build: bool = True,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST /v2/apps/{app_id}/deployments — trigger a fresh deployment that
    PULLS THE LATEST COMMIT from the source repo. Critical for redeploys:
    ``update_app`` (PUT spec) does NOT re-pull code (DO reuses its cached
    branch tip), so after a force-push it would keep building a stale commit.
    Creating a deployment forces a fresh source fetch + build."""
    data = await _request(
        "POST",
        f"/apps/{app_id}/deployments",
        token=token,
        json={"force_build": force_build},
        client=client,
    )
    return data["deployment"]


async def get_deployment(
    app_id: str,
    deployment_id: str,
    *,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """GET /v2/apps/{app_id}/deployments/{did}."""
    data = await _request(
        "GET", f"/apps/{app_id}/deployments/{deployment_id}",
        token=token, client=client,
    )
    return data["deployment"]


async def validate_rollback(
    app_id: str,
    deployment_id: str,
    *,
    token: str,
    skip_pin: bool = True,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST /v2/apps/{app_id}/rollback/validate."""
    return await _request(
        "POST",
        f"/apps/{app_id}/rollback/validate",
        token=token,
        json={"deployment_id": deployment_id, "skip_pin": skip_pin},
        client=client,
    )


async def rollback_app(
    app_id: str,
    deployment_id: str,
    *,
    token: str,
    skip_pin: bool = True,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """POST /v2/apps/{app_id}/rollback."""
    return await _request(
        "POST",
        f"/apps/{app_id}/rollback",
        token=token,
        json={"deployment_id": deployment_id, "skip_pin": skip_pin},
        client=client,
    )


async def deployment_logs(
    app_id: str,
    deployment_id: str,
    *,
    log_type: str,
    token: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """GET /v2/apps/{app_id}/deployments/{did}/logs?type=BUILD|DEPLOY|RUN.

    Returns DO's log pointer payload — ``{"historic_urls": [...]}`` for
    BUILD/DEPLOY (presigned archive URLs) or ``{"url", "live_url"}`` for RUN
    (a proxy snapshot + websocket). The caller fetches the actual text from
    those URLs.
    """
    return await _request(
        "GET",
        f"/apps/{app_id}/deployments/{deployment_id}/logs",
        token=token,
        params={"type": log_type},
        client=client,
    )


def app_live_url(app: dict[str, Any]) -> str | None:
    """Extract the public URL from an app object."""
    return app.get("live_url") or app.get("default_ingress") or None


def deployment_phase(dep: dict[str, Any]) -> str:
    return str(dep.get("phase") or "UNKNOWN").upper()


def is_terminal(phase: str) -> bool:
    return phase.upper() in TERMINAL_PHASES


def is_failed(phase: str) -> bool:
    return phase.upper() in FAILED_PHASES


__all__ = [
    "DigitalOceanAPIError",
    "PHASE_ACTIVE",
    "PHASE_ERROR",
    "TERMINAL_PHASES",
    "FAILED_PHASES",
    "app_live_url",
    "create_app",
    "create_deployment",
    "update_app",
    "deployment_phase",
    "get_app",
    "get_deployment",
    "get_latest_deployment",
    "deployment_logs",
    "is_failed",
    "is_terminal",
    "propose_app",
    "propose_monthly_cost",
    "instance_sizes",
    "instance_price_map",
    "rollback_app",
    "validate_rollback",
]
