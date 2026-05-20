"""Tests for the pull-request cache reconciler (C1, 2026-05-19).

``pull_requests`` is webhook-fed: missed events leave rows
``state=open`` after GitHub merges / closes a PR, and the dashboard's
stuck-PR mirror then regenerates inbox letters the operator cannot
dismiss. The reconciler walks stale-open rows every 30 min and
refreshes them from GitHub.

Coverage:

- Row merged on GH → state/merged/merged_at/closed_at copied,
  ``updated_at_external`` touched so we don't re-check next tick.
- Row still open on GH → only ``updated_at_external`` touched,
  cache fields unchanged.
- Row not stale (within 3-day threshold) → not touched (no GH call,
  no DB write).
- ``MAX_ROWS_PER_TICK`` budget enforced — extra stale rows queued
  for the next tick.
- Workspace with no active GitHub install → rows skipped, logged.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.db.models.integrations import GitHubInstallation
from backend.app.db.models.pipelines import PullRequest
from backend.app.services import pr_cache_reconciler


def _stale_open_pr(workspace_id, repo_full_name: str, number: int, days_old: int = 5):
    """``state=open`` PR row with ``updated_at_external`` ``days_old``
    days ago — qualifies for the reconciler's 3-day-stale floor."""
    ts = datetime.now(timezone.utc) - timedelta(days=days_old)
    return PullRequest(
        workspace_id=workspace_id,
        repo_id=None,
        external_id=10000 + number,
        number=number,
        repo_full_name=repo_full_name,
        title=f"PR #{number}",
        state="open",
        merged=False,
        html_url=f"https://github.com/{repo_full_name}/pull/{number}",
        opened_at=ts,
        updated_at_external=ts,
    )


def _install_for(workspace_id, installation_id: int = 555):
    return GitHubInstallation(
        workspace_id=workspace_id,
        installation_id=installation_id,
        account_login="askslayer",
    )


class _FakeGateway:
    """Minimal stand-in for ``GitHubCodeHost`` — only ``get_pull_request``
    is exercised. Stores expected responses keyed by ``(repo, number)``."""

    def __init__(self, responses: dict[tuple[str, int], dict]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, int]] = []

    async def get_pull_request(self, ref) -> dict:
        owner = ref.repo.owner
        repo = ref.repo.repo
        key = (f"{owner}/{repo}", ref.number)
        self.calls.append(key)
        if key not in self.responses:
            raise RuntimeError(f"unexpected GH call: {key}")
        return self.responses[key]


@pytest.fixture
def patch_gateway(monkeypatch):
    """Replace ``GitHubCodeHost`` with the fake — keyed by ``(repo, number)``."""
    holder: dict[str, _FakeGateway] = {}

    def install(responses: dict[tuple[str, int], dict]) -> _FakeGateway:
        gw = _FakeGateway(responses)
        holder["gw"] = gw

        def _factory(installation_id, *, settings=None, client=None):
            return gw

        monkeypatch.setattr(pr_cache_reconciler, "GitHubCodeHost", _factory)
        return gw

    return install


@pytest.mark.asyncio
async def test_merged_pr_is_reconciled_from_open_to_merged(
    db_session, seed_workspace, patch_gateway, monkeypatch
) -> None:
    """The headline case — GH webhook missed a merge event, cache
    says open, GH says merged. The reconciler must copy state +
    merged_at into the row."""
    _, _, ws = seed_workspace
    db_session.add(_install_for(ws.id))
    pr = _stale_open_pr(ws.id, "askslayer/visitor-back", 11, days_old=10)
    db_session.add(pr)
    await db_session.flush()
    pr_id = pr.id

    merged_at = "2026-05-05T10:05:13Z"
    gateway = patch_gateway(
        {
            ("askslayer/visitor-back", 11): {
                "state": "closed",
                "merged": True,
                "merged_at": merged_at,
                "closed_at": merged_at,
            }
        }
    )

    # Patch get_sessionmaker so the function uses the test session
    async def _fake_session_ctx():
        yield db_session

    class _Maker:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self_inner):
                    return db_session
                async def __aexit__(self_inner, *_):
                    return False
            return _Ctx()

    monkeypatch.setattr(
        pr_cache_reconciler, "get_sessionmaker", lambda: _Maker()
    )

    updated = await pr_cache_reconciler.reconcile_stale_pull_requests()
    assert updated == 1
    assert gateway.calls == [("askslayer/visitor-back", 11)]

    await db_session.refresh(pr)
    assert pr.state == "merged"
    assert pr.merged is True
    assert pr.merged_at is not None
    assert pr.closed_at is not None


@pytest.mark.asyncio
async def test_still_open_pr_only_touches_timestamp(
    db_session, seed_workspace, patch_gateway, monkeypatch
) -> None:
    """When GH still says ``open``, the reconciler must not flip the
    cache — only stamp ``updated_at_external`` so the same row
    doesn't re-fire on every subsequent tick."""
    _, _, ws = seed_workspace
    db_session.add(_install_for(ws.id))
    pr = _stale_open_pr(ws.id, "askslayer/visitor-back", 28, days_old=4)
    db_session.add(pr)
    await db_session.flush()
    original_updated = pr.updated_at_external

    gateway = patch_gateway(
        {
            ("askslayer/visitor-back", 28): {
                "state": "open",
                "merged": False,
                "merged_at": None,
                "closed_at": None,
            }
        }
    )

    class _Maker:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self_inner):
                    return db_session
                async def __aexit__(self_inner, *_):
                    return False
            return _Ctx()

    monkeypatch.setattr(
        pr_cache_reconciler, "get_sessionmaker", lambda: _Maker()
    )

    updated = await pr_cache_reconciler.reconcile_stale_pull_requests()
    assert updated == 0  # state unchanged → not counted as "updated"

    await db_session.refresh(pr)
    assert pr.state == "open"
    assert pr.merged is False
    assert pr.updated_at_external > original_updated  # touched


