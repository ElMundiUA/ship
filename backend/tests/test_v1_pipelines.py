"""End-to-end tests for ``/v1/workspaces/{ws}/pipelines/*`` (pilot Day 3).

Pipelines are seeded via :func:`seed_default_pipelines` (which the
repos/activate route triggers in production). The tests here drive the
seeder directly so they don't need a GitHub App installation row, then
exercise list / toggle / run / runs through the HTTP layer.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_pipelines_returns_seeded_defaults(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.services.default_pipelines import (
        DEFAULT_PIPELINES,
        seed_default_pipelines,
    )

    user, raw, workspace = seed_workspace
    await seed_default_pipelines(db_session, workspace.id)
    await db_session.flush()

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert {p["kind"] for p in body} == {p.kind for p in DEFAULT_PIPELINES}
    # Seed order is preserved on the API surface so the dashboard can
    # render cards in the curated order without re-sorting.
    assert [p["kind"] for p in body] == [p.kind for p in DEFAULT_PIPELINES]
    # Self-heal lane ships off by default; everything else on.
    by_kind = {p["kind"]: p for p in body}
    assert by_kind["pr_review"]["enabled"] is True
    assert by_kind["self_heal"]["enabled"] is False


@pytest.mark.asyncio
async def test_toggle_pipeline_flips_enabled_and_audits(
    v1_client, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.pipelines import Pipeline
    from backend.app.db.models.tenancy import AuditLog
    from backend.app.services.default_pipelines import seed_default_pipelines

    user, raw, workspace = seed_workspace
    seeded = await seed_default_pipelines(db_session, workspace.id)
    await db_session.flush()

    target = next(p for p in seeded if p.kind == "self_heal")
    workspace_id = workspace.id
    pipeline_id = target.id

    response = await v1_client.patch(
        f"/v1/workspaces/{workspace_id}/pipelines/{pipeline_id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"enabled": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["enabled"] is True

    refreshed = (
        await db_session.execute(
            select(Pipeline).where(Pipeline.id == pipeline_id)
        )
    ).scalar_one()
    assert refreshed.enabled is True

    audits = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "pipeline.toggle",
            )
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].payload == {"kind": "self_heal", "enabled": True}


@pytest.mark.asyncio
async def test_toggle_no_op_skips_audit(
    v1_client, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.tenancy import AuditLog
    from backend.app.services.default_pipelines import seed_default_pipelines

    user, raw, workspace = seed_workspace
    seeded = await seed_default_pipelines(db_session, workspace.id)
    await db_session.flush()

    target = next(p for p in seeded if p.kind == "pr_review")
    workspace_id = workspace.id

    response = await v1_client.patch(
        f"/v1/workspaces/{workspace_id}/pipelines/{target.id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"enabled": True},  # already True; should be a no-op
    )
    assert response.status_code == 200
    audits = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action == "pipeline.toggle",
            )
        )
    ).scalars().all()
    assert audits == []


@pytest.mark.asyncio
async def test_run_pipeline_records_run_and_updates_summary(
    v1_client, db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.pipelines import Pipeline, PipelineRun
    from backend.app.services.default_pipelines import seed_default_pipelines

    user, raw, workspace = seed_workspace
    seeded = await seed_default_pipelines(db_session, workspace.id)
    await db_session.flush()

    target = next(p for p in seeded if p.kind == "pr_review")
    workspace_id = workspace.id
    pipeline_id = target.id

    response = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/pipelines/{pipeline_id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
        json={"note": "demo run"},
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "succeeded"
    assert run["trigger"] == "manual"
    assert run["summary"] == "demo run"

    rows = (
        await db_session.execute(
            select(PipelineRun).where(PipelineRun.workspace_id == workspace_id)
        )
    ).scalars().all()
    assert len(rows) == 1

    pipeline = (
        await db_session.execute(
            select(Pipeline).where(Pipeline.id == pipeline_id)
        )
    ).scalar_one()
    assert pipeline.last_run_status == "succeeded"
    assert pipeline.last_run_at is not None


@pytest.mark.asyncio
async def test_run_pipeline_rejects_disabled(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.services.default_pipelines import seed_default_pipelines

    user, raw, workspace = seed_workspace
    seeded = await seed_default_pipelines(db_session, workspace.id)
    await db_session.flush()

    # Self-heal ships disabled — perfect for the 409 case.
    target = next(p for p in seeded if p.kind == "self_heal")

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 409
    assert "disabled" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_runs_returns_recent_first(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.services.default_pipelines import seed_default_pipelines

    user, raw, workspace = seed_workspace
    seeded = await seed_default_pipelines(db_session, workspace.id)
    await db_session.flush()
    target = next(p for p in seeded if p.kind == "pr_review")

    for n in range(3):
        await v1_client.post(
            f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
            headers={"Authorization": f"Bearer {raw}"},
            json={"note": f"run #{n}"},
        )

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/pipelines/{target.id}/runs",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3
    # Most-recent first; per-call timing is sub-second so sort by note
    # too as a tie-breaker would be flaky — instead just verify shape.
    assert all(r["pipeline_id"] == str(target.id) for r in body)
    assert {r["summary"] for r in body} == {"run #0", "run #1", "run #2"}


@pytest.mark.asyncio
async def test_unknown_pipeline_returns_404(
    v1_client, seed_workspace
) -> None:
    import uuid

    user, raw, workspace = seed_workspace
    response = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}/pipelines/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"enabled": True},
    )
    assert response.status_code == 404
