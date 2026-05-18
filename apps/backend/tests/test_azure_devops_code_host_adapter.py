"""Unit tests for :class:`AzureDevOpsCodeHost`."""

from __future__ import annotations

import base64

import httpx
import pytest

from backend.app.integrations.azure_devops.code_host_adapter import (
    AzureDevOpsCodeHost,
    _project_repo,
    _summary_from_repo,
)
from backend.app.integrations.gateway.code_host import BlobContent, PullRequestRef, RepoRef

ORG = "contoso"
PAT = "test-pat"
PROJECT = "MyProject"

REPO = RepoRef(kind="azure_devops", owner=f"{ORG}/{PROJECT}", repo="app")
PR_REF = PullRequestRef(repo=REPO, number=12)


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, matcher):
        self._matcher = matcher
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._matcher(request)


def _assert_ado_request(request: httpx.Request, *, pat: str = PAT) -> None:
    assert request.url.host == "dev.azure.com"
    assert request.url.params.get("api-version") == "7.1"
    auth = request.headers.get("Authorization", "")
    assert auth.startswith("Basic ")
    decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("latin-1")
    username, _, password = decoded.partition(":")
    assert username == ""
    assert password == pat


@pytest.mark.asyncio
async def test_list_repo_summaries_with_fixed_project() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        assert request.method == "GET"
        assert request.url.path == f"/{ORG}/{PROJECT}/_apis/git/repositories"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "repo-guid",
                        "name": "app",
                        "defaultBranch": "refs/heads/develop",
                        "webUrl": "https://dev.azure.com/contoso/MyProject/_git/app",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        host = AzureDevOpsCodeHost(
            organization=ORG, pat=PAT, project=PROJECT, client=client
        )
        summaries = await host.list_repo_summaries()

    assert len(summaries) == 1
    assert summaries[0].full_name == f"{ORG}/{PROJECT}/app"
    assert summaries[0].default_branch == "develop"
    assert summaries[0].ref.repo == "app"


@pytest.mark.asyncio
async def test_list_repo_summaries_paginates_projects() -> None:
    calls: list[str] = []

    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        calls.append(request.url.path)
        if request.url.path == f"/{ORG}/_apis/projects":
            token = request.url.params.get("continuationToken")
            if token is None:
                return httpx.Response(
                    200,
                    json={"value": [{"name": "ProjA"}]},
                    headers={"x-ms-continuationtoken": "page2"},
                )
            assert token == "page2"
            return httpx.Response(200, json={"value": [{"name": "ProjB"}]})
        if request.url.path == f"/{ORG}/ProjA/_apis/git/repositories":
            return httpx.Response(
                200, json={"value": [{"name": "repo-a", "defaultBranch": "refs/heads/main"}]}
            )
        if request.url.path == f"/{ORG}/ProjB/_apis/git/repositories":
            return httpx.Response(
                200, json={"value": [{"name": "repo-b", "defaultBranch": "refs/heads/main"}]}
            )
        raise AssertionError(request.url.path)

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        host = AzureDevOpsCodeHost(organization=ORG, pat=PAT, client=client)
        summaries = await host.list_repo_summaries()

    assert calls.count(f"/{ORG}/_apis/projects") == 2
    names = {s.ref.repo for s in summaries}
    assert names == {"repo-a", "repo-b"}


@pytest.mark.asyncio
async def test_list_repos_delegates_to_summaries() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        return httpx.Response(
            200,
            json={"value": [{"name": "app", "defaultBranch": "refs/heads/main"}]},
        )

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        host = AzureDevOpsCodeHost(
            organization=ORG, pat=PAT, project=PROJECT, client=client
        )
        repos = await host.list_repos()

    assert len(repos) == 1
    assert repos[0].full_name == f"{ORG}/{PROJECT}/app"
    assert repos[0].kind == "azure_devops"


def test_summary_from_repo_strips_refs_heads() -> None:
    summary = _summary_from_repo(
        ORG,
        PROJECT,
        {"name": "app", "defaultBranch": "refs/heads/feature/x", "id": "guid"},
    )
    assert summary.default_branch == "feature/x"
    assert summary.full_name == f"{ORG}/{PROJECT}/app"


