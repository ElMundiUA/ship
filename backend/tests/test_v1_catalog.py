"""HTTP tests for ``/v1/catalog/*`` (Phase 2)."""

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
async def test_list_workflows_exposes_install_target(v1_client, seed_workspace) -> None:
    _, raw, _ = seed_workspace
    response = await v1_client.get(
        "/v1/catalog/workflows",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    by_id = {entry["id"]: entry for entry in response.json()}
    assert (
        by_id["pr-and-ci-gate"]["install_target"]
        == ".github/workflows/pr-and-ci-gate.yml"
    )


@pytest.mark.asyncio
async def test_get_workflow_by_id(v1_client, seed_workspace) -> None:
    _, raw, _ = seed_workspace
    response = await v1_client.get(
        "/v1/catalog/workflows/pipeline-self-heal",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == "pipeline-self-heal"
    assert body["install_target"].endswith("pipeline-self-heal.yml")


@pytest.mark.asyncio
async def test_get_workflow_404(v1_client, seed_workspace) -> None:
    _, raw, _ = seed_workspace
    response = await v1_client.get(
        "/v1/catalog/workflows/does-not-exist",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 404
