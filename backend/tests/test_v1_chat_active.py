"""Active-thread lifecycle (C12 single-window UX).

The SSE ``/chat/stream`` endpoint needs an LLM key to run, so its
coverage lives in the integration smoke test (skipped when no key).
Here we exercise the read / pack / new endpoints that stand on
their own without any model calls.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_active_endpoint_bootstraps_thread(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    auth = {"Authorization": f"Bearer {raw}"}

    # First hit bootstraps an empty thread.
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/chat/active", headers=auth
    )
    assert resp.status_code == 200, resp.text
    first = resp.json()
    assert first["status"] == "active"
    assert first["message_count"] == 0
    assert first["packed_into_bucket_id"] is None

    # Second hit returns the same thread.
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/chat/active", headers=auth
    )
    assert resp.json()["id"] == first["id"]


@pytest.mark.asyncio
async def test_new_active_archives_previous(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    auth = {"Authorization": f"Bearer {raw}"}

    first = (
        await v1_client.get(
            f"/v1/workspaces/{workspace.id}/chat/active", headers=auth
        )
    ).json()

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/active/new",
        headers=auth,
        json={"title": "Fresh start"},
    )
    assert resp.status_code == 200, resp.text
    fresh = resp.json()
    assert fresh["id"] != first["id"]
    assert fresh["title"] == "Fresh start"

    # The old thread is now archived — /active returns the fresh one.
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/chat/active", headers=auth
    )
    assert resp.json()["id"] == fresh["id"]
