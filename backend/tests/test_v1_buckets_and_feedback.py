"""v1 API — bucket CRUD + artifact feedback (C12).

The two surfaces share a router, so one test module covers both.
They're straightforward DB CRUD on top of the rows introduced by
migration 0010_agent_v2, so the happy-path coverage here is what
guards against accidental schema / serialisation regressions.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_bucket_lifecycle(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    auth = {"Authorization": f"Bearer {raw}"}

    # Empty listing on a fresh workspace.
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/buckets", headers=auth
    )
    assert resp.status_code == 200
    assert resp.json() == []

    # Create.
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets",
        headers=auth,
        json={"name": "Auth refactor", "description": "rework sessions"},
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["slug"] == "auth-refactor"
    assert created["name"] == "Auth refactor"
    # Phase 1 consolidation surface: every bucket created via the
    # public API defaults to workspace/agent-memory, no carrier FKs.
    # UI / CLI use these fields to decide how to render the row
    # (agent-memory = "packed chat memory", workspace = "shared across
    # all projects/repos").
    assert created["scope_kind"] == "workspace"
    assert created["source_kind"] == "agent_memory"
    assert created["source_ref"] is None
    assert created["project_id"] is None
    assert created["repo_id"] is None
    assert created["user_id"] is None

    # Duplicate slug → 409.
    dup = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/buckets",
        headers=auth,
        json={"slug": "auth-refactor", "name": "Dup"},
    )
    assert dup.status_code == 409

    # Get by slug.
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/buckets/auth-refactor", headers=auth
    )
    assert resp.status_code == 200
    assert resp.json()["summary_count"] == 0

    # Patch name + archive.
    resp = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/buckets/auth-refactor",
        headers=auth,
        json={"name": "Auth rework", "archived": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Auth rework"
    assert body["archived_at"] is not None

    # Archived buckets hidden by default; visible with include_archived.
    listing = (
        await v1_client.get(
            f"/v1/workspaces/{workspace.id}/buckets", headers=auth
        )
    ).json()
    assert listing == []
    listing_all = (
        await v1_client.get(
            f"/v1/workspaces/{workspace.id}/buckets?include_archived=true",
            headers=auth,
        )
    ).json()
    assert len(listing_all) == 1


@pytest.mark.asyncio
async def test_artifact_feedback_flow(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    auth = {"Authorization": f"Bearer {raw}"}

    # Empty listing.
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/artifact-feedback", headers=auth
    )
    assert resp.status_code == 200
    assert resp.json() == []

    # Create.
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/artifact-feedback",
        headers=auth,
        json={
            "artifact_id": "pattern/common-base",
            "body": "steps 3-4 assume docker",
            "context": {"hint": "monorepo"},
        },
    )
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["status"] == "open"
    assert created["artifact_id"] == "pattern/common-base"
    assert created["context"] == {"hint": "monorepo"}

    # Patch to triaged.
    resp = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/artifact-feedback/{created['id']}",
        headers=auth,
        json={"status": "triaged", "linked_pr_url": "https://github.com/x/y/pull/1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "triaged"
    assert body["linked_pr_url"].endswith("/pull/1")

    # Status filter.
    open_only = (
        await v1_client.get(
            f"/v1/workspaces/{workspace.id}/artifact-feedback?status_filter=open",
            headers=auth,
        )
    ).json()
    assert open_only == []
    triaged = (
        await v1_client.get(
            f"/v1/workspaces/{workspace.id}/artifact-feedback?status_filter=triaged",
            headers=auth,
        )
    ).json()
    assert len(triaged) == 1
