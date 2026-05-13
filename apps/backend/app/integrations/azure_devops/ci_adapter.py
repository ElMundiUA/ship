"""Azure Pipelines implementation of :class:`CIGateway`."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from backend.app.integrations.gateway.ci import CIGateway
from backend.app.integrations.gateway.code_host import RepoRef

_API_VERSION = "7.1"


class AzureDevOpsPipelines(CIGateway):
    def __init__(
        self,
        *,
        organization: str,
        pat: str,
        project: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._organization = organization
        self._project = project
        self._pat = pat
        self._client = client
        self._base_url = f"https://dev.azure.com/{quote(organization, safe='')}"

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        owns_client = self._client is None
        http = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        try:
            response = await http.request(
                method,
                f"{self._base_url}{path}",
                auth=("", self._pat),
                headers={"Accept": "application/json"},
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
        project, repo_name = _project_repo(repo, self._project)
        cap = max(1, min(limit, 100))
        payload = (
            await self._request(
                "GET",
                f"/{quote(project, safe='')}/_apis/build/builds",
                params={
                    "api-version": _API_VERSION,
                    "repositoryName": repo_name,
                    "$top": str(cap),
                    "queryOrder": "finishTimeDescending",
                },
            )
        ).json()
        out: list[dict[str, Any]] = []
        for item in (payload.get("value") or [])[:cap]:
            if not isinstance(item, dict):
                continue
            definition = item.get("definition") or {}
            out.append(
                {
                    "id": item.get("id"),
                    "name": definition.get("name") or item.get("buildNumber"),
                    "status": item.get("status"),
                    "conclusion": item.get("result"),
                    "url": (item.get("_links") or {}).get("web", {}).get("href"),
                    "created_at": item.get("queueTime") or item.get("startTime"),
                    "finished_at": item.get("finishTime"),
                    "source_branch": item.get("sourceBranch"),
                    "source_version": item.get("sourceVersion"),
                }
            )
        return out

    async def rerun(self, repo: RepoRef, *, run_id: str | int) -> None:
        project, _repo_name = _project_repo(repo, self._project)
        build = (
            await self._request(
                "GET",
                f"/{quote(project, safe='')}/_apis/build/builds/{run_id}",
                params={"api-version": _API_VERSION},
            )
        ).json()
        definition = build.get("definition") or {}
        definition_id = definition.get("id")
        if definition_id is None:
            raise ValueError(f"Azure build {run_id} has no definition id to rerun")
        await self._request(
            "POST",
            f"/{quote(project, safe='')}/_apis/build/builds",
            params={"api-version": _API_VERSION},
            json={
                "definition": {"id": definition_id},
                "sourceBranch": build.get("sourceBranch"),
                "sourceVersion": build.get("sourceVersion"),
            },
        )

    async def get_logs(self, repo: RepoRef, *, run_id: str | int) -> str:
        project, _repo_name = _project_repo(repo, self._project)
        payload = (
            await self._request(
                "GET",
                f"/{quote(project, safe='')}/_apis/build/builds/{run_id}/logs",
                params={"api-version": _API_VERSION},
            )
        ).json()
        chunks: list[str] = []
        for log in payload.get("value") or []:
            if not isinstance(log, dict) or log.get("id") is None:
                continue
            response = await self._request(
                "GET",
                f"/{quote(project, safe='')}/_apis/build/builds/{run_id}/logs/{log['id']}",
                params={"api-version": _API_VERSION},
            )
            chunks.append(f"===== {log.get('type') or log['id']} =====\n{response.text}")
        return "\n\n".join(chunks)


def _project_repo(ref: RepoRef, fallback_project: str | None) -> tuple[str, str]:
    parts = ref.full_name.split("/")
    if len(parts) >= 3:
        return parts[-2], parts[-1]
    if fallback_project:
        return fallback_project, ref.repo
    owner_parts = ref.owner.split("/")
    if len(owner_parts) >= 2:
        return owner_parts[-1], ref.repo
    raise ValueError(f"Azure DevOps repo ref {ref.full_name!r} lacks project segment")


__all__ = ["AzureDevOpsPipelines"]