@pytest.mark.asyncio
async def test_get_pull_request_returns_json() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        assert request.method == "GET"
        assert (
            request.url.path
            == f"/{ORG}/{PROJECT}/_apis/git/repositories/app/pullRequests/12"
        )
        return httpx.Response(200, json={"pullRequestId": 12, "title": "Fix bug"})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        host = AzureDevOpsCodeHost(organization=ORG, pat=PAT, client=client)
        pr = await host.get_pull_request(PR_REF)

    assert pr["pullRequestId"] == 12
    assert pr["title"] == "Fix bug"


@pytest.mark.asyncio
async def test_list_pull_request_files_no_iterations() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        assert request.url.path.endswith("/iterations")
        return httpx.Response(200, json={"value": []})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        host = AzureDevOpsCodeHost(organization=ORG, pat=PAT, client=client)
        files = await host.list_pull_request_files(PR_REF)

    assert files == []


@pytest.mark.asyncio
async def test_list_pull_request_files_uses_last_iteration() -> None:
    paths: list[str] = []

    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        paths.append(request.url.path)
        if request.url.path.endswith("/iterations"):
            return httpx.Response(
                200,
                json={"value": [{"id": 1}, {"id": 3}]},
            )
        assert request.url.path.endswith("/iterations/3/changes")
        return httpx.Response(
            200,
            json={
                "changeEntries": [
                    {"item": {"path": "/src/main.py"}, "changeType": "edit"},
                ]
            },
        )

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        host = AzureDevOpsCodeHost(organization=ORG, pat=PAT, client=client)
        files = await host.list_pull_request_files(PR_REF, limit=50)

    assert any(p.endswith("/iterations/3/changes") for p in paths)
    assert files == [{"filename": "src/main.py", "status": "edit", "patch": None}]


@pytest.mark.asyncio
async def test_list_files_returns_non_folder_paths() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        assert request.url.path.endswith("/items")
        assert request.url.params["scopePath"] == "/"
        assert request.url.params["recursionLevel"] == "Full"
        return httpx.Response(
            200,
            json={
                "value": [
                    {"path": "/README.md", "isFolder": False},
                    {"path": "/src", "isFolder": True},
                    {"path": "/src/app.py", "isFolder": False},
                ]
            },
        )

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        host = AzureDevOpsCodeHost(organization=ORG, pat=PAT, client=client)
        paths = await host.list_files(REPO)

    assert paths == ["README.md", "src/app.py"]


@pytest.mark.asyncio
async def test_get_blob_success() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        assert request.url.params["includeContent"] == "true"
        assert request.url.params["path"] == "/README.md"
        return httpx.Response(
            200,
            json={
                "content": "hello",
                "objectId": "sha-1",
                "size": 5,
                "commitId": "commit-abc",
            },
        )

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        host = AzureDevOpsCodeHost(organization=ORG, pat=PAT, client=client)
        blob = await host.get_blob(REPO, path="README.md", ref_sha="commit-abc")

    assert isinstance(blob, BlobContent)
    assert blob.path == "README.md"
    assert blob.content == "hello"
    assert blob.sha == "sha-1"
    assert blob.ref == "commit-abc"


@pytest.mark.asyncio
async def test_get_blob_404_raises_file_not_found() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        return httpx.Response(404, json={"message": "not found"})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        host = AzureDevOpsCodeHost(organization=ORG, pat=PAT, client=client)
        with pytest.raises(FileNotFoundError, match=f"{REPO.full_name}:missing.md@HEAD"):
            await host.get_blob(REPO, path="missing.md")

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        host = AzureDevOpsCodeHost(organization=ORG, pat=PAT, client=client)
        with pytest.raises(
            FileNotFoundError, match=f"{REPO.full_name}:missing.md@deadbeef"
        ):
            await host.get_blob(REPO, path="missing.md", ref_sha="deadbeef")


@pytest.mark.asyncio
async def test_get_blob_500_propagates_http_status_error() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        return httpx.Response(500, json={"message": "server error"})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        host = AzureDevOpsCodeHost(organization=ORG, pat=PAT, client=client)
        with pytest.raises(httpx.HTTPStatusError):
            await host.get_blob(REPO, path="README.md")


def test_project_repo_invalid_ref_raises() -> None:
    bad = RepoRef(kind="azure_devops", owner=ORG, repo="app")
    with pytest.raises(ValueError, match="lacks project segment"):
        _project_repo(bad)
