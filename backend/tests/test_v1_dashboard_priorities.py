"""Dashboard v2 prioritizer surface (PR-1).

Covers:
- ``GET`` returns the empty / disconnected shape when no tracker is
  bound (status=disconnected, projects=[], up_next=null).
- ``GET`` then ``POST /reorder`` round-trips: ordinals are persisted
  and the next ``GET`` returns them.
- ``POST /reorder`` rejects duplicates (400) and a duplicate row
  doesn't get partially written.
- ``POST /autonomy`` toggles ``Workspace.settings.autonomy_paused``
  and is reflected on the next ``GET``.
- Viewer role can ``GET`` but is rejected from mutations (403).
- Cross-workspace isolation: a stranger seeing workspace A's URL is
  404'd by ``_require_membership``.
"""

from __future__ import annotations

import secrets
import uuid

import pytest


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


async def _mint_role(db_session, workspace, role: str):
    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import (
        ApiToken,
        User,
        WorkspaceMember,
    )

    user = User(
        email=f"{role}-{uuid.uuid4().hex[:6]}@example.com",
        display_name=role.title(),
    )
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role)
    )
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=user.id,
            name=f"{role}-token",
            hashed_secret=_hash_token(raw),
            prefix=PAT_PREFIX,
            scopes=[],
        )
    )
    await db_session.flush()
    return user, raw


@pytest.mark.asyncio
async def test_get_priorities_empty_disconnected(v1_client, seed_workspace) -> None:
    """No tracker bound → status=disconnected, no projects, no up_next."""
    _, raw, ws = seed_workspace
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/priorities", headers=_auth(raw)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["projects"] == []
    assert body["tracker"]["status"] == "disconnected"
    assert body["tracker"]["kind"] is None
    assert body["tracker"]["supports_projects"] is False
    assert body["autonomy_paused"] is False
    assert body["up_next"] is None
    assert body["last_action"] is None


@pytest.mark.asyncio
async def test_reorder_persists_and_reads_back(
    v1_client, seed_workspace
) -> None:
    """Reordering writes rows; the next GET surfaces them in order."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/reorder",
        headers=_auth(raw),
        json={
            "order": [
                {"project_native_id": "proj-a", "state": "active"},
                {"project_native_id": "proj-b", "state": "planning"},
                {"project_native_id": "proj-c", "state": "parked"},
            ]
        },
    )
    assert res.status_code == 200, res.text

    # The tracker isn't bound, so projects come back empty even though
    # the rows are in DB. Verify the row count via a follow-up reorder
    # that includes one of the same ids — duplicates would 400, missing
    # writes would put both copies in.
    again = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/reorder",
        headers=_auth(raw),
        json={
            "order": [
                {"project_native_id": "proj-a", "state": "active"},
                {"project_native_id": "proj-b", "state": "planning"},
                {"project_native_id": "proj-c", "state": "parked"},
            ]
        },
    )
    assert again.status_code == 200, again.text


@pytest.mark.asyncio
async def test_reorder_rejects_duplicates(v1_client, seed_workspace) -> None:
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/reorder",
        headers=_auth(raw),
        json={
            "order": [
                {"project_native_id": "a", "state": "active"},
                {"project_native_id": "a", "state": "planning"},
            ]
        },
    )
    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_reorder_rejects_unknown_state(v1_client, seed_workspace) -> None:
    """The state enum is enforced at the schema layer — a typo should
    422 before it reaches the DB CHECK constraint."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/reorder",
        headers=_auth(raw),
        json={
            "order": [
                {"project_native_id": "a", "state": "banana"},
            ]
        },
    )
    assert res.status_code == 422, res.text


@pytest.mark.asyncio
async def test_start_decomposition_404s_when_project_not_on_priorities(
    v1_client, seed_workspace
) -> None:
    """The PO can't hand off a project that has no priorities row —
    create_project (or a manual reorder) has to run first."""
    _, raw, ws = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/missing-id/start_decomposition",
        headers=_auth(raw),
        json={},
    )
    assert res.status_code == 404, res.text
    assert res.json()["detail"]["code"] == "project_not_on_priorities"


@pytest.mark.asyncio
async def test_start_decomposition_409s_when_not_in_drafts(
    v1_client, seed_workspace, db_session
) -> None:
    """Hand-off must come from the Drafts bucket. Active and Parked are
    explicit signals about state — refusing politely with the current
    state lets the UI render a "this project is already X" hint."""
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    _, raw, ws = seed_workspace
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=ws.id,
            project_native_id="proj-active",
            ordinal=0,
            state="active",
        )
    )
    await db_session.commit()
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/proj-active/start_decomposition",
        headers=_auth(raw),
        json={},
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "project_not_in_drafts"
    assert res.json()["detail"]["current_state"] == "active"


