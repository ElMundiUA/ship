"""GitLab CI implementation of :class:`CIGateway`."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from backend.app.integrations.gateway.ci import CIGateway
from backend.app.integrations.gateway.code_host import RepoRef


class GitLabCI(CIGateway):
    def __init__(
        self,
        *,
        base_url: str,
        pat: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._pat = pat
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json", "PRIVATE-TOKEN": self._pat}

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        owns_client = self._client is None
        http = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        try:
            response = await http.request(
                method,
                f"{self._base_url}{path}",
                headers=self._headers(),
                **kwargs,
            )
        finally:
            if owns_client:
                await http.aclose()
        response.raise_for_status()
        return response

    async def list_runs(
        self, repo: RepoRef, *, limit: int = 25
    ) -> list[dict[str, Any]]:
        project = quote(repo.full_name, safe="")
        cap = max(1, min(limit, 100))
        items = (
            await self._request(
                "GET",
                f"/api/v4/projects/{project}/pipelines",
                params={"per_page": str(cap)},
            )
        ).json() or []
        out: list[dict[str, Any]] = []
        for item in items[:cap]:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "id": item.get("id"),
                    "name": item.get("ref"),
                    "status": item.get("status"),
                    "conclusion": _gitlab_conclusion(item.get("status")),
                    "url": item.get("web_url"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "ref": item.get("ref"),
                    "sha": item.get("sha"),
                }
            )
        return out

    async def rerun(self, repo: RepoRef, *, run_id: str | int) -> None:
        project = quote(repo.full_name, safe="")
        await self._request(
            "POST",
            f"/api/v4/projects/{project}/pipelines/{run_id}/retry",
        )

    async def get_logs(self, repo: RepoRef, *, run_id: str | int) -> str:
        project = quote(repo.full_name, safe="")
        jobs = (
            await self._request(
                "GET",
                f"/api/v4/projects/{project}/pipelines/{run_id}/jobs",
                params={"per_page": "100"},
            )
        ).json() or []
        chunks: list[str] = []
        for job in jobs:
            if not isinstance(job, dict) or job.get("id") is None:
                continue
            trace = (
                await self._request(
                    "GET",
                    f"/api/v4/projects/{project}/jobs/{job['id']}/trace",
                )
            ).text
            chunks.append(f"===== {job.get('name') or job['id']} =====\n{trace}")
        return "\n\n".join(chunks)


def _gitlab_conclusion(status: object) -> str | None:
    value = str(status or "").lower()
    if value == "success":
        return "success"
    if value in {"failed", "canceled", "skipped"}:
        return value
    return None


__all__ = ["GitLabCI"]
