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


@pytest.mark.asyncio
async def test_new_active_persists_intent_shape_project(
    v1_client, seed_workspace
) -> None:
    """The dashboard "+ New project" CTA POSTs ``intent='shape_project'``.
    The new thread has to carry that intent on read so the system-prompt
    assembler can inject the drafting-mode block on every turn, and so
    the client can surface a "drafting" UI hint."""
    _, raw, workspace = seed_workspace
    auth = {"Authorization": f"Bearer {raw}"}

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/active/new",
        headers=auth,
        json={"intent": "shape_project"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["intent"] == "shape_project"
    # Default title is mode-specific so the archive list disambiguates
    # without the operator having to type one.
    assert body["title"] == "Drafting a project"

    # GET /chat/active also surfaces the intent — the prompt
    # assembler reads it on every turn, not just at creation.
    active = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/chat/active", headers=auth
    )
    assert active.json()["intent"] == "shape_project"


@pytest.mark.asyncio
async def test_new_active_intent_defaults_to_null(
    v1_client, seed_workspace
) -> None:
    """A regular "New conversation" click leaves intent null, so the
    drafting-mode prompt block is NOT injected for default chats."""
    _, raw, workspace = seed_workspace
    auth = {"Authorization": f"Bearer {raw}"}

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/active/new",
        headers=auth,
        json={"title": "Hello"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["intent"] is None


@pytest.mark.asyncio
async def test_new_active_rejects_unknown_intent(
    v1_client, seed_workspace
) -> None:
    """Pydantic Literal guards the enum at the schema layer — a typo'd
    intent never reaches the DB."""
    _, raw, workspace = seed_workspace
    auth = {"Authorization": f"Bearer {raw}"}

    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/chat/active/new",
        headers=auth,
        json={"intent": "shape_banana"},
    )
    assert resp.status_code == 422, resp.text
