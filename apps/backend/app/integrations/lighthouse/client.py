"""Thin async HTTP client for the Lighthouse ``/v1`` surface.

Two operations Ship needs:

- :meth:`search` — ``GET /v1/search`` scoped by ``X-Workspace``. The
  read path for the knowledge cutover.
- :meth:`provision_s3_importer` — ``POST /v1/importers/provision/s3`` to
  idempotently set up the per-workspace S3 source on workspace setup.

The client is intentionally stateless (a fresh ``httpx.AsyncClient`` per
call): these are low-frequency calls and a per-call client sidesteps
event-loop / lifespan ownership questions in the worker + request paths.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from backend.app.core.config import Settings

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class LighthouseClient:
    def __init__(self, *, base_url: str, admin_token: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._admin_token = admin_token

    async def search(
        self,
        *,
        workspace_id: uuid.UUID | str,
        query: str,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Return raw ``/v1/search`` hits (dicts) for the workspace.

        Raises ``httpx.HTTPError`` on transport / non-2xx — callers in
        the dual-read path treat any failure as "fall back to internal".
        """
        headers = {"X-Workspace": str(workspace_id)}
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT
        ) as client:
            resp = await client.get(
                "/v1/search",
                params={"q": query, "top_k": top_k},
                headers=headers,
            )
            resp.raise_for_status()
            return list(resp.json().get("hits", []))

    async def provision_s3_importer(
        self, *, workspace_id: uuid.UUID | str
    ) -> dict[str, Any]:
        """Idempotently provision the per-workspace S3 importer."""
        headers = {"X-Workspace": str(workspace_id)}
        if self._admin_token:
            headers["Authorization"] = f"Bearer {self._admin_token}"
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT
        ) as client:
            resp = await client.post(
                "/v1/importers/provision/s3", headers=headers
            )
            resp.raise_for_status()
            return dict(resp.json())


def build_lighthouse_client(settings: "Settings") -> LighthouseClient | None:
    """Construct a client when ``LIGHTHOUSE_BASE_URL`` is configured.

    Returns ``None`` when Lighthouse isn't wired (local dev, or before
    the cutover) — callers then use Ship's internal index.
    """
    base = getattr(settings, "lighthouse_base_url", None)
    if not base:
        return None
    return LighthouseClient(
        base_url=base,
        admin_token=getattr(settings, "lighthouse_admin_token", None),
    )
