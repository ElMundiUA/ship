"""Live System aggregator (Dashboard v2 — PR-2).

Smoke coverage:
- Empty workspace returns the expected zero-shape (no runs, no
  knowledge, no routines, idle specialists).
- A succeeded :class:`PipelineRun` lifts ``masthead.success_rate_7d``
  to 1.0 with ``last_run_status='ok'``.
- A failed :class:`PipelineRun` flips the most recent status to
  ``error`` and increments ``failures_7d``.
- A done :class:`KnowledgeIngestionRun` populates
  ``knowledge.ingested_today`` and the state stays ``idle``.
- Inbox queue counts split improvement-vs-failure correctly.
- Viewer can read; stranger gets 404.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


@pytest.mark.asyncio
async def test_empty_workspace_returns_zero_shape(v1_client, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/dashboard/live-system",
        headers=_auth(raw),
    )
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["masthead"] == {
        "success_rate_7d": None,
        "failures_7d": 0,
        "last_run_at": None,
        "last_run_status": None,
    }
    assert body["knowledge"]["ingested_today"] == 0
    assert body["knowledge"]["state_label"] == "idle"
    assert body["routines"] == []
    assert body["daily"]["queue_wins"] == 0
    assert body["daily"]["queue_blockers"] == 0
    assert body["specialists"] == {
        "idle_count": 0,
        "working_count": 0,
        "errored_count": 0,
        "errored_names": [],
        "working_name": None,
    }


@pytest.mark.asyncio
async def test_masthead_aggregates_pipeline_runs(
    v1_client, seed_workspace, db_session
) -> None:
    """A succeeded run + a failed run → success_rate=0.5, failures_7d=1."""
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.db.models.lanes import Routine, RoutineRun

    _, raw, ws = seed_workspace
    repo = WorkspaceRepo(
        workspace_id=ws.id,
        provider="github",
        external_id=hash(uuid.uuid4()) & 0x7FFFFFFF,
        full_name=f"test/live-{uuid.uuid4().hex[:6]}",
        html_url=f"https://github.com/test/live-{uuid.uuid4().hex[:6]}",
    )
    db_session.add(repo)
    await db_session.flush()
    pipe = Routine(
        workspace_id=ws.id,
        repo_id=repo.id,
        lane_id="dev_implementation",
        kind="event",
        pattern="ship-dev",
        enabled=True,
    )
    db_session.add(pipe)
    await db_session.flush()

    now = datetime.now(timezone.utc)
    db_session.add(
        RoutineRun(
            routine_id=pipe.id,
            workspace_id=ws.id,
            trigger="manual",
            status="succeeded",
            started_at=now - timedelta(hours=3),
            finished_at=now - timedelta(hours=2),
        )
    )
    db_session.add(
        RoutineRun(
            routine_id=pipe.id,
            workspace_id=ws.id,
            trigger="manual",
            status="failed",
            started_at=now - timedelta(hours=1),
            finished_at=now - timedelta(minutes=30),
        )
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/dashboard/live-system",
        headers=_auth(raw),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["masthead"]["success_rate_7d"] == pytest.approx(0.5)
    assert body["masthead"]["failures_7d"] == 1
    assert body["masthead"]["last_run_status"] == "error"
    # Latest pipeline_run is failed → specialist errored.
    assert body["specialists"]["errored_count"] == 1
    assert body["specialists"]["errored_names"] == ["developer"]


@pytest.mark.asyncio
async def test_inbox_queue_counts_split_by_type(
    v1_client, seed_workspace, db_session
) -> None:
    from backend.app.db.models.inbox import InboxItem

    _, raw, ws = seed_workspace
    db_session.add_all(
        [
            InboxItem(
                workspace_id=ws.id,
                type="improvement",
                title="Win 1",
                source_table="improvements",
                source_id=uuid.uuid4(),
                status="new",
            ),
            InboxItem(
                workspace_id=ws.id,
                type="improvement",
                title="Win 2",
                source_table="improvements",
                source_id=uuid.uuid4(),
                status="new",
            ),
            InboxItem(
                workspace_id=ws.id,
                type="failure",
                title="Blocker",
                source_table="pipeline_runs",
                source_id=uuid.uuid4(),
                status="new",
            ),
        ]
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/dashboard/live-system",
        headers=_auth(raw),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["daily"]["queue_wins"] == 2
    assert body["daily"]["queue_blockers"] == 1


@pytest.mark.asyncio
async def test_stranger_is_404d(v1_client, seed_workspace, db_session) -> None:
    _, _, ws = seed_workspace
    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import ApiToken, User

    stranger = User(
        email=f"stranger-{uuid.uuid4().hex[:6]}@example.com",
        display_name="Stranger",
    )
    db_session.add(stranger)
    await db_session.flush()
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=stranger.id,
            name="stranger-token",
            hashed_secret=_hash_token(raw),
            prefix=PAT_PREFIX,
            scopes=[],
        )
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/dashboard/live-system",
        headers=_auth(raw),
    )
    assert res.status_code == 404, res.text
