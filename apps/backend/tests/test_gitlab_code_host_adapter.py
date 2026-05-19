"""Unit tests for :class:`GitLabCodeHost` — mocked httpx, no live GitLab."""

from __future__ import annotations

import base64

import httpx
import pytest

from backend.app.integrations.gateway.code_host import BlobContent, PullRequestRef, RepoRef
from backend.app.integrations.gitlab.code_host_adapter import (
    GitLabCodeHost,
    _change_status,
    _encoded_project,
    _summary_from_project,
)

_BASE_URL = "https://gitlab.example.com"
_PAT = "glpat-test"
_REPO = RepoRef(kind="gitlab", owner="acme", repo="widget")


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, matcher):
        self._matcher = matcher
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._matcher(request)


def _client(matcher) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=_MockTransport(matcher),
        base_url=_BASE_URL,
    )


def _adapter(
    client: httpx.AsyncClient,
    *,
    group: str | None = None,
) -> GitLabCodeHost:
    return GitLabCodeHost(base_url=_BASE_URL, pat=_PAT, group=group, client=client)


@pytest.mark.asyncio
async def test_list_repo_summaries_membership_path() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/v4/projects"
        assert request.url.params.get("membership") == "true"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "path_with_namespace": "acme/widget",
                    "default_branch": "main",
                    "visibility": "private",
                    "web_url": "https://gitlab.example.com/acme/widget",
                }
            ],
        )

    async with _client(match) as client:
        summaries = await _adapter(client).list_repo_summaries()

    assert len(summaries) == 1
    assert summaries[0].ref.kind == "gitlab"
    assert summaries[0].full_name == "acme/widget"
    assert summaries[0].private is True


@pytest.mark.asyncio
async def test_list_repo_summaries_group_path() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        assert "org%2Fteam" in str(request.url)
        assert request.url.path.endswith("/groups/org/team/projects")
        assert request.url.params.get("include_subgroups") == "true"
        return httpx.Response(200, json=[])

    async with _client(match) as client:
        await _adapter(client, group="org/team").list_repo_summaries()


@pytest.mark.asyncio
async def test_list_repo_summaries_pagination_stops() -> None:
    page_calls: list[str] = []

    def _project(i: int) -> dict:
        return {
            "id": i,
            "path_with_namespace": f"acme/repo{i}",
            "default_branch": "main",
            "visibility": "public",
            "web_url": f"https://gitlab.example.com/acme/repo{i}",
        }

    def match(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        page_calls.append(page)
        if page == "1":
            return httpx.Response(200, json=[_project(i) for i in range(100)])
        if page == "2":
            return httpx.Response(200, json=[_project(100)])
        raise AssertionError(f"unexpected page {page}")

    transport = _MockTransport(match)
    async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:
        summaries = await _adapter(client).list_repo_summaries()

    assert len(summaries) == 101
    assert page_calls == ["1", "2"]


@pytest.mark.asyncio
async def test_list_repo_summaries_max_repos_cap() -> None:
    page_calls: list[str] = []

    def _project(i: int) -> dict:
        return {
            "id": i,
            "path_with_namespace": f"ns/repo{i}",
            "default_branch": "main",
            "visibility": "public",
            "web_url": f"https://gitlab.example.com/ns/repo{i}",
        }

    def match(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        page_calls.append(str(page))
        start = (page - 1) * 100
        batch = [_project(start + i) for i in range(100)]
        return httpx.Response(200, json=batch)

    transport = _MockTransport(match)
    async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:
        summaries = await _adapter(client).list_repo_summaries()

    assert len(summaries) == 500
    assert page_calls == ["1", "2", "3", "4", "5"]


@pytest.mark.asyncio
async def test_list_repo_summaries_skips_malformed() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "path_with_namespace": "acme/good",
                    "default_branch": "main",
                    "visibility": "public",
                    "web_url": "https://x",
                },
                "bad",
            ],
        )

    async with _client(match) as client:
        summaries = await _adapter(client).list_repo_summaries()

    assert len(summaries) == 1


@pytest.mark.asyncio
async def test_get_pull_request_encoded_project() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        assert "acme%2Fwidget" in str(request.url)
        assert request.url.path.endswith("/merge_requests/5")
        return httpx.Response(200, json={"iid": 5, "title": "Fix widget"})

    pr_ref = PullRequestRef(repo=_REPO, number=5)
    async with _client(match) as client:
        payload = await _adapter(client).get_pull_request(pr_ref)

    assert payload["title"] == "Fix widget"


