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

        Raises ``httpx.HTTPError`` on transport / non-2xx — the search
        wrapper degrades any failure to an empty result (K8: reads are
        Lighthouse-only, so an outage means "no knowledge", not fallback).
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

    async def corpus_stats(
        self, *, workspace_id: uuid.UUID | str
    ) -> dict[str, Any]:
        """Return ``/v1/corpus/stats`` for the workspace (chunk/source
        counts, last ingest)."""
        headers = {"X-Workspace": str(workspace_id)}
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT
        ) as client:
            resp = await client.get("/v1/corpus/stats", headers=headers)
            resp.raise_for_status()
            return dict(resp.json())

    async def corpus_sources(
        self, *, workspace_id: uuid.UUID | str, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return ``/v1/corpus/sources`` for the workspace (per-source
        roll-up: chunk count, recipes, last ingest)."""
        headers = {"X-Workspace": str(workspace_id)}
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT
        ) as client:
            resp = await client.get(
                "/v1/corpus/sources",
                params={"limit": limit, "order": "recent"},
                headers=headers,
            )
            resp.raise_for_status()
            return list(resp.json())

    async def provision_s3_importer(
        self, *, workspace_id: uuid.UUID | str
    ) -> dict[str, Any]:
        """Idempotently provision the per-workspace S3 importer."""
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT
        ) as client:
            resp = await client.post(
                "/v1/importers/provision/s3",
                headers=self._admin_headers(workspace_id),
            )
            resp.raise_for_status()
            return dict(resp.json())

    # ---- importer CRUD (operator-driven, e.g. "add import source" UI) ----

    async def list_importer_types(self) -> list[dict[str, Any]]:
        """Return every importer type the engine knows about plus its
        ``config_schema`` and ``secret_keys`` — used to drive the
        "Add import source" form."""
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT
        ) as client:
            resp = await client.get(
                "/v1/importers/types", headers=self._admin_headers()
            )
            resp.raise_for_status()
            return list(resp.json())

    async def list_workspace_importers(
        self, *, workspace_id: uuid.UUID | str
    ) -> list[dict[str, Any]]:
        """List every importer registered against the workspace."""
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT
        ) as client:
            resp = await client.get(
                "/v1/importers/",
                headers=self._admin_headers(workspace_id),
            )
            resp.raise_for_status()
            return list(resp.json())

    async def create_importer(
        self,
        *,
        workspace_id: uuid.UUID | str,
        type_: str,
        name: str,
        recipe: str,
        config: dict[str, Any],
        secrets: dict[str, str] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create an importer under the workspace."""
        body: dict[str, Any] = {
            "type": type_,
            "name": name,
            "recipe": recipe,
            "config": config,
            "secrets": secrets or {},
        }
        if description is not None:
            body["description"] = description
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT
        ) as client:
            resp = await client.post(
                "/v1/importers/",
                headers=self._admin_headers(workspace_id),
                json=body,
            )
            resp.raise_for_status()
            return dict(resp.json())

    async def run_importer(
        self,
        *,
        workspace_id: uuid.UUID | str,
        importer_id: uuid.UUID | str,
    ) -> dict[str, Any]:
        """Trigger an on-demand run of the importer."""
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=_TIMEOUT
        ) as client:
            resp = await client.post(
                f"/v1/importers/{importer_id}/run",
                headers=self._admin_headers(workspace_id),
            )
            resp.raise_for_status()
            return dict(resp.json())

    # ---- helpers ----

    def _admin_headers(
        self, workspace_id: uuid.UUID | str | None = None
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if workspace_id is not None:
            headers["X-Workspace"] = str(workspace_id)
        if self._admin_token:
            headers["Authorization"] = f"Bearer {self._admin_token}"
        return headers


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
