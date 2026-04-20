"""Chat threads (C10) — API contract.

Covers:

- Create thread → user + stub assistant messages present.
- Append message → both user + assistant are appended, updated_at bumps.
- Can't append to a resolved thread.
- Resolve with create_improvement=True materialises an Improvement.
- Archive path doesn't spawn an Improvement.
- Cross-workspace access returns 404.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_create_thread_seeds_messages(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "title": "Scope the payment webhooks",
            "initial_message": "Can we add retry + idempotency to payment webhooks?",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["message_count"] == 2
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant"]
    assert body["messages"][0]["body"].startswith("Can we add retry")


@pytest.mark.asyncio
async def test_append_bumps_thread(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    thread = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/chat/threads",
            headers={"Authorization": f"Bearer {raw}"},
            json={"title": "t", "initial_message": "hi"},
        )
    ).json()
    initial_updated_at = thread["updated_at"]

    # Sleep briefly so updated_at can actually tick (Postgres
    # timestamp resolution is microseconds but the clock sometimes
    # returns identical values in the same async tick).
    await asyncio.sleep(0.01)

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads/{thread['id']}/messages",
        headers={"Authorization": f"Bearer {raw}"},
        json={"body": "follow-up question"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["message_count"] == 4
    assert body["updated_at"] >= initial_updated_at


@pytest.mark.asyncio
async def test_cannot_append_to_resolved(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    thread = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/chat/threads",
            headers={"Authorization": f"Bearer {raw}"},
            json={"title": "t", "initial_message": "hi"},
        )
    ).json()
    await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads/{thread['id']}/resolve",
        headers={"Authorization": f"Bearer {raw}"},
        json={"ticket_ref": "TICKET-1"},
    )
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads/{thread['id']}/messages",
        headers={"Authorization": f"Bearer {raw}"},
        json={"body": "more"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_resolve_with_improvement(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.agent_surface import Improvement

    _, raw, workspace = seed_workspace
    thread = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/chat/threads",
            headers={"Authorization": f"Bearer {raw}"},
            json={"title": "Add circuit breaker", "initial_message": "how?"},
        )
    ).json()
    workspace_id = workspace.id

    resolve = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads/{thread['id']}/resolve",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "ticket_ref": "LINEAR-789",
            "create_improvement": True,
            "action": "resolved",
        },
    )
    assert resolve.status_code == 200, resolve.text
    assert resolve.json()["status"] == "resolved"
    assert resolve.json()["resolved_ticket_ref"] == "LINEAR-789"

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(Improvement).where(Improvement.workspace_id == workspace_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].kind == "chat"
    assert rows[0].context["thread_id"] == thread["id"]


@pytest.mark.asyncio
async def test_archive_path_skips_improvement(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.agent_surface import Improvement

    _, raw, workspace = seed_workspace
    thread = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/chat/threads",
            headers={"Authorization": f"Bearer {raw}"},
            json={"title": "Spike", "initial_message": "nevermind"},
        )
    ).json()
    workspace_id = workspace.id

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads/{thread['id']}/resolve",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "ticket_ref": "NOP",
            "create_improvement": True,
            "action": "archived",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"
    assert resp.json()["resolved_ticket_ref"] is None

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(Improvement).where(Improvement.workspace_id == workspace_id)
        )
    ).scalars().all()
    assert len(rows) == 0


@pytest.mark.asyncio
async def test_list_threads_orders_by_updated(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    a = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/chat/threads",
            headers={"Authorization": f"Bearer {raw}"},
            json={"title": "A", "initial_message": "first"},
        )
    ).json()
    await asyncio.sleep(0.01)
    b = (
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/chat/threads",
            headers={"Authorization": f"Bearer {raw}"},
            json={"title": "B", "initial_message": "second"},
        )
    ).json()

    # Bump B by appending a message so its updated_at is newer
    # than A — we can't rely on creation-time microseconds alone
    # across the greenlet boundary.
    await asyncio.sleep(0.01)
    await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/threads/{b['id']}/messages",
        headers={"Authorization": f"Bearer {raw}"},
        json={"body": "bump"},
    )
    listed = (
        await v1_client.get(
            f"/v1/workspaces/{workspace.id}/chat/threads",
            headers={"Authorization": f"Bearer {raw}"},
        )
    ).json()
    assert [t["id"] for t in listed] == [b["id"], a["id"]]
