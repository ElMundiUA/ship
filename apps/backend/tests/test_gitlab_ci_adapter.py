"""Unit tests for :class:`GitLabCI` — mocked httpx, no live GitLab."""

from __future__ import annotations

import httpx
import pytest

from backend.app.integrations.gateway.code_host import RepoRef
from backend.app.integrations.gitlab.ci_adapter import GitLabCI, _gitlab_conclusion

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


def _adapter(client: httpx.AsyncClient) -> GitLabCI:
    return GitLabCI(base_url=_BASE_URL, pat=_PAT, client=client)


@pytest.mark.asyncio
async def test_list_runs_maps_status_and_url() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "acme%2Fwidget" in str(request.url)
        assert request.url.path.endswith("/pipelines")
        assert request.url.params.get("per_page") == "25"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 1,
                    "ref": "main",
                    "status": "success",
                    "web_url": "https://gitlab.example.com/acme/widget/-/pipelines/1",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T01:00:00Z",
                    "sha": "abc123",
                }
            ],
        )

    async with _client(match) as client:
        runs = await _adapter(client).list_runs(_REPO, limit=25)

    assert len(runs) == 1
    assert runs[0]["id"] == 1
    assert runs[0]["name"] == "main"
    assert runs[0]["status"] == "success"
    assert runs[0]["conclusion"] == "success"
    assert runs[0]["url"] == "https://gitlab.example.com/acme/widget/-/pipelines/1"
    assert runs[0]["ref"] == "main"
    assert runs[0]["sha"] == "abc123"


@pytest.mark.asyncio
async def test_list_runs_respects_limit_cap() -> None:
    pipelines = [
        {"id": i, "ref": "main", "status": "success", "web_url": f"https://x/{i}"}
        for i in range(100)
    ]

    def match(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("per_page") == "100"
        return httpx.Response(200, json=pipelines)

    async with _client(match) as client:
        runs = await _adapter(client).list_runs(_REPO, limit=200)

    assert len(runs) == 100


@pytest.mark.asyncio
async def test_list_runs_skips_non_dict_items() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"id": 1, "ref": "main", "status": "success", "web_url": "https://x/1"},
                "not-a-dict",
                {},
            ],
        )

    async with _client(match) as client:
        runs = await _adapter(client).list_runs(_REPO)

    assert len(runs) == 2
    assert runs[0]["id"] == 1
    assert runs[1]["id"] is None


@pytest.mark.asyncio
async def test_rerun_posts_retry_endpoint() -> None:
    def match(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "acme%2Fwidget" in str(request.url)
        assert request.url.path.endswith("/pipelines/42/retry")
        return httpx.Response(201, json={})

    transport = _MockTransport(match)
    async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL) as client:
        await _adapter(client).rerun(_REPO, run_id=42)

    assert len(transport.requests) == 1


@pytest.mark.asyncio
async def test_get_logs_concatenates_jobs() -> None:
    call = 0

    def match(request: httpx.Request) -> httpx.Response:
        nonlocal call
        call += 1
        if "/pipelines/7/jobs" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {"id": 10, "name": "build"},
                    {"name": "no-id"},
                    {"id": 11, "name": "test"},
                ],
            )
        if request.url.path.endswith("/jobs/10/trace"):
            return httpx.Response(200, text="build log")
        if request.url.path.endswith("/jobs/11/trace"):
            return httpx.Response(200, text="test log")
        raise AssertionError(f"unexpected path: {request.url.path}")

    async with _client(match) as client:
        logs = await _adapter(client).get_logs(_REPO, run_id=7)

    assert "===== build =====\nbuild log" in logs
    assert "===== test =====\ntest log" in logs
    assert call == 3


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("success", "success"),
        ("failed", "failed"),
        ("canceled", "canceled"),
        ("skipped", "skipped"),
        ("running", None),
        ("", None),
        ("unknown", None),
        (None, None),
    ],
)
def test_gitlab_conclusion_matrix(status: object, expected: str | None) -> None:
    assert _gitlab_conclusion(status) == expected
