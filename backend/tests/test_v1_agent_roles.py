"""HTTP tests for the agent role registry (Phase 2.4 Step A).

Pins the public + workspace surfaces:

* ``GET /v1/agent-roles`` lists Ship-shipped defaults.
* ``GET /v1/agent-roles/{slug}`` returns one default body.
* Workspace CRUD covers override (slug == default), clone (new slug
  with ``base_role_slug``), invalid combinations, and delete-revert.
* ``/{slug}/resolve`` prefers the workspace row, falls back to the
  Ship default, and 404s for unknown slugs.

The Ship defaults are file-backed under
``backend/app/resources/agent_roles/`` and shipped with the repo, so
these tests exercise the real registry rather than a fixture set.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_ship_defaults(v1_client, seed_workspace) -> None:
    _, raw, _ = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    res = await v1_client.get("/v1/agent-roles", headers=headers)
    assert res.status_code == 200, res.text
    rows = res.json()
    slugs = {row["slug"] for row in rows}
    # The Phase 2.4 mirror seeded these — sanity-check a few.
    for required in ("system", "developer", "intake", "qa-architect"):
        assert required in slugs, f"missing default: {required}"

    intake = next(row for row in rows if row["slug"] == "intake")
    assert intake["fsm_stage"] == "task_intake"
    developer = next(row for row in rows if row["slug"] == "developer")
    assert developer["fsm_stage"] is None


@pytest.mark.asyncio
async def test_get_ship_default_returns_body(v1_client, seed_workspace) -> None:
    _, raw, _ = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    res = await v1_client.get("/v1/agent-roles/system", headers=headers)
    assert res.status_code == 200
    body = res.json()
    assert body["slug"] == "system"
    assert body["name"]
    assert isinstance(body["prompt"], str) and body["prompt"].strip()


@pytest.mark.asyncio
async def test_get_unknown_default_is_404(v1_client, seed_workspace) -> None:
    _, raw, _ = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    res = await v1_client.get("/v1/agent-roles/nope", headers=headers)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_invalid_slug_format_is_400(v1_client, seed_workspace) -> None:
    _, raw, _ = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    res = await v1_client.get("/v1/agent-roles/Invalid_Slug", headers=headers)
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_workspace_override_then_revert(
    v1_client, seed_workspace
) -> None:
    """Override a Ship default, resolve picks override, delete reverts."""
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    base = f"/v1/workspaces/{workspace.id}/agent-roles"

    # Pre-condition: resolve falls back to Ship default.
    res = await v1_client.get(f"{base}/developer/resolve", headers=headers)
    assert res.status_code == 200
    assert res.json()["source"] == "ship_default"
    default_prompt = res.json()["prompt"]

    # Create override (same slug as Ship default).
    create = await v1_client.post(
        base,
        headers=headers,
        json={
            "slug": "developer",
            "name": "Developer (workspace tweak)",
            "prompt": "tweaked prompt body",
        },
    )
    assert create.status_code == 201, create.text
    assert create.json()["base_role_slug"] is None

    # Resolve now returns workspace prompt + carries Ship default's fsm_stage.
    res2 = await v1_client.get(f"{base}/developer/resolve", headers=headers)
    assert res2.status_code == 200
    body = res2.json()
    assert body["source"] == "workspace"
    assert body["prompt"] == "tweaked prompt body"

    # Delete — reverts to Ship default.
    delete = await v1_client.delete(f"{base}/developer", headers=headers)
    assert delete.status_code == 204
    res3 = await v1_client.get(f"{base}/developer/resolve", headers=headers)
    assert res3.status_code == 200
    assert res3.json()["source"] == "ship_default"
    assert res3.json()["prompt"] == default_prompt


@pytest.mark.asyncio
async def test_workspace_clone_with_base(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    base = f"/v1/workspaces/{workspace.id}/agent-roles"

    create = await v1_client.post(
        base,
        headers=headers,
        json={
            "slug": "developer-mobile",
            "name": "Mobile developer",
            "prompt": "mobile-specific prompt",
            "base_role_slug": "developer",
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["slug"] == "developer-mobile"
    assert body["base_role_slug"] == "developer"

    # Resolve picks the workspace clone — fsm_stage inherits from base.
    res = await v1_client.get(
        f"{base}/developer-mobile/resolve", headers=headers
    )
    assert res.status_code == 200
    assert res.json()["source"] == "workspace"
    assert res.json()["prompt"] == "mobile-specific prompt"


@pytest.mark.asyncio
async def test_clone_with_unknown_base_is_400(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    base = f"/v1/workspaces/{workspace.id}/agent-roles"

    res = await v1_client.post(
        base,
        headers=headers,
        json={
            "slug": "custom-role",
            "name": "Custom",
            "prompt": "body",
            "base_role_slug": "no-such-default",
        },
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_clone_slug_clashes_with_default_is_400(
    v1_client, seed_workspace
) -> None:
    """Posting clone with slug that shadows a default → ask for override path."""
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    base = f"/v1/workspaces/{workspace.id}/agent-roles"

    res = await v1_client.post(
        base,
        headers=headers,
        json={
            "slug": "developer",
            "name": "Should fail",
            "prompt": "body",
            "base_role_slug": "developer",
        },
    )
    assert res.status_code == 400
    assert "drop base_role_slug" in res.json()["detail"]


@pytest.mark.asyncio
async def test_duplicate_slug_is_409(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    base = f"/v1/workspaces/{workspace.id}/agent-roles"

    payload = {"slug": "developer", "name": "First", "prompt": "a"}
    first = await v1_client.post(base, headers=headers, json=payload)
    assert first.status_code == 201

    second = await v1_client.post(base, headers=headers, json=payload)
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_update_workspace_role(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    base = f"/v1/workspaces/{workspace.id}/agent-roles"

    create = await v1_client.post(
        base,
        headers=headers,
        json={"slug": "developer", "name": "v1", "prompt": "body v1"},
    )
    assert create.status_code == 201

    upd = await v1_client.put(
        f"{base}/developer",
        headers=headers,
        json={"name": "v2", "prompt": "body v2"},
    )
    assert upd.status_code == 200
    assert upd.json()["name"] == "v2"
    assert upd.json()["prompt"] == "body v2"


@pytest.mark.asyncio
async def test_update_with_no_fields_is_400(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    base = f"/v1/workspaces/{workspace.id}/agent-roles"

    create = await v1_client.post(
        base,
        headers=headers,
        json={"slug": "developer", "name": "v1", "prompt": "body"},
    )
    assert create.status_code == 201

    upd = await v1_client.put(
        f"{base}/developer", headers=headers, json={}
    )
    assert upd.status_code == 400


@pytest.mark.asyncio
async def test_resolve_unknown_slug_is_404(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    res = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/agent-roles/no-such/resolve",
        headers=headers,
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_list_workspace_roles_starts_empty(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    res = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/agent-roles", headers=headers
    )
    assert res.status_code == 200
    assert res.json() == {"roles": []}
