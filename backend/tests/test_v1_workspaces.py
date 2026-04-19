"""End-to-end tests for the v1 tenancy slice (RFC-0006 acceptance).

Skipped automatically when the local Postgres stack is not running — see
``backend/tests/db_conftest.py``.
"""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_health_reports_database_ok(v1_client) -> None:
    response = await v1_client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


@pytest.mark.asyncio
async def test_workspaces_require_auth(v1_client) -> None:
    response = await v1_client.get("/v1/workspaces")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_user_can_create_and_list_workspace(
    v1_client, seed_user_with_token
) -> None:
    _, raw_token = seed_user_with_token
    headers = {"Authorization": f"Bearer {raw_token}"}

    create = await v1_client.post(
        "/v1/workspaces",
        headers=headers,
        json={"name": "Acme Mobile", "slug": "acme-mobile"},
    )
    assert create.status_code == 201, create.text
    created = create.json()
    assert created["slug"] == "acme-mobile"
    assert created["catalog_sources"] == {
        "global": True,
        "workspace": True,
        "project": True,
    }

    listed = await v1_client.get("/v1/workspaces", headers=headers)
    assert listed.status_code == 200
    payload = listed.json()
    assert any(ws["id"] == created["id"] for ws in payload)


@pytest.mark.asyncio
async def test_workspace_slug_must_be_unique_per_org(
    v1_client, seed_user_with_token
) -> None:
    _, raw_token = seed_user_with_token
    headers = {"Authorization": f"Bearer {raw_token}"}

    first = await v1_client.post(
        "/v1/workspaces",
        headers=headers,
        json={"name": "First", "slug": "dup"},
    )
    assert first.status_code == 201

    second = await v1_client.post(
        "/v1/workspaces",
        headers=headers,
        json={"name": "Second", "slug": "dup"},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_get_workspace_returns_404_for_strangers(
    v1_client, seed_user_with_token
) -> None:
    _, raw_token = seed_user_with_token
    headers = {"Authorization": f"Bearer {raw_token}"}
    response = await v1_client.get(
        f"/v1/workspaces/{uuid.uuid4()}", headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_delete_workspace_requires_slug_confirmation(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    bad = await v1_client.request(
        "DELETE",
        f"/v1/workspaces/{ws.id}",
        headers=headers,
        json={"slug_confirmation": "not-the-slug"},
    )
    assert bad.status_code == 409
    # Workspace must still be visible after the failed delete.
    still = await v1_client.get(f"/v1/workspaces/{ws.id}", headers=headers)
    assert still.status_code == 200


@pytest.mark.asyncio
async def test_delete_workspace_owner_succeeds(
    v1_client, seed_workspace
) -> None:
    _, raw, ws = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    res = await v1_client.request(
        "DELETE",
        f"/v1/workspaces/{ws.id}",
        headers=headers,
        json={"slug_confirmation": ws.slug},
    )
    assert res.status_code == 204, res.text

    after = await v1_client.get(f"/v1/workspaces/{ws.id}", headers=headers)
    # 404 because membership row was cascaded — same hide-from-strangers rule
    # as the get endpoint applies.
    assert after.status_code == 404
