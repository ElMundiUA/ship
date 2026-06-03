"""Health monitoring for live deployments.

Used in two places:
* the single-deployment GET route, for an on-demand re-check, and
* the ``deployments_health`` cron tick (every 15 min), which keeps the
  ``healthy`` flag fresh for every ACTIVE deployment even when nobody has
  the console open.

The probe is a plain HTTP GET against the app's live URL + the plan's
health path (``/`` for static sites; a service may declare e.g. Streamlit's
``/_stcore/health``). 2xx/3xx ⇒ healthy.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.deploy import Deployment, DeploymentStatus as DS


log = logging.getLogger(__name__)

# Right after a deploy goes ACTIVE the app's public URL can take a minute or
# two to become reachable (DNS propagation on *.ondigitalocean.app, TLS, cold
# start). Probing in that window returns "not reachable" and would flip the
# card to a scary "failing" on a deploy that's actually fine. During the grace
# window we report ``None`` (pending/unknown) instead of ``False`` — a positive
# probe still flips to healthy immediately.
HEALTH_GRACE_SECONDS = 180


async def probe_with_grace(
    url: str,
    path: str,
    finished_at: datetime | None,
    *,
    client: httpx.AsyncClient | None = None,
    now: datetime | None = None,
) -> bool | None:
    """Probe health, but suppress a transient ``False`` within the grace
    window after ``finished_at`` (return ``None`` = pending instead)."""
    ok = await probe(url, path, client=client)
    if ok:
        return True
    now = now or datetime.now(timezone.utc)
    if finished_at is not None and (now - finished_at).total_seconds() < HEALTH_GRACE_SECONDS:
        return None
    return False


def _join_route(prefix: str, path: str) -> str:
    """Join a component's external route prefix with its internal health path.

    A backend routed at ``/api`` whose app code serves ``/health`` is reachable
    from the public URL at ``/api/health`` (DO strips the route prefix before
    forwarding). So the probe must hit ``/api/health`` — probing ``/health``
    lands on whatever owns ``/`` (usually the frontend static site) and 404s.
    """
    pfx = "/" + prefix.strip("/") if prefix.strip("/") else "/"
    p = "/" + path.lstrip("/")
    if pfx == "/":
        return p
    # Don't double the prefix if the health path already includes it.
    if p == pfx or p.startswith(pfx + "/"):
        return p
    return pfx.rstrip("/") + p


def health_check_path(plan: dict) -> str:
    """Pick the externally-reachable health path from a DeployPlan: a service's
    health path prefixed by its route (e.g. route ``/api`` + ``/health`` ->
    ``/api/health``), else ``/`` (a served static site / SPA answers 200 at
    root, whereas an unprefixed ``/health`` 404s on the frontend)."""
    for comp in (plan or {}).get("components", []):
        hc = comp.get("health_check_path")
        if hc:
            routes = comp.get("routes") or []
            prefix = str(routes[0]) if routes else ""
            return _join_route(prefix, str(hc))
    return "/"


async def probe(url: str, path: str, *, client: httpx.AsyncClient | None = None) -> bool:
    """Return True if ``url + path`` answers with HTTP < 400."""
    full = url.rstrip("/") + "/" + path.lstrip("/")
    owns = client is None
    http = client or httpx.AsyncClient(
        timeout=httpx.Timeout(10.0), follow_redirects=True
    )
    try:
        resp = await http.get(full)
        return resp.status_code < 400
    except Exception:  # noqa: BLE001 — any failure means "not healthy right now"
        return False
    finally:
        if owns:
            await http.aclose()


async def recheck_active_deployments(session: AsyncSession) -> tuple[int, int]:
    """Re-probe every ACTIVE deployment with a live URL; update ``healthy``.

    Distinct URLs are probed once (a redeploy can leave several ACTIVE rows
    sharing one app URL). Returns ``(urls_checked, rows_changed)``. The
    caller commits.
    """
    rows = (
        await session.execute(
            select(Deployment).where(
                Deployment.status == DS.ACTIVE,
                Deployment.live_url.isnot(None),
            )
        )
    ).scalars().all()

    groups: dict[tuple[str, str], list[Deployment]] = {}
    for d in rows:
        groups.setdefault((d.live_url or "", health_check_path(d.plan)), []).append(d)

    checked = changed = 0
    now = datetime.now(timezone.utc)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0), follow_redirects=True
    ) as http:
        for (url, path), deps in groups.items():
            if not url:
                continue
            ok = await probe(url, path, client=http)
            checked += 1
            for d in deps:
                if ok:
                    new_health: bool | None = True
                elif (
                    d.finished_at is not None
                    and (now - d.finished_at).total_seconds() < HEALTH_GRACE_SECONDS
                ):
                    new_health = None  # too soon after deploy to call it failing
                else:
                    new_health = False
                if d.healthy != new_health:
                    d.healthy = new_health
                    d.updated_at = now
                    changed += 1
    return checked, changed


__all__ = [
    "health_check_path",
    "probe",
    "probe_with_grace",
    "recheck_active_deployments",
]
