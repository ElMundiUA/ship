"""Unit tests for :class:`AzureDevOpsPipelines`."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from backend.app.integrations.azure_devops.ci_adapter import (
    AzureDevOpsPipelines,
    _project_repo,
)
from backend.app.integrations.gateway.code_host import RepoRef

ORG = "contoso"
PAT = "test-pat"
PROJECT = "MyProject"

REPO = RepoRef(kind="azure_devops", owner=f"{ORG}/{PROJECT}", repo="app")


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
async def test_list_runs_normalizes_builds() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        assert request.method == "GET"
        assert request.url.path == f"/{ORG}/{PROJECT}/_apis/build/builds"
        assert request.url.params["repositoryName"] == "app"
        assert request.url.params["$top"] == "25"
        assert request.url.params["queryOrder"] == "finishTimeDescending"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": 42,
                        "buildNumber": "20260518.1",
                        "status": "completed",
                        "result": "succeeded",
                        "definition": {"name": "CI"},
                        "_links": {"web": {"href": "https://dev.azure.com/build/42"}},
                        "queueTime": "2026-05-18T10:00:00Z",
                        "finishTime": "2026-05-18T10:05:00Z",
                        "sourceBranch": "refs/heads/main",
                        "sourceVersion": "abc123",
                    },
                    {
                        "id": 41,
                        "buildNumber": "fallback-name",
                        "status": "completed",
                        "result": "failed",
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        adapter = AzureDevOpsPipelines(organization=ORG, pat=PAT, client=client)
        runs = await adapter.list_runs(REPO, limit=25)

    assert len(runs) == 2
    assert runs[0]["id"] == 42
    assert runs[0]["name"] == "CI"
    assert runs[0]["conclusion"] == "succeeded"
    assert runs[0]["url"] == "https://dev.azure.com/build/42"
    assert runs[1]["name"] == "fallback-name"


@pytest.mark.asyncio
async def test_list_runs_clamps_limit_low() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        assert request.url.params["$top"] == "1"
        return httpx.Response(200, json={"value": [{"id": 1, "buildNumber": "1"}]})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        adapter = AzureDevOpsPipelines(organization=ORG, pat=PAT, client=client)
        runs = await adapter.list_runs(REPO, limit=0)

    assert len(runs) <= 1


@pytest.mark.asyncio
async def test_list_runs_clamps_limit_high() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        assert request.url.params["$top"] == "100"
        items = [{"id": i, "buildNumber": str(i)} for i in range(150)]
        return httpx.Response(200, json={"value": items})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        adapter = AzureDevOpsPipelines(organization=ORG, pat=PAT, client=client)
        runs = await adapter.list_runs(REPO, limit=500)

    assert len(runs) == 100


@pytest.mark.asyncio
async def test_rerun_posts_definition_from_build() -> None:
    seen: list[str] = []

    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        seen.append(request.method)
        if request.method == "GET":
            assert request.url.path == f"/{ORG}/{PROJECT}/_apis/build/builds/99"
            return httpx.Response(
                200,
                json={
                    "definition": {"id": 7},
                    "sourceBranch": "refs/heads/main",
                    "sourceVersion": "deadbeef",
                },
            )
        assert request.method == "POST"
        assert request.url.path == f"/{ORG}/{PROJECT}/_apis/build/builds"
        body = json.loads(request.content.decode("utf-8"))
        assert body == {
            "definition": {"id": 7},
            "sourceBranch": "refs/heads/main",
            "sourceVersion": "deadbeef",
        }
        return httpx.Response(200, json={"id": 100})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        adapter = AzureDevOpsPipelines(organization=ORG, pat=PAT, client=client)
        await adapter.rerun(REPO, run_id=99)

    assert seen == ["GET", "POST"]


@pytest.mark.asyncio
async def test_rerun_missing_definition_raises() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        return httpx.Response(200, json={"definition": {}, "id": 55})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        adapter = AzureDevOpsPipelines(organization=ORG, pat=PAT, client=client)
        with pytest.raises(ValueError, match="Azure build 55 has no definition id"):
            await adapter.rerun(REPO, run_id=55)


@pytest.mark.asyncio
async def test_get_logs_empty_value() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        assert request.url.path.endswith("/logs")
        return httpx.Response(200, json={"value": []})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        adapter = AzureDevOpsPipelines(organization=ORG, pat=PAT, client=client)
        text = await adapter.get_logs(REPO, run_id=10)

    assert text == ""


@pytest.mark.asyncio
async def test_get_logs_joins_multiple_entries() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        if request.url.path.endswith("/builds/10/logs"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": 1, "type": "build"},
                        {"id": 2, "type": "deploy"},
                    ]
                },
            )
        if request.url.path.endswith("/logs/1"):
            return httpx.Response(200, text="line-one")
        if request.url.path.endswith("/logs/2"):
            return httpx.Response(200, text="line-two")
        raise AssertionError(f"unexpected path {request.url.path}")

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        adapter = AzureDevOpsPipelines(organization=ORG, pat=PAT, client=client)
        text = await adapter.get_logs(REPO, run_id=10)

    assert "===== build =====" in text
    assert "line-one" in text
    assert "===== deploy =====" in text
    assert "line-two" in text
    assert "\n\n" in text


@pytest.mark.asyncio
async def test_project_repo_uses_constructor_fallback() -> None:
    short = RepoRef(kind="azure_devops", owner=ORG, repo="app")

    def match(request: httpx.Request) -> httpx.Response:
        _assert_ado_request(request)
        assert request.url.path == f"/{ORG}/{PROJECT}/_apis/build/builds"
        return httpx.Response(200, json={"value": []})

    async with httpx.AsyncClient(transport=_MockTransport(match)) as client:
        adapter = AzureDevOpsPipelines(
            organization=ORG, pat=PAT, project=PROJECT, client=client
        )
        await adapter.list_runs(short, limit=1)


def test_project_repo_invalid_ref_raises() -> None:
    bad = RepoRef(kind="azure_devops", owner=ORG, repo="app")
    with pytest.raises(ValueError, match="lacks project segment"):
        _project_repo(bad, None)
