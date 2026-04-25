"""Tests for first-party native integration install APIs."""

from __future__ import annotations

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_admin_can_connect_azure_devops_pat(
    v1_client,
    db_session,
    seed_workspace,
) -> None:
    from backend.app.db.models.integrations import (
        NativeIntegrationAuditEvent,
        NativeIntegrationCredential,
        NativeIntegrationInstallation,
    )
    from backend.app.security.encryption import decrypt

    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/native-integrations/azure-devops/pat",
        headers=headers,
        json={
            "organization": "acme-corp",
            "project": "Platform",
            "pat": "ado_pat_secret",
            "scopes": ["vso.code", "vso.build_execute"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "azure_devops"
    assert body["auth_mode"] == "pat"
    assert body["external_account_id"] == "acme-corp"
    assert body["external_account_url"] == "https://dev.azure.com/acme-corp"
    assert body["capabilities"] == ["code_host", "orchestrator"]
    assert body["status"] == "pending"
    assert body["has_credential"] is True
    assert "pat" not in body

    installation = (
        await db_session.execute(select(NativeIntegrationInstallation))
    ).scalar_one()
    credential = (
        await db_session.execute(select(NativeIntegrationCredential))
    ).scalar_one()
    assert credential.installation_id == installation.id
    assert decrypt(credential.secret_ciphertext) == "ado_pat_secret"

    audit_event = (
        await db_session.execute(select(NativeIntegrationAuditEvent))
    ).scalar_one()
    assert audit_event.provider == "azure_devops"
    assert audit_event.action == "native_integration.create"
    assert audit_event.payload["credential_rotated"] is True


@pytest.mark.asyncio
async def test_list_native_integrations_requires_admin(
    v1_client,
    seed_user_with_token,
) -> None:
    _, raw = seed_user_with_token
    headers = {"Authorization": f"Bearer {raw}"}

    response = await v1_client.get(
        "/v1/workspaces/00000000-0000-0000-0000-000000000000/native-integrations",
        headers=headers,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_can_connect_atlassian_api_token(
    v1_client,
    db_session,
    seed_workspace,
) -> None:
    from backend.app.db.models.integrations import (
        NativeIntegrationCredential,
        NativeIntegrationInstallation,
    )
    from backend.app.db.models.tenancy import Integration
    from backend.app.security.encryption import decrypt

    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/native-integrations/atlassian/api-token",
        headers=headers,
        json={
            "site": "acme.atlassian.net",
            "email": "Owner@Example.com",
            "api_token": "atlassian_secret",
            "jira_project": "eng",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "atlassian"
    assert body["auth_mode"] == "pat"
    assert body["external_account_id"] == "acme.atlassian.net"
    assert body["external_account_url"] == "https://acme.atlassian.net"
    assert body["capabilities"] == ["tracker", "knowledge"]
    assert body["config"]["email"] == "owner@example.com"
    assert body["config"]["jira_project"] == "ENG"
    assert body["has_credential"] is True
    assert "api_token" not in body

    installation = (
        await db_session.execute(select(NativeIntegrationInstallation))
    ).scalar_one()
    credential = (
        await db_session.execute(select(NativeIntegrationCredential))
    ).scalar_one()
    assert credential.kind == "api_token"
    assert credential.installation_id == installation.id
    assert decrypt(credential.secret_ciphertext) == "atlassian_secret"

    confluence = (
        await db_session.execute(
            select(Integration).where(Integration.kind == "confluence")
        )
    ).scalar_one()
    assert confluence.config["site_url"] == "https://acme.atlassian.net"
    assert confluence.config["email"] == "owner@example.com"
    assert decrypt(confluence.secret_ciphertext) == "atlassian_secret"
