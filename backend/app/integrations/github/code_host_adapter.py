"""GitHub implementation of :class:`CodeHostGateway`.

Backed by the App installation token, not a PAT. Each adapter instance
binds to one ``installation_id`` so call sites stay one-vendor /
one-tenant. Day-1 surface is intentionally minimal — only the methods the
default pipelines need today (`list_repos`, `get_pull_request`,
`list_files`). We bolt on more verbs when an actual pipeline starts
calling them.
"""

from __future__ import annotations

from typing import Any

import httpx

from backend.app.core.config import Settings
from backend.app.integrations.gateway.code_host import (
    CodeHostGateway,
    PullRequestRef,
    RepoRef,
)
from backend.app.integrations.github.app_auth import (
    GITHUB_API_BASE,
    fetch_installation_token,
)


class GitHubCodeHost(CodeHostGateway):
    """Per-installation gateway. Construct one per (workspace, install)."""

    def __init__(
        self,
        installation_id: int,
        *,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._installation_id = installation_id
        self._settings = settings
        # Reusing a caller-supplied client lets the FastAPI request scope
        # share its connection pool. Outside of a request (tests, scripts)
        # we open a short-lived client on each call.
        self._client = client

    async def _headers(self) -> dict[str, str]:
        token = await fetch_installation_token(
            self._installation_id, settings=self._settings, client=self._client
        )
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = await self._headers()
        url = f"{GITHUB_API_BASE}{path}"
        owns_client = self._client is None
        http = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        try:
            response = await http.request(method, url, headers=headers, **kwargs)
        finally:
            if owns_client:
                await http.aclose()
        response.raise_for_status()
        return response

    async def list_repos(self) -> list[RepoRef]:
        # ``/installation/repositories`` returns the repos the App has
        # access to under the *current* installation token — this is the
        # canonical "what can we see" call.
        response = await self._request(
            "GET", "/installation/repositories", params={"per_page": 100}
        )
        payload = response.json()
        return [
            RepoRef(
                kind="github",
                owner=item["owner"]["login"],
                repo=item["name"],
            )
            for item in payload.get("repositories", [])
        ]

    async def get_pull_request(self, ref: PullRequestRef) -> dict[str, Any]:
        response = await self._request(
            "GET",
            f"/repos/{ref.repo.owner}/{ref.repo.repo}/pulls/{ref.number}",
        )
        return response.json()

    async def list_files(
        self, ref: RepoRef, *, ref_sha: str | None = None
    ) -> list[str]:
        # Use Trees API recursive=true; cap to 5_000 paths upstream so we
        # don't hand a 200 MB JSON document to the LLM. The tree itself is
        # rooted at ``ref_sha`` if given else HEAD of the default branch.
        if ref_sha is None:
            repo_info = (await self._request(
                "GET", f"/repos/{ref.owner}/{ref.repo}"
            )).json()
            ref_sha = repo_info.get("default_branch", "main")
        response = await self._request(
            "GET",
            f"/repos/{ref.owner}/{ref.repo}/git/trees/{ref_sha}",
            params={"recursive": "1"},
        )
        tree = response.json().get("tree", [])
        return [entry["path"] for entry in tree if entry.get("type") == "blob"]


__all__ = ["GitHubCodeHost"]
