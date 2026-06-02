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


def health_check_path(plan: dict) -> str:
    """Pick the health path from a DeployPlan: an explicit service path if
    present, else ``/`` (a served static site / SPA answers 200 at root,
    whereas ``/health`` 404s)."""
    for comp in (plan or {}).get("components", []):
        hc = comp.get("health_check_path")
        if hc:
            return str(hc)
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
            healthy = await probe(url, path, client=http)
            checked += 1
            for d in deps:
                if d.healthy != healthy:
                    d.healthy = healthy
                    d.updated_at = now
                    changed += 1
    return checked, changed


__all__ = ["health_check_path", "probe", "recheck_active_deployments"]
