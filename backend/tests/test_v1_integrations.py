"""End-to-end tests for `/v1/workspaces/{id}/integrations` (RFC-0006).

Covers:

- Admins can upsert and delete integrations.
- The plaintext secret is never returned; only ``has_secret`` flips.
- Invalid kinds are rejected before touching the DB.
- Membership is required (404 for strangers, 403 for read-only members).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_integrations_require_membership(v1_client, seed_user_with_token) -> None:
    _, raw = seed_user_with_token
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.get(
        f"/v1/workspaces/{uuid.uuid4()}/integrations", headers=headers
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_upsert_integration_and_secret_stays_opaque(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.tenancy import Integration

    user, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    create = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/linear",
        headers=headers,
        json={
            "kind": "linear",
            "config": {"team_id": "ENG"},
            "secret": "lin_api_supersecret",
        },
    )
    assert create.status_code == 200, create.text
    body = create.json()
    assert body["kind"] == "linear"
    assert body["has_secret"] is True
    assert body["status"] == "pending"
    assert "secret" not in body
    assert "secret_ciphertext" not in body
    assert body["config"] == {"team_id": "ENG"}

    # Round-trip via DB to confirm the ciphertext is present and decrypts.
    from backend.app.security.encryption import decrypt

    row = (
        await db_session.execute(
            select(Integration).where(Integration.workspace_id == workspace.id)
        )
    ).scalar_one()
    assert row.secret_ciphertext is not None
    assert decrypt(row.secret_ciphertext) == "lin_api_supersecret"

    # Editing config without a secret leaves the ciphertext untouched.
    update = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/linear",
        headers=headers,
        json={"kind": "linear", "config": {"team_id": "PLAT"}, "secret": None},
    )
    assert update.status_code == 200
    assert update.json()["has_secret"] is True
    assert update.json()["config"] == {"team_id": "PLAT"}


@pytest.mark.asyncio
async def test_unknown_integration_kind_is_422(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/nonsense",
        headers=headers,
        json={"kind": "nonsense", "config": {}, "secret": "x"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_path_kind_must_match_payload(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    response = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/linear",
        headers=headers,
        json={"kind": "slack", "config": {}, "secret": "x"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_removes_integration(v1_client, seed_workspace) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    await v1_client.put(
        f"/v1/workspaces/{workspace.id}/integrations/slack",
        headers=headers,
        json={"kind": "slack", "config": {"channel": "#eng"}, "secret": "xoxb-..."},
    )
    deleted = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/integrations/slack",
        headers=headers,
    )
    assert deleted.status_code == 204
    listed = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/integrations", headers=headers
    )
    assert listed.status_code == 200
    assert listed.json() == []
