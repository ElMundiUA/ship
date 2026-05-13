"""HTTP tests for workspace prose-rule policies (Workspace policy injection).

The policies API is a thin CRUD over :class:`WorkspacePolicy`
rows; the interesting behaviour (rendering them as a markdown
preamble for the chat agent / shipctl) lives in
``services.policies``. These tests pin the API contract: list
order, optimistic concurrency-free PATCH semantics, RBAC, and the
shared rendering helper.
"""

from __future__ import annotations

import pytest

from backend.app.services.policies import (
    format_policies_preamble,
    render_policies_preamble,
)


@pytest.mark.asyncio
async def test_create_list_patch_delete(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies",
        headers=headers,
        json={
            "title": "Always work via PR",
            "body": "Never push directly to main.",
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["title"] == "Always work via PR"
    assert body["enabled"] is True
    assert body["sort_order"] == 0
    policy_id = body["id"]

    listing = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/policies", headers=headers
    )
    assert listing.status_code == 200
    assert len(listing.json()["policies"]) == 1

    patch = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/policies/{policy_id}",
        headers=headers,
        json={"enabled": False, "sort_order": 5},
    )
    assert patch.status_code == 200
    assert patch.json()["enabled"] is False
    assert patch.json()["sort_order"] == 5

    delete = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/policies/{policy_id}", headers=headers
    )
    assert delete.status_code == 204
    listing2 = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/policies", headers=headers
    )
    assert listing2.json()["policies"] == []


@pytest.mark.asyncio
async def test_list_order_by_sort_then_created(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    titles = ["A", "B", "C"]
    sort_orders = [10, 0, 5]
    for title, so in zip(titles, sort_orders):
        resp = await v1_client.post(
            f"/v1/workspaces/{workspace.id}/policies",
            headers=headers,
            json={"title": title, "body": f"body of {title}", "sort_order": so},
        )
        assert resp.status_code == 201, resp.text

    listing = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/policies", headers=headers
    )
    titles_returned = [p["title"] for p in listing.json()["policies"]]
    # sort_order asc: B(0) < C(5) < A(10)
    assert titles_returned == ["B", "C", "A"]


def test_format_preamble_returns_none_for_empty() -> None:
    assert format_policies_preamble([]) is None


def test_format_preamble_includes_title_and_body() -> None:
    class _Stub:
        def __init__(self, title: str, body: str) -> None:
            self.title = title
            self.body = body

    out = format_policies_preamble(
        [
            _Stub("Always work via PR", "Never push to main."),
            _Stub("Never commit secrets", "Use repo Actions secrets."),
        ]
    )
    assert out is not None
    assert out.startswith("# Workspace policies\n")
    assert "## Always work via PR" in out
    assert "Never push to main." in out
    assert "## Never commit secrets" in out
    assert "Use repo Actions secrets." in out


@pytest.mark.asyncio
async def test_create_with_applies_to_roles(v1_client, seed_workspace) -> None:
    """``applies_to_roles`` round-trips through create + list. Empty
    list normalises to ``null`` server-side so the client sees one
    consistent shape regardless of how admins edited the
    multi-select."""
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies",
        headers=headers,
        json={
            "title": "Developer-scoped rule",
            "body": "Run lint before opening a PR.",
            "applies_to_roles": ["developer"],
        },
    )
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["applies_to_roles"] == ["developer"]
    policy_id = body["id"]

    listing = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/policies", headers=headers
    )
    fetched = next(
        p for p in listing.json()["policies"] if p["id"] == policy_id
    )
    assert fetched["applies_to_roles"] == ["developer"]

    # Empty list — must normalise to ``null`` to keep the renderer's
    # global semantic.
    create_global = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies",
        headers=headers,
        json={
            "title": "Global rule",
            "body": "Applies to everyone.",
            "applies_to_roles": [],
        },
    )
    assert create_global.status_code == 201
    assert create_global.json()["applies_to_roles"] is None


