"""HTTP tests for ``/v1/catalog/*`` (Phase 2).

RFC-0007 Phase 6 retired the ``/v1/catalog/workflows`` routes alongside
``artifact_kind=workflow``; presets/collections/patterns/tools remain.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_presets_requires_auth(v1_client) -> None:
    response = await v1_client.get("/v1/catalog/presets")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_presets_returns_catalog_presets(v1_client, seed_workspace) -> None:
    _, raw, _ = seed_workspace
    response = await v1_client.get(
        "/v1/catalog/presets",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    ids = {entry["id"] for entry in body}
    assert "preset-web-app" in ids
    assert "preset-api-backend" in ids
    for entry in body:
        assert entry["group"] == "preset"
        assert entry["preset_id"]


@pytest.mark.asyncio
async def test_workflows_route_removed(v1_client, seed_workspace) -> None:
    """Phase 6 removed ``/v1/catalog/workflows`` — no more catalog kind."""
    _, raw, _ = seed_workspace
    response = await v1_client.get(
        "/v1/catalog/workflows",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 404