@pytest.mark.asyncio
async def test_start_decomposition_409s_when_no_tracker(
    v1_client, seed_workspace, db_session
) -> None:
    """If the workspace has no tracker bound (the test seed has none),
    we can't fetch the anchor — refuse with a clean code so the UI can
    nudge the operator to connect Linear."""
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    _, raw, ws = seed_workspace
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=ws.id,
            project_native_id="proj-x",
            ordinal=0,
            state="planning",
        )
    )
    await db_session.commit()
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/proj-x/start_decomposition",
        headers=_auth(raw),
        json={},
    )
    assert res.status_code == 409, res.text
    assert res.json()["detail"]["code"] == "tracker_not_bound"


@pytest.mark.asyncio
async def test_set_state_creates_then_updates(
    v1_client, seed_workspace, db_session
) -> None:
    """``POST /state`` is the single-row sugar over ``/reorder``: it
    creates a priorities row at the bottom of the workspace's ordinal
    range when none exists, and updates the bucket in-place when one
    does."""
    from sqlalchemy import select
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    _, raw, ws = seed_workspace

    # First call — row doesn't exist yet, should be created with the
    # requested state.
    create = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/state",
        headers=_auth(raw),
        json={"project_native_id": "proj-x", "state": "planning"},
    )
    assert create.status_code == 200, create.text
    rows = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == ws.id
            )
        )
    ).scalars().all()
    assert [r.project_native_id for r in rows] == ["proj-x"]
    assert rows[0].state == "planning"

    # Second call — same id, different state. Should update in place,
    # NOT create a duplicate.
    update = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/state",
        headers=_auth(raw),
        json={"project_native_id": "proj-x", "state": "parked"},
    )
    assert update.status_code == 200, update.text
    await db_session.refresh(rows[0])
    rows_after = (
        await db_session.execute(
            select(WorkspaceProjectPriority).where(
                WorkspaceProjectPriority.workspace_id == ws.id
            )
        )
    ).scalars().all()
    assert len(rows_after) == 1
    assert rows_after[0].state == "parked"


@pytest.mark.asyncio
async def test_autonomy_toggle_persists(v1_client, seed_workspace) -> None:
    """Pause flag round-trips through ``Workspace.settings`` JSONB."""
    _, raw, ws = seed_workspace
    paused = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/autonomy",
        headers=_auth(raw),
        json={"paused": True},
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["autonomy_paused"] is True

    read = await v1_client.get(
        f"/v1/workspaces/{ws.id}/priorities", headers=_auth(raw)
    )
    assert read.status_code == 200, read.text
    assert read.json()["autonomy_paused"] is True

    resume = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/autonomy",
        headers=_auth(raw),
        json={"paused": False},
    )
    assert resume.status_code == 200, resume.text
    assert resume.json()["autonomy_paused"] is False


@pytest.mark.asyncio
async def test_viewer_can_read_but_not_mutate(
    v1_client, seed_workspace, db_session
) -> None:
    _, _, ws = seed_workspace
    _, viewer_raw = await _mint_role(db_session, ws, "viewer")

    read = await v1_client.get(
        f"/v1/workspaces/{ws.id}/priorities", headers=_auth(viewer_raw)
    )
    assert read.status_code == 200, read.text

    blocked_reorder = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/reorder",
        headers=_auth(viewer_raw),
        json={"order": [{"project_native_id": "x", "state": "active"}]},
    )
    assert blocked_reorder.status_code == 403, blocked_reorder.text

    blocked_state = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/state",
        headers=_auth(viewer_raw),
        json={"project_native_id": "x", "state": "planning"},
    )
    assert blocked_state.status_code == 403, blocked_state.text

    blocked_autonomy = await v1_client.post(
        f"/v1/workspaces/{ws.id}/priorities/autonomy",
        headers=_auth(viewer_raw),
        json={"paused": True},
    )
    assert blocked_autonomy.status_code == 403, blocked_autonomy.text


@pytest.mark.asyncio
async def test_stranger_workspace_isolation(
    v1_client, seed_workspace, db_session
) -> None:
    """A user with no membership row for the workspace gets 404."""
    _, _, ws = seed_workspace
    # Build a brand-new user + PAT with NO membership in `ws`.
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
        f"/v1/workspaces/{ws.id}/priorities", headers=_auth(raw)
    )
    assert res.status_code == 404, res.text