@pytest.mark.asyncio
async def test_list_merge_request_files_change_statuses() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "changes": [
                    {"new_path": "a.py", "new_file": True},
                    {"new_path": "b.py", "deleted_file": True},
                    {"new_path": "c.py", "renamed_file": True, "old_path": "old.py"},
                    {"new_path": "d.py"},
                    "skip-me",
                ]
            },
        )

    pr_ref = PullRequestRef(repo=_REPO, number=1)
    async with _client(match) as client:
        files = await _adapter(client).list_merge_request_files(pr_ref)

    assert [f["status"] for f in files] == ["added", "removed", "renamed", "modified"]
    assert len(files) == 4


@pytest.mark.asyncio
async def test_list_files_blob_only_and_ref() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        assert "acme%2Fwidget" in str(request.url)
        assert request.url.path.endswith("/repository/tree")
        assert request.url.params.get("recursive") == "true"
        assert request.url.params.get("per_page") == "100"
        assert request.url.params.get("ref") == "deadbeef"
        return httpx.Response(
            200,
            json=[
                {"path": "src/main.py", "type": "blob"},
                {"path": "src", "type": "tree"},
            ],
        )

    async with _client(match) as client:
        paths = await _adapter(client).list_files(_REPO, ref_sha="deadbeef")

    assert paths == ["src/main.py"]


@pytest.mark.asyncio
async def test_list_files_pagination() -> None:
    pages_seen: list[str] = []

    def match(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        pages_seen.append(page)
        if page == "1":
            return httpx.Response(
                200,
                json=[{"path": f"f{i}.py", "type": "blob"} for i in range(100)],
            )
        return httpx.Response(
            200,
            json=[{"path": "last.py", "type": "blob"}],
        )

    async with _client(match) as client:
        paths = await _adapter(client).list_files(_REPO)

    assert len(paths) == 101
    assert pages_seen == ["1", "2"]


@pytest.mark.asyncio
async def test_get_blob_utf8_decode() -> None:
    text = "hello world"
    encoded = base64.b64encode(text.encode()).decode()

    def match(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": encoded,
                "encoding": "base64",
                "size": len(text),
                "blob_id": "sha1",
            },
        )

    async with _client(match) as client:
        blob = await _adapter(client).get_blob(_REPO, path="README.md", ref_sha="main")

    assert isinstance(blob, BlobContent)
    assert blob.encoding == "utf-8"
    assert blob.content == text


@pytest.mark.asyncio
async def test_get_blob_binary_fallback() -> None:
    raw_bytes = bytes([0xFF, 0xFE, 0x00, 0x01])
    encoded = base64.b64encode(raw_bytes).decode()

    def match(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": encoded,
                "encoding": "base64",
                "size": len(raw_bytes),
                "blob_id": "bin",
            },
        )

    async with _client(match) as client:
        blob = await _adapter(client).get_blob(_REPO, path="bin.dat", ref_sha="main")

    assert blob.encoding == "base64"
    assert blob.content == encoded


@pytest.mark.asyncio
async def test_get_blob_404_raises_file_not_found() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with _client(match) as client:
        with pytest.raises(FileNotFoundError, match=r"acme/widget:missing\.py@main"):
            await _adapter(client).get_blob(_REPO, path="missing.py", ref_sha="main")


@pytest.mark.asyncio
async def test_get_blob_default_branch_when_no_ref() -> None:
    calls: list[httpx.Request] = []

    def match(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/v4/projects/acme/widget":
            return httpx.Response(200, json={"default_branch": "develop"})
        if "/repository/files/" in request.url.path:
            content = base64.b64encode(b"file body").decode()
            return httpx.Response(
                200,
                json={"content": content, "encoding": "base64", "size": 9, "blob_id": "x"},
            )
        raise AssertionError(request.url.path)

    async with _client(match) as client:
        blob = await _adapter(client).get_blob(_REPO, path="src/app.py")

    assert calls[0].url.path == "/api/v4/projects/acme/widget"
    assert "acme%2Fwidget" in str(calls[0].url)
    assert blob.ref == "develop"
    assert blob.content == "file body"


@pytest.mark.asyncio
async def test_summary_from_project_parsing() -> None:
    summary = _summary_from_project(
        {
            "id": 99,
            "path_with_namespace": "",
            "namespace": {"full_path": "acme"},
            "path": "widget",
            "default_branch": "main",
            "visibility": "public",
            "web_url": "https://gitlab.example.com/acme/widget",
            "description": "A widget",
        }
    )
    assert summary.ref.owner == "acme"
    assert summary.ref.repo == "widget"
    assert summary.full_name == "acme/widget"
    assert summary.private is False
    assert summary.external_id == 99


def test_encoded_project_slashes() -> None:
    assert _encoded_project(_REPO) == "acme%2Fwidget"


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"new_file": True}, "added"),
        ({"deleted_file": True}, "removed"),
        ({"renamed_file": True}, "renamed"),
        ({}, "modified"),
    ],
)
def test_change_status_mapping(item: dict, expected: str) -> None:
    assert _change_status(item) == expected
