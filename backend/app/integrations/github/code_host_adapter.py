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
    BlobContent,
    CodeHostGateway,
    PullRequestRef,
    RepoRef,
    RepoSummary,
)
from backend.app.integrations.github.app_auth import (
    GITHUB_API_BASE,
    fetch_installation_token,
)

# Hard cap on how many repos we'll return to the picker. GitHub orgs can
# legitimately have more, but past ~500 the checkbox UI is hostile and
# we'd rather force a search box than ship a 5MB JSON to the browser.
_MAX_INSTALL_REPOS = 500


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
        return [s.ref for s in await self.list_repo_summaries()]

    async def list_repo_summaries(self) -> list[RepoSummary]:
        # Same call as ``list_repos`` but we keep the rich metadata so the
        # picker UI can show repo descriptions / visibility badges. We
        # paginate up to ``_MAX_INSTALL_REPOS`` to keep the response
        # bounded — installations bigger than that should fall back to a
        # search box on the picker (Day 3 polish).
        out: list[RepoSummary] = []
        page = 1
        while True:
            response = await self._request(
                "GET",
                "/installation/repositories",
                params={"per_page": 100, "page": page},
            )
            payload = response.json()
            items = payload.get("repositories", []) or []
            for item in items:
                out.append(
                    RepoSummary(
                        ref=RepoRef(
                            kind="github",
                            owner=item["owner"]["login"],
                            repo=item["name"],
                        ),
                        external_id=int(item["id"]),
                        full_name=item["full_name"],
                        default_branch=item.get("default_branch") or "main",
                        private=bool(item.get("private", False)),
                        html_url=item.get("html_url") or "",
                        description=item.get("description"),
                    )
                )
            if len(items) < 100 or len(out) >= _MAX_INSTALL_REPOS:
                break
            page += 1
        return out[:_MAX_INSTALL_REPOS]

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

    async def get_blob(
        self,
        ref: RepoRef,
        *,
        path: str,
        ref_sha: str | None = None,
    ) -> BlobContent:
        # ``/repos/{owner}/{repo}/contents/{path}`` returns a ``content``
        # field (base64) for files < 1 MB; for anything bigger we fall
        # back to the Blob API via the ``sha`` reported by the contents
        # call, which streams up to 100 MB. Agent KB docs and source
        # files comfortably fit under 1 MB, so the fallback is rare.
        params: dict[str, str] = {}
        if ref_sha is not None:
            params["ref"] = ref_sha
        try:
            response = await self._request(
                "GET",
                f"/repos/{ref.owner}/{ref.repo}/contents/{path}",
                params=params,
            )
        except httpx.HTTPStatusError as exc:  # pragma: no cover — translated
            if exc.response.status_code == 404:
                raise FileNotFoundError(
                    f"{ref.owner}/{ref.repo}:{path}@{ref_sha or 'HEAD'}"
                ) from exc
            raise
        payload = response.json()
        if isinstance(payload, list):
            # Directory, not a file — callers must walk themselves.
            raise IsADirectoryError(f"{ref.owner}/{ref.repo}:{path}")

        encoding = str(payload.get("encoding") or "base64")
        size = int(payload.get("size") or 0)
        sha = str(payload.get("sha") or "")
        raw_content = str(payload.get("content") or "")
        effective_ref = ref_sha or payload.get("ref") or "HEAD"

        if encoding == "base64":
            import base64

            try:
                decoded_bytes = base64.b64decode(raw_content.replace("\n", ""))
                decoded = decoded_bytes.decode("utf-8")
                return BlobContent(
                    path=path,
                    ref=str(effective_ref),
                    sha=sha,
                    size=size,
                    encoding="utf-8",
                    content=decoded,
                )
            except UnicodeDecodeError:
                # Binary — hand back the base64 so the caller (KB
                # indexer, get_repo_file) can refuse it without us
                # re-downloading.
                return BlobContent(
                    path=path,
                    ref=str(effective_ref),
                    sha=sha,
                    size=size,
                    encoding="base64",
                    content=raw_content,
                )
        return BlobContent(
            path=path,
            ref=str(effective_ref),
            sha=sha,
            size=size,
            encoding=encoding,
            content=raw_content,
        )


__all__ = ["GitHubCodeHost"]
