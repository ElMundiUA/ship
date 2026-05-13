"""Azure DevOps Repos implementation of :class:`CodeHostGateway`."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from backend.app.integrations.gateway.code_host import (
    BlobContent,
    CodeHostGateway,
    PullRequestRef,
    RepoRef,
    RepoSummary,
)

_API_VERSION = "7.1"
_MAX_REPOS = 500


class AzureDevOpsCodeHost(CodeHostGateway):
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

    async def list_repos(self) -> list[RepoRef]:
        return [summary.ref for summary in await self.list_repo_summaries()]

    async def list_repo_summaries(self) -> list[RepoSummary]:
        projects = [self._project] if self._project else await self._list_projects()
        out: list[RepoSummary] = []
        for project in projects:
            if not project:
                continue
            path = f"/{quote(project, safe='')}/_apis/git/repositories"
            payload = (
                await self._request(
                    "GET",
                    path,
                    params={"api-version": _API_VERSION},
                )
            ).json()
            for item in payload.get("value") or []:
                if not isinstance(item, dict):
                    continue
                out.append(_summary_from_repo(self._organization, project, item))
                if len(out) >= _MAX_REPOS:
                    return out
        return out

    async def get_pull_request(self, ref: PullRequestRef) -> dict[str, Any]:
        project, repo = _project_repo(ref.repo)
        return (
            await self._request(
                "GET",
                f"/{quote(project, safe='')}/_apis/git/repositories/"
                f"{quote(repo, safe='')}/pullRequests/{ref.number}",
                params={"api-version": _API_VERSION},
            )
        ).json()

    async def list_pull_request_files(
        self, ref: PullRequestRef, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        project, repo = _project_repo(ref.repo)
        iterations = (
            await self._request(
                "GET",
                f"/{quote(project, safe='')}/_apis/git/repositories/"
                f"{quote(repo, safe='')}/pullRequests/{ref.number}/iterations",
                params={"api-version": _API_VERSION},
            )
        ).json()
        values = iterations.get("value") or []
        if not values:
            return []
        iteration_id = values[-1].get("id")
        if iteration_id is None:
            return []
        changes = (
            await self._request(
                "GET",
                f"/{quote(project, safe='')}/_apis/git/repositories/"
                f"{quote(repo, safe='')}/pullRequests/{ref.number}/iterations/"
                f"{iteration_id}/changes",
                params={"api-version": _API_VERSION, "$top": str(limit)},
            )
        ).json()
        out: list[dict[str, Any]] = []
        for item in (changes.get("changeEntries") or [])[: max(1, min(limit, 500))]:
            if not isinstance(item, dict):
                continue
            target = item.get("item") or {}
            out.append(
                {
                    "filename": str(target.get("path") or "").lstrip("/"),
                    "status": item.get("changeType"),
                    "patch": None,
                }
            )
        return out

    async def list_files(
        self, ref: RepoRef, *, ref_sha: str | None = None
    ) -> list[str]:
        project, repo = _project_repo(ref)
        params = {
            "scopePath": "/",
            "recursionLevel": "Full",
            "includeContentMetadata": "false",
            "api-version": _API_VERSION,
        }
        if ref_sha:
            params["versionDescriptor.version"] = ref_sha
        payload = (
            await self._request(
                "GET",
                f"/{quote(project, safe='')}/_apis/git/repositories/"
                f"{quote(repo, safe='')}/items",
                params=params,
            )
        ).json()
        return [
            str(item.get("path") or "").lstrip("/")
            for item in payload.get("value") or []
            if isinstance(item, dict) and not item.get("isFolder")
        ]

    async def get_blob(
        self,
        ref: RepoRef,
        *,
        path: str,
        ref_sha: str | None = None,
    ) -> BlobContent:
        project, repo = _project_repo(ref)
        params = {
            "path": f"/{path.lstrip('/')}",
            "includeContent": "true",
            "api-version": _API_VERSION,
        }
        if ref_sha:
            params["versionDescriptor.version"] = ref_sha
        try:
            payload = (
                await self._request(
                    "GET",
                    f"/{quote(project, safe='')}/_apis/git/repositories/"
                    f"{quote(repo, safe='')}/items",
                    params=params,
                )
            ).json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise FileNotFoundError(f"{ref.full_name}:{path}@{ref_sha or 'HEAD'}") from exc
            raise
        content = str(payload.get("content") or "")
        sha = str(payload.get("objectId") or payload.get("commitId") or "")
        size = int(payload.get("size") or len(content.encode("utf-8")))
        return BlobContent(
            path=path,
            ref=str(ref_sha or payload.get("commitId") or "HEAD"),
            sha=sha,
            size=size,
            encoding="utf-8",
            content=content,
        )

    async def _list_projects(self) -> list[str]:
        out: list[str] = []
        continuation: str | None = None
        while len(out) < 100:
            params = {"api-version": _API_VERSION, "$top": "100"}
            if continuation:
                params["continuationToken"] = continuation
            response = await self._request("GET", "/_apis/projects", params=params)
            payload = response.json()
            out.extend(
                str(item["name"])
                for item in payload.get("value") or []
                if isinstance(item, dict) and item.get("name")
            )
            continuation = response.headers.get("x-ms-continuationtoken")
            if not continuation:
                break
        return out


def _summary_from_repo(organization: str, project: str, item: dict[str, Any]) -> RepoSummary:
    name = str(item.get("name") or item.get("id") or "")
    default_branch = str(item.get("defaultBranch") or "refs/heads/main")
    default_branch = default_branch.removeprefix("refs/heads/")
    full_name = f"{organization}/{project}/{name}"
    return RepoSummary(
        ref=RepoRef(kind="azure_devops", owner=f"{organization}/{project}", repo=name),
        external_id=str(item.get("id") or full_name),
        full_name=full_name,
        default_branch=default_branch or "main",
        private=True,
        html_url=str(item.get("webUrl") or item.get("remoteUrl") or ""),
        description=None,
    )


def _project_repo(ref: RepoRef) -> tuple[str, str]:
    parts = ref.full_name.split("/")
    if len(parts) >= 3:
        return parts[-2], parts[-1]
    owner_parts = ref.owner.split("/")
    if len(owner_parts) >= 2:
        return owner_parts[-1], ref.repo
    raise ValueError(f"Azure DevOps repo ref {ref.full_name!r} lacks project segment")


__all__ = ["AzureDevOpsCodeHost"]