@pytest.mark.asyncio
async def test_fresh_pr_is_not_touched(
    db_session, seed_workspace, patch_gateway, monkeypatch
) -> None:
    """Rows updated within the last 3 days are still fresh — the
    webhook path is the primary signal, the reconciler stays out
    of its way."""
    _, _, ws = seed_workspace
    db_session.add(_install_for(ws.id))
    pr = _stale_open_pr(ws.id, "askslayer/visitor-back", 42, days_old=1)
    db_session.add(pr)
    await db_session.flush()

    gateway = patch_gateway({})  # zero GH calls expected

    class _Maker:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self_inner):
                    return db_session
                async def __aexit__(self_inner, *_):
                    return False
            return _Ctx()

    monkeypatch.setattr(
        pr_cache_reconciler, "get_sessionmaker", lambda: _Maker()
    )

    updated = await pr_cache_reconciler.reconcile_stale_pull_requests()
    assert updated == 0
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_workspace_without_install_is_skipped(
    db_session, seed_workspace, patch_gateway, monkeypatch, caplog
) -> None:
    """Workspace whose GitHub App install was revoked: the row stays
    stale but we don't crash — log + continue."""
    _, _, ws = seed_workspace
    # NO install row inserted.
    pr = _stale_open_pr(ws.id, "askslayer/visitor-back", 99, days_old=10)
    db_session.add(pr)
    await db_session.flush()

    gateway = patch_gateway({})  # zero GH calls — install resolution fails

    class _Maker:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self_inner):
                    return db_session
                async def __aexit__(self_inner, *_):
                    return False
            return _Ctx()

    monkeypatch.setattr(
        pr_cache_reconciler, "get_sessionmaker", lambda: _Maker()
    )

    updated = await pr_cache_reconciler.reconcile_stale_pull_requests()
    assert updated == 0
    assert gateway.calls == []
    # Row untouched
    await db_session.refresh(pr)
    assert pr.state == "open"


@pytest.mark.asyncio
async def test_budget_caps_rows_per_tick(
    db_session, seed_workspace, patch_gateway, monkeypatch
) -> None:
    """``MAX_ROWS_PER_TICK`` keeps the GH API usage bounded. Extra
    stale rows queue up for the next tick (the oldest go first)."""
    _, _, ws = seed_workspace
    db_session.add(_install_for(ws.id))

    # MAX_ROWS_PER_TICK + 5 stale rows, all merged on GH
    responses = {}
    for i in range(pr_cache_reconciler.MAX_ROWS_PER_TICK + 5):
        n = 1000 + i
        db_session.add(
            _stale_open_pr(ws.id, "askslayer/visitor-back", n, days_old=10 + i)
        )
        responses[("askslayer/visitor-back", n)] = {
            "state": "closed",
            "merged": True,
            "merged_at": "2026-05-05T10:00:00Z",
            "closed_at": "2026-05-05T10:00:00Z",
        }
    await db_session.flush()

    gateway = patch_gateway(responses)

    class _Maker:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self_inner):
                    return db_session
                async def __aexit__(self_inner, *_):
                    return False
            return _Ctx()

    monkeypatch.setattr(
        pr_cache_reconciler, "get_sessionmaker", lambda: _Maker()
    )

    updated = await pr_cache_reconciler.reconcile_stale_pull_requests()
    assert updated == pr_cache_reconciler.MAX_ROWS_PER_TICK
    assert len(gateway.calls) == pr_cache_reconciler.MAX_ROWS_PER_TICK


@pytest.mark.asyncio
async def test_gh_api_error_logs_and_continues(
    db_session, seed_workspace, patch_gateway, monkeypatch
) -> None:
    """One PR's GH call failing must not abort the rest of the batch.
    Log + continue is the right reliability story for a passive
    background reconciler."""
    _, _, ws = seed_workspace
    db_session.add(_install_for(ws.id))
    pr_good = _stale_open_pr(ws.id, "askslayer/visitor-back", 200, days_old=10)
    pr_bad = _stale_open_pr(ws.id, "askslayer/visitor-back", 201, days_old=11)
    db_session.add(pr_good)
    db_session.add(pr_bad)
    await db_session.flush()

    class _PartialGW:
        def __init__(self) -> None:
            self.calls: list = []

        async def get_pull_request(self, ref):
            self.calls.append((ref.repo.repo, ref.number))
            if ref.number == 201:
                raise RuntimeError("GH API 502")
            return {
                "state": "closed",
                "merged": True,
                "merged_at": "2026-05-05T10:00:00Z",
                "closed_at": "2026-05-05T10:00:00Z",
            }

    gw = _PartialGW()
    monkeypatch.setattr(
        pr_cache_reconciler,
        "GitHubCodeHost",
        lambda installation_id, *, settings=None, client=None: gw,
    )

    class _Maker:
        def __call__(self):
            class _Ctx:
                async def __aenter__(self_inner):
                    return db_session
                async def __aexit__(self_inner, *_):
                    return False
            return _Ctx()

    monkeypatch.setattr(
        pr_cache_reconciler, "get_sessionmaker", lambda: _Maker()
    )

    updated = await pr_cache_reconciler.reconcile_stale_pull_requests()
    # pr_bad's update failed but pr_good still reconciled
    assert updated == 1
    await db_session.refresh(pr_good)
    assert pr_good.state == "merged"
    await db_session.refresh(pr_bad)
    assert pr_bad.state == "open"  # untouched
