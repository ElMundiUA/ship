"""GitLab implementation of :class:`CodeHostGateway` backed by a PAT."""

from __future__ import annotations

import base64
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

_MAX_REPOS = 500


class GitLabCodeHost(CodeHostGateway):
    """Per-installation GitLab gateway."""

    def __init__(
        self,
        *,
        base_url: str,
        pat: str,
        group: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._pat = pat
        self._group = group.strip("/") if group else None
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "PRIVATE-TOKEN": self._pat,
        }

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

    async def list_repos(self) -> list[RepoRef]:
        return [summary.ref for summary in await self.list_repo_summaries()]

    async def list_repo_summaries(self) -> list[RepoSummary]:
        out: list[RepoSummary] = []
        page = 1
        while len(out) < _MAX_REPOS:
            if self._group:
                path = f"/api/v4/groups/{quote(self._group, safe='')}/projects"
                params = {
                    "include_subgroups": "true",
                    "order_by": "last_activity_at",
                    "sort": "desc",
                    "per_page": "100",
                    "page": str(page),
                }
            else:
                path = "/api/v4/projects"
                params = {
                    "membership": "true",
                    "order_by": "last_activity_at",
                    "sort": "desc",
                    "per_page": "100",
                    "page": str(page),
                }
            batch = (await self._request("GET", path, params=params)).json() or []
            if not isinstance(batch, list) or not batch:
                break
            for item in batch:
                if not isinstance(item, dict):
                    continue
                out.append(_summary_from_project(item))
                if len(out) >= _MAX_REPOS:
                    break
            if len(batch) < 100:
                break
            page += 1
        return out

    async def get_pull_request(self, ref: PullRequestRef) -> dict[str, Any]:
        project = _encoded_project(ref.repo)
        return (
            await self._request(
                "GET",
                f"/api/v4/projects/{project}/merge_requests/{ref.number}",
            )
        ).json()

    async def list_merge_request_files(
        self, ref: PullRequestRef, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        project = _encoded_project(ref.repo)
        payload = (
            await self._request(
                "GET",
                f"/api/v4/projects/{project}/merge_requests/{ref.number}/changes",
            )
        ).json()
        changes = payload.get("changes") or []
        out: list[dict[str, Any]] = []
        for item in changes[: max(1, min(limit, 500))]:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "filename": item.get("new_path"),
                    "previous_filename": item.get("old_path"),
                    "status": _change_status(item),
                    "patch": item.get("diff"),
                }
            )
        return out

    async def list_files(
        self, ref: RepoRef, *, ref_sha: str | None = None
    ) -> list[str]:
        project = _encoded_project(ref)
        out: list[str] = []
        page = 1
        while True:
            params = {
                "recursive": "true",
                "per_page": "100",
                "page": str(page),
            }
            if ref_sha:
                params["ref"] = ref_sha
            batch = (
                await self._request(
                    "GET",
                    f"/api/v4/projects/{project}/repository/tree",
                    params=params,
                )
            ).json() or []
            if not isinstance(batch, list) or not batch:
                break
            out.extend(
                str(item["path"])
                for item in batch
                if isinstance(item, dict) and item.get("type") == "blob"
            )
            if len(batch) < 100:
                break
            page += 1
        return out

    async def get_blob(
        self,
        ref: RepoRef,
        *,
        path: str,
        ref_sha: str | None = None,
    ) -> BlobContent:
        project = _encoded_project(ref)
        effective_ref = ref_sha or await self._default_branch(ref)
        try:
            payload = (
                await self._request(
                    "GET",
                    f"/api/v4/projects/{project}/repository/files/{quote(path, safe='')}",
                    params={"ref": effective_ref},
                )
            ).json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise FileNotFoundError(f"{ref.full_name}:{path}@{effective_ref}") from exc
            raise
        raw_content = str(payload.get("content") or "")
        encoding = str(payload.get("encoding") or "base64")
        size = int(payload.get("size") or 0)
        blob_id = str(payload.get("blob_id") or payload.get("content_sha256") or "")
        if encoding == "base64":
            try:
                decoded = base64.b64decode(raw_content).decode("utf-8")
                return BlobContent(path, effective_ref, blob_id, size, "utf-8", decoded)
            except UnicodeDecodeError:
                return BlobContent(path, effective_ref, blob_id, size, "base64", raw_content)
        return BlobContent(path, effective_ref, blob_id, size, encoding, raw_content)

    async def _default_branch(self, ref: RepoRef) -> str:
        payload = (
            await self._request("GET", f"/api/v4/projects/{_encoded_project(ref)}")
        ).json()
        return str(payload.get("default_branch") or "main")


def _summary_from_project(item: dict[str, Any]) -> RepoSummary:
    path_with_namespace = str(item.get("path_with_namespace") or "")
    namespace, _, name = path_with_namespace.rpartition("/")
    if not namespace:
        namespace = str((item.get("namespace") or {}).get("full_path") or "")
    if not name:
        name = str(item.get("path") or item.get("name") or "")
    return RepoSummary(
        ref=RepoRef(kind="gitlab", owner=namespace, repo=name),
        external_id=int(item["id"]),
        full_name=path_with_namespace or f"{namespace}/{name}",
        default_branch=str(item.get("default_branch") or "main"),
        private=str(item.get("visibility") or "private") != "public",
        html_url=str(item.get("web_url") or ""),
        description=item.get("description"),
    )


def _encoded_project(ref: RepoRef) -> str:
    return quote(ref.full_name, safe="")


def _change_status(item: dict[str, Any]) -> str:
    if item.get("new_file"):
        return "added"
    if item.get("deleted_file"):
        return "removed"
    if item.get("renamed_file"):
        return "renamed"
    return "modified"


__all__ = ["GitLabCodeHost"]
