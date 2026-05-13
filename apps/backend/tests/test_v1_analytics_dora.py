"""DORA analytics endpoint coverage.

Verifies the four metrics computed off ``WorkflowRun`` + ``PullRequest``
within a configurable rolling window:

- Deployment frequency (per-day median + time series).
- Lead time for changes (median + p90 + per-week time series).
- Change failure rate (failed deploys + hotfix PRs / total).
- Mean time to recovery (median across consecutive failure→success gaps).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_empty_workspace_returns_zero_shape(
    v1_client, seed_workspace
) -> None:
    """No runs, no PRs → all-zero metrics, "no data" labels, all four
    series populated with one entry per day in the default 30d window."""
    _, raw, ws = seed_workspace
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/analytics/dora",
        headers=_auth(raw),
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["window_days"] == 30
    df = body["deployment_frequency"]
    assert df["total_deploys"] == 0
    assert df["per_day_average"] == 0.0
    assert df["per_day_median"] == 0.0
    assert len(df["time_series"]) >= 30

    lt = body["lead_time"]
    assert lt["sample_size"] == 0
    assert lt["median_hours"] is None

    cfr = body["change_failure_rate"]
    assert cfr["rate"] == 0.0
    assert cfr["total_deploys"] == 0

    mttr = body["mttr"]
    assert mttr["median_hours"] is None
    assert mttr["incidents"] == []


@pytest.mark.asyncio
async def test_deploys_aggregate_into_per_day_counts(
    v1_client, seed_workspace, db_session
) -> None:
    """Three successful deploy runs on consecutive days roll up into
    ``total_deploys=3`` with the right per-day buckets."""
    from backend.app.db.models.pipelines import WorkflowRun

    _, raw, ws = seed_workspace
    now = datetime.now(timezone.utc)
    for i in range(3):
        db_session.add(
            WorkflowRun(
                workspace_id=ws.id,
                external_id=10_000 + i,
                repo_full_name="elmundi/ship",
                name="Ship — platform images (backend + console + landing)",
                event="push",
                status="completed",
                conclusion="success",
                head_branch="main",
                head_sha=f"sha-{i}",
                started_at=now - timedelta(days=i, hours=1),
                finished_at=now - timedelta(days=i),
            )
        )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/analytics/dora",
        headers=_auth(raw),
    )
    body = res.json()
    df = body["deployment_frequency"]
    assert df["total_deploys"] == 3
    days_with_deploys = [p for p in df["time_series"] if p["count"] > 0]
    assert len(days_with_deploys) == 3
    assert all(p["count"] == 1 for p in days_with_deploys)


@pytest.mark.asyncio
async def test_lead_time_uses_pr_open_to_merge_in_hours(
    v1_client, seed_workspace, db_session
) -> None:
    """Two merged PRs with explicit open/merge timestamps → median lead
    time is the actual median."""
    from backend.app.db.models.pipelines import PullRequest

    _, raw, ws = seed_workspace
    now = datetime.now(timezone.utc)
    db_session.add(
        PullRequest(
            workspace_id=ws.id,
            external_id=1,
            number=1,
            repo_full_name="elmundi/ship",
            title="feat: a thing",
            state="closed",
            merged=True,
            draft=False,
            html_url="https://github.com/elmundi/ship/pull/1",
            opened_at=now - timedelta(hours=10),
            merged_at=now - timedelta(hours=2),  # 8h lead
        )
    )
    db_session.add(
        PullRequest(
            workspace_id=ws.id,
            external_id=2,
            number=2,
            repo_full_name="elmundi/ship",
            title="fix: another thing",
            state="closed",
            merged=True,
            draft=False,
            html_url="https://github.com/elmundi/ship/pull/2",
            opened_at=now - timedelta(hours=5),
            merged_at=now - timedelta(hours=3),  # 2h lead
        )
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/analytics/dora",
        headers=_auth(raw),
    )
    body = res.json()
    lt = body["lead_time"]
    assert lt["sample_size"] == 2
    # Median of [8h, 2h] = 5h.
    assert lt["median_hours"] == pytest.approx(5.0, abs=0.5)
    # Elite band (< 24h).
    assert lt["label"].startswith("Elite")


@pytest.mark.asyncio
async def test_change_failure_rate_combines_failures_and_hotfix_prs(
    v1_client, seed_workspace, db_session
) -> None:
    """A successful deploy + a failed deploy + a HOTFIX-titled merged PR
    → CFR counts the failure AND the hotfix as effective failures
    (capped at total deploys)."""
    from backend.app.db.models.pipelines import PullRequest, WorkflowRun

    _, raw, ws = seed_workspace
    now = datetime.now(timezone.utc)
    db_session.add(
        WorkflowRun(
            workspace_id=ws.id,
            external_id=20_001,
            repo_full_name="elmundi/ship",
            name="Ship — platform images",
            event="push",
            status="completed",
            conclusion="success",
            head_branch="main",
            head_sha="ok",
            started_at=now - timedelta(hours=4),
            finished_at=now - timedelta(hours=3),
        )
    )
    db_session.add(
        WorkflowRun(
            workspace_id=ws.id,
            external_id=20_002,
            repo_full_name="elmundi/ship",
            name="Ship — platform images",
            event="push",
            status="completed",
            conclusion="failure",
            head_branch="main",
            head_sha="bad",
            started_at=now - timedelta(hours=2),
            finished_at=now - timedelta(hours=1, minutes=30),
        )
    )
    db_session.add(
        PullRequest(
            workspace_id=ws.id,
            external_id=99,
            number=99,
            repo_full_name="elmundi/ship",
            title="HOTFIX: revert bad migration",
            state="closed",
            merged=True,
            draft=False,
            html_url="https://github.com/elmundi/ship/pull/99",
            opened_at=now - timedelta(hours=1),
            merged_at=now - timedelta(minutes=30),
        )
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/analytics/dora",
        headers=_auth(raw),
    )
    body = res.json()
    cfr = body["change_failure_rate"]
    assert cfr["total_deploys"] == 2
    assert cfr["failed_deploys"] == 1
    assert cfr["hotfix_prs"] == 1
    # 1 failure + 1 hotfix capped at 2 total → 100%.
    assert cfr["rate"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_mttr_pairs_failure_with_next_success(
    v1_client, seed_workspace, db_session
) -> None:
    """A failed deploy followed by a success ~1h later → one incident
    with hours≈1, median≈1, label=Elite."""
    from backend.app.db.models.pipelines import WorkflowRun

    _, raw, ws = seed_workspace
    now = datetime.now(timezone.utc)
    fail_at = now - timedelta(hours=3)
    recover_at = now - timedelta(hours=2)
    db_session.add(
        WorkflowRun(
            workspace_id=ws.id,
            external_id=30_001,
            repo_full_name="elmundi/ship",
            name="Ship — deploy",
            event="push",
            status="completed",
            conclusion="failure",
            head_branch="main",
            head_sha="failsha",
            started_at=fail_at,
            finished_at=fail_at + timedelta(minutes=5),
        )
    )
    db_session.add(
        WorkflowRun(
            workspace_id=ws.id,
            external_id=30_002,
            repo_full_name="elmundi/ship",
            name="Ship — deploy",
            event="push",
            status="completed",
            conclusion="success",
            head_branch="main",
            head_sha="recoversha",
            started_at=recover_at,
            finished_at=recover_at + timedelta(minutes=5),
        )
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/analytics/dora",
        headers=_auth(raw),
    )
    body = res.json()
    mttr = body["mttr"]
    assert len(mttr["incidents"]) == 1
    incident = mttr["incidents"][0]
    assert incident["recovered_at"] is not None
    # ~55 min between fail.finished_at and recover.started_at.
    assert mttr["median_hours"] == pytest.approx(0.92, abs=0.2)
    assert mttr["label"].startswith("Elite")


@pytest.mark.asyncio
async def test_non_main_branch_runs_are_ignored(
    v1_client, seed_workspace, db_session
) -> None:
    """A PR-preview workflow on a feature branch must NOT count as a
    deploy."""
    from backend.app.db.models.pipelines import WorkflowRun

    _, raw, ws = seed_workspace
    now = datetime.now(timezone.utc)
    db_session.add(
        WorkflowRun(
            workspace_id=ws.id,
            external_id=40_000,
            repo_full_name="elmundi/ship",
            name="Ship — platform images",
            event="pull_request",
            status="completed",
            conclusion="success",
            head_branch="feat/preview",
            head_sha="featsha",
            started_at=now - timedelta(hours=1),
            finished_at=now,
        )
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/analytics/dora",
        headers=_auth(raw),
    )
    body = res.json()
    assert body["deployment_frequency"]["total_deploys"] == 0
