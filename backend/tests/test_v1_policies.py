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