@pytest.mark.asyncio
async def test_patch_applies_to_roles_clear_to_global(
    v1_client, seed_workspace
) -> None:
    """PATCH semantics: ``null`` clears the scope (back to global);
    omitting the key leaves it untouched. Pin both branches so the
    Console's "edit chip away" gesture never accidentally widens or
    narrows a rule."""
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies",
        headers=headers,
        json={
            "title": "Scoped",
            "body": "BA-scoped.",
            "applies_to_roles": ["ba"],
        },
    )
    policy_id = create.json()["id"]

    # Omit the key — scope must remain ['ba'].
    patch_other = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/policies/{policy_id}",
        headers=headers,
        json={"sort_order": 5},
    )
    assert patch_other.json()["applies_to_roles"] == ["ba"]

    # Send null — scope clears to global.
    patch_clear = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/policies/{policy_id}",
        headers=headers,
        json={"applies_to_roles": None},
    )
    assert patch_clear.json()["applies_to_roles"] is None

    # Re-scope to multiple roles.
    patch_multi = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/policies/{policy_id}",
        headers=headers,
        json={"applies_to_roles": ["ba", "intake"]},
    )
    assert sorted(patch_multi.json()["applies_to_roles"]) == ["ba", "intake"]


@pytest.mark.asyncio
async def test_invalid_role_slug_is_422(v1_client, seed_workspace) -> None:
    """A stray ``"DROP TABLE"`` or whitespace doesn't silently
    round-trip into the policy column — server rejects with 422."""
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    bad = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/policies",
        headers=headers,
        json={
            "title": "Should fail",
            "body": "x",
            "applies_to_roles": ["Developer; DROP TABLE"],
        },
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_workspace_scoped_preamble_endpoint(
    v1_client, db_session, seed_workspace
) -> None:
    """The workspace-scoped ``/policies/preamble`` endpoint backs the
    ``shipctl run`` flow (which mints ``run_id`` locally and can't
    use the per-run JWT auth path). Workspace-membership token is
    enough; ``?role=`` opts into role-scoped policies on top of the
    globals."""
    from backend.app.db.models.policies import WorkspacePolicy

    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    db_session.add_all(
        [
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Always work via PR",
                body="Never push directly to main.",
                enabled=True,
                sort_order=0,
                applies_to_roles=None,
            ),
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Run lint before PR",
                body="Developer-only quality gate.",
                enabled=True,
                sort_order=1,
                applies_to_roles=["developer"],
            ),
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="BA rewrites description",
                body="BA-only.",
                enabled=True,
                sort_order=2,
                applies_to_roles=["ba"],
            ),
        ]
    )
    await db_session.flush()

    # No role param → globals only.
    no_role = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/policies/preamble",
        headers=headers,
    )
    assert no_role.status_code == 200
    body_no_role = no_role.json()["preamble"]
    assert "Always work via PR" in body_no_role
    assert "Run lint before PR" not in body_no_role
    assert "BA rewrites description" not in body_no_role

    # ?role=developer → globals ∪ developer-scoped.
    dev = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/policies/preamble?role=developer",
        headers=headers,
    )
    assert dev.status_code == 200
    body_dev = dev.json()["preamble"]
    assert "Always work via PR" in body_dev
    assert "Run lint before PR" in body_dev
    assert "BA rewrites description" not in body_dev

    # Invalid role slug rejected with 422.
    bad = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/policies/preamble?role=Bad%20Slug",
        headers=headers,
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_render_skips_disabled(db_session, seed_workspace) -> None:
    """Disabled rows are not in the preamble even if they sort first."""
    from backend.app.db.models.policies import WorkspacePolicy

    _, _, workspace = seed_workspace
    db_session.add_all(
        [
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="Off",
                body="Should not render.",
                enabled=False,
                sort_order=-100,
            ),
            WorkspacePolicy(
                workspace_id=workspace.id,
                title="On",
                body="Render me.",
                enabled=True,
                sort_order=0,
            ),
        ]
    )
    await db_session.flush()

    rendered = await render_policies_preamble(db_session, workspace.id)
    assert rendered is not None
    assert "Off" not in rendered
    assert "Should not render." not in rendered
    assert "On" in rendered
    assert "Render me." in rendered
