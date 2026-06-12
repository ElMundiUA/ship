"""Workspace agent_provider binding (PR-1).

Covers the resolver default, the GET/PUT routes, the audit row, and
the validation guard rails (CHECK constraint + 422 on unsupported
kind).
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_resolver_returns_default_for_fresh_workspace(
    db_session, seed_workspace
) -> None:
    from backend.app.services.agent_provider_resolver import (
        DEFAULT_PROVIDER,
        resolve_for_workspace,
    )

    _, _, workspace = seed_workspace
    resolved = await resolve_for_workspace(
        session=db_session, workspace_id=workspace.id
    )
    assert resolved.kind == DEFAULT_PROVIDER
    assert resolved.workspace_id == workspace.id


@pytest.mark.asyncio
async def test_resolver_reads_bound_kind(
    db_session, seed_workspace
) -> None:
    from backend.app.services.agent_provider_resolver import (
        resolve_for_workspace,
    )

    _, _, workspace = seed_workspace
    workspace.agent_provider = "claude"
    await db_session.flush()

    resolved = await resolve_for_workspace(
        session=db_session, workspace_id=workspace.id
    )
    assert resolved.kind == "claude"


@pytest.mark.asyncio
async def test_get_agent_provider_returns_default_and_supported(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/agent-provider",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workspace_id"] == str(workspace.id)
    assert body["kind"] == "cursor"
    assert body["supported"] == ["claude", "codex", "cursor", "ship"]  # ship: ELS-241 self-spawn (dogfood-gated)


@pytest.mark.asyncio
async def test_put_agent_provider_changes_kind_and_audits(
    db_session, v1_client, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.tenancy import AuditLog, Workspace

    _, raw, workspace = seed_workspace
    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/agent-provider",
        json={"kind": "codex"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["kind"] == "codex"

    refreshed = await db_session.get(Workspace, workspace.id)
    assert refreshed.agent_provider == "codex"

    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "workspace.agent_provider.set",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload == {"from": "cursor", "to": "codex"}


@pytest.mark.asyncio
async def test_put_agent_provider_idempotent_no_audit(
    db_session, v1_client, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.tenancy import AuditLog

    _, raw, workspace = seed_workspace
    # Workspace defaults to cursor; PUT'ing the same value is a no-op.
    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/agent-provider",
        json={"kind": "cursor"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text

    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "workspace.agent_provider.set",
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_put_agent_provider_rejects_unknown(
    v1_client, seed_workspace
) -> None:
    _, raw, workspace = seed_workspace
    resp = await v1_client.put(
        f"/v1/workspaces/{workspace.id}/agent-provider",
        json={"kind": "copilot"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 422, resp.text
    assert "agent_provider must be one of" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_patch_workspace_can_set_agent_provider(
    db_session, v1_client, seed_workspace
) -> None:
    from backend.app.db.models.tenancy import Workspace

    _, raw, workspace = seed_workspace
    resp = await v1_client.patch(
        f"/v1/workspaces/{workspace.id}",
        json={"agent_provider": "claude"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["agent_provider"] == "claude"

    refreshed = await db_session.get(Workspace, workspace.id)
    assert refreshed.agent_provider == "claude"
