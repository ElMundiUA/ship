"""HTTP tests for workspace-private catalog patterns (RFC-0008 §H — PR-6).

Covers the end-to-end CRUD, baked-in collision guards, referenced-by
guards on delete, and the merge contract at
``GET /v1/catalog/patterns?workspace_id=<ws>``. The LLM draft
endpoint's happy-path is exercised with a fake :class:`AgentClient`
injected via ``monkeypatch`` so the test suite stays offline.

The draft *contract* test is deliberately small: we pin that the
route accepts a brief, asks the LLM once with JSON mode, coerces the
response through :class:`PatternDraft`, and forces the ``custom-``
prefix. Richer prompt/output assertions belong in a future
evaluation suite where real model outputs are worth chasing.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

import pytest
import pytest_asyncio


BUILTIN_PATTERN_ID = "flow-daily-retro"


@pytest_asyncio.fixture
async def seed_pattern_workspace(db_session, seed_workspace):
    _, raw, workspace = seed_workspace
    return raw, workspace


class _FakeAgentClient:
    """Minimal :class:`AgentClient` stand-in for the draft endpoint."""

    vendor = "fake"

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    async def acomplete(
        self,
        messages: Sequence[Any],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        self.calls.append(
            {
                "messages": [m.content for m in messages],
                "response_format": response_format,
            }
        )
        return json.dumps(self._payload)


@pytest.mark.asyncio
async def test_create_and_list_custom_pattern(
    v1_client, seed_pattern_workspace
) -> None:
    raw, workspace = seed_pattern_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/patterns",
        headers=headers,
        json={
            "pattern_id": "custom-nightly-dep-audit",
            "name": "Nightly dep audit",
            "description": "Runs npm audit + python safety at 03:00 UTC.",
            "category": "scan",
            "modes": ["lane", "request"],
            "inputs": [{"id": "target_dir", "label": "Target dir", "type": "string"}],
            "spec": {"default_trigger": {"schedule": "0 3 * * *"}},
            "body": "Audit dependencies in ${target_dir} and open a PR with fixes.",
        },
    )
    assert create.status_code == 201, create.text
    created = create.json()
    assert created["pattern_id"] == "custom-nightly-dep-audit"
    assert created["modes"] == ["lane", "request"]

    listing = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/patterns", headers=headers
    )
    assert listing.status_code == 200
    assert len(listing.json()) == 1


@pytest.mark.asyncio
async def test_duplicate_pattern_id_conflicts(
    v1_client, seed_pattern_workspace
) -> None:
    raw, workspace = seed_pattern_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    base = {
        "pattern_id": "custom-unique",
        "name": "First",
        "modes": ["request"],
        "body": "echo ${foo}",
        "inputs": [{"id": "foo", "label": "Foo"}],
    }
    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/patterns", headers=headers, json=base
    )
    assert first.status_code == 201, first.text
    dup = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/patterns",
        headers=headers,
        json={**base, "name": "Second"},
    )
    assert dup.status_code == 409
    assert dup.json()["detail"]["code"] == "pattern_id_conflict"


@pytest.mark.asyncio
async def test_builtin_collision_rejected(
    v1_client, seed_pattern_workspace
) -> None:
    raw, workspace = seed_pattern_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/patterns",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "pattern_id": BUILTIN_PATTERN_ID,
            "name": "Shadow",
            "modes": ["lane"],
            "body": "noop",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "pattern_id_reserved"


@pytest.mark.asyncio
async def test_invalid_pattern_id_422(v1_client, seed_pattern_workspace) -> None:
    raw, workspace = seed_pattern_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/patterns",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "pattern_id": "Bad ID!",
            "name": "Bad",
            "modes": ["request"],
            "body": "noop",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_catalog_merge_with_workspace_id(
    v1_client, seed_pattern_workspace
) -> None:
    raw, workspace = seed_pattern_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    await v1_client.post(
        f"/v1/workspaces/{workspace.id}/patterns",
        headers=headers,
        json={
            "pattern_id": "custom-merge-check",
            "name": "Merge check",
            "modes": ["request"],
            "body": "noop",
        },
    )

    # Without ``workspace_id`` the baked-in catalog has no record of
    # the custom pattern.
    baseline = await v1_client.get("/v1/catalog/patterns", headers=headers)
    assert baseline.status_code == 200
    baseline_ids = {p["id"] for p in baseline.json()}
    assert "custom-merge-check" not in baseline_ids

    merged = await v1_client.get(
        f"/v1/catalog/patterns?workspace_id={workspace.id}", headers=headers
    )
    assert merged.status_code == 200
    merged_rows = merged.json()
    merged_by_id = {p["id"]: p for p in merged_rows}
    # Baked-in entries still present...
    assert len(merged_rows) > len(baseline_ids)
    # ...and the workspace-private row shows up with the right source
    # tag so the Console can badge it.
    assert "custom-merge-check" in merged_by_id
    assert merged_by_id["custom-merge-check"]["source"] == "workspace"


@pytest.mark.asyncio
async def test_delete_custom_pattern(
    v1_client, seed_pattern_workspace
) -> None:
    raw, workspace = seed_pattern_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/patterns",
        headers=headers,
        json={
            "pattern_id": "custom-disposable",
            "name": "Disposable",
            "modes": ["request"],
            "body": "noop",
        },
    )
    row_id = create.json()["id"]

    resp = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/patterns/{row_id}", headers=headers
    )
    assert resp.status_code == 204

    listing = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/patterns", headers=headers
    )
    assert listing.json() == []


@pytest.mark.asyncio
async def test_delete_blocked_by_fleet_lane(
    v1_client, seed_pattern_workspace, db_session
) -> None:
    """A custom pattern referenced by an active Fleet lane can't be deleted.

    We insert the :class:`FleetLane` row directly (bypasses the
    fleet-lanes API, which validates against the baked-in catalog
    for ``modes`` — irrelevant here; the delete-guard reads
    ``pattern_id`` textually off the DB).
    """
    from backend.app.db.models.fleet_lanes import FleetLane

    raw, workspace = seed_pattern_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    create = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/patterns",
        headers=headers,
        json={
            "pattern_id": "custom-guarded",
            "name": "Guarded",
            "modes": ["lane"],
            "body": "noop",
        },
    )
    row_id = create.json()["id"]

    db_session.add(
        FleetLane(
            workspace_id=workspace.id,
            kind="mirror_lane",
            name="Guarded mirror",
            pattern_id="custom-guarded",
            lane_id="guarded_lane",
            cadence="@daily",
            inputs={},
        )
    )
    await db_session.flush()

    resp = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/patterns/{row_id}", headers=headers
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "pattern_in_use_fleet_lane"


@pytest.mark.asyncio
async def test_draft_endpoint_happy_path(
    v1_client, seed_pattern_workspace, monkeypatch
) -> None:
    """LLM draft endpoint happy-path with a fake client.

    Verifies that the route:
    * asks the LLM once in JSON mode,
    * coerces the response through :class:`PatternDraft`,
    * enforces the ``custom-`` prefix even when the model drops it.
    """
    fake = _FakeAgentClient(
        {
            "pattern_id": "nightly-seo",  # no prefix — route should add it
            "name": "Nightly SEO audit",
            "description": "Runs lighthouse on the blog every night.",
            "category": "scan",
            "modes": ["lane"],
            "inputs": [
                {"id": "site_url", "label": "Site URL", "required": True}
            ],
            "spec": {"default_trigger": {"schedule": "0 2 * * *"}},
            "body": "Audit ${site_url} with lighthouse and open a PR.",
        }
    )

    # Patch the factory inside the route module so no real API key is
    # needed in tests.
    import backend.app.api.v1.routes.custom_patterns as mod

    monkeypatch.setattr(mod, "pick_default_client", lambda settings: fake)

    raw, workspace = seed_pattern_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/patterns/draft",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "prompt": "Every night, audit the marketing site with lighthouse and open a PR with fixes.",
            "target_modes": ["lane"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pattern_id"] == "custom-nightly-seo"
    assert body["modes"] == ["lane"]
    assert body["inputs"][0]["id"] == "site_url"
    # One upstream call, JSON mode, brief forwarded verbatim.
    assert len(fake.calls) == 1
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    assert "lighthouse" in "\n".join(fake.calls[0]["messages"])


@pytest.mark.asyncio
async def test_draft_endpoint_llm_unconfigured_returns_412(
    v1_client, seed_pattern_workspace, monkeypatch
) -> None:
    import backend.app.api.v1.routes.custom_patterns as mod

    def _raise(_settings):
        raise RuntimeError("No LLM API key configured.")

    monkeypatch.setattr(mod, "pick_default_client", _raise)

    raw, workspace = seed_pattern_workspace
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/patterns/draft",
        headers={"Authorization": f"Bearer {raw}"},
        json={"prompt": "Something useful to automate across repos every Monday."},
    )
    assert resp.status_code == 412
    assert resp.json()["detail"]["code"] == "llm_unconfigured"
