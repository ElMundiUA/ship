"""Tests for first-party native integration install APIs."""

from __future__ import annotations

import pytest
from sqlalchemy import select


@pytest.mark.asyncio
async def test_admin_can_connect_azure_devops_pat(
    v1_client,
    db_session,
    seed_workspace,
    monkeypatch,
) -> None:
    from backend.app.db.models.integrations import (
        NativeIntegrationAuditEvent,
        NativeIntegrationCredential,
        NativeIntegrationInstallation,
    )
    from backend.app.security.encryption import decrypt

    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    async def _fake_probe(*, organization, pat, project):
        assert organization == "acme-corp"
        assert pat == "ado_pat_secret"
        assert project == "Platform"
        return True, None

    monkeypatch.setattr(
        "backend.app.api.v1.routes.native_integrations._probe_azure_devops_pat",
        _fake_probe,
    )

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
    assert body["status"] == "ready"
    assert body["last_health_error"] is None
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
async def test_admin_can_save_azure_devops_pat_with_failed_probe(
    v1_client,
    seed_workspace,
    monkeypatch,
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    async def _fake_probe(*, organization, pat, project):
        return False, "Azure DevOps rejected the PAT or required scopes."

    monkeypatch.setattr(
        "backend.app.api.v1.routes.native_integrations._probe_azure_devops_pat",
        _fake_probe,
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/native-integrations/azure-devops/pat",
        headers=headers,
        json={"organization": "acme-corp", "pat": "bad_pat"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "azure_devops"
    assert body["status"] == "error"
    assert body["last_health_error"] == "Azure DevOps rejected the PAT or required scopes."
    assert body["has_credential"] is True


@pytest.mark.asyncio
async def test_admin_can_connect_gitlab_pat(
    v1_client,
    db_session,
    seed_workspace,
    monkeypatch,
) -> None:
    from backend.app.db.models.integrations import (
        NativeIntegrationCredential,
        NativeIntegrationInstallation,
    )
    from backend.app.security.encryption import decrypt

    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    async def _fake_probe(*, base_url, pat, group):
        assert base_url == "https://gitlab.example.com"
        assert pat == "gitlab_pat_secret"
        assert group == "platform/core"
        return True, None

    monkeypatch.setattr(
        "backend.app.api.v1.routes.native_integrations._probe_gitlab_pat",
        _fake_probe,
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/native-integrations/gitlab/pat",
        headers=headers,
        json={
            "host": "https://gitlab.example.com",
            "group": "platform/core",
            "pat": "gitlab_pat_secret",
            "scopes": ["read_api", "read_repository"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "gitlab"
    assert body["auth_mode"] == "pat"
    assert body["external_account_id"] == "gitlab.example.com"
    assert body["external_account_name"] == "platform/core"
    assert body["external_account_url"] == "https://gitlab.example.com"
    assert body["capabilities"] == ["code_host", "orchestrator"]
    assert body["status"] == "ready"
    assert body["has_credential"] is True
    assert "pat" not in body

    installation = (
        await db_session.execute(select(NativeIntegrationInstallation))
    ).scalar_one()
    credential = (
        await db_session.execute(select(NativeIntegrationCredential))
    ).scalar_one()
    assert credential.kind == "pat"
    assert credential.installation_id == installation.id
    assert decrypt(credential.secret_ciphertext) == "gitlab_pat_secret"


@pytest.mark.asyncio
async def test_admin_can_save_gitlab_pat_with_failed_probe(
    v1_client,
    seed_workspace,
    monkeypatch,
) -> None:
    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    async def _fake_probe(*, base_url, pat, group):
        return False, "GitLab rejected the PAT or required scopes."

    monkeypatch.setattr(
        "backend.app.api.v1.routes.native_integrations._probe_gitlab_pat",
        _fake_probe,
    )

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/native-integrations/gitlab/pat",
        headers=headers,
        json={"host": "gitlab.com", "pat": "bad_pat"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "gitlab"
    assert body["status"] == "error"
    assert body["last_health_error"] == "GitLab rejected the PAT or required scopes."
    assert body["has_credential"] is True


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
async def test_admin_can_disable_native_integration(
    v1_client,
    db_session,
    seed_workspace,
    monkeypatch,
) -> None:
    from backend.app.db.models.integrations import (
        NativeIntegrationAuditEvent,
        NativeIntegrationCredential,
        NativeIntegrationInstallation,
    )

    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}

    async def _fake_probe(*, base_url, pat, group):
        return True, None

    monkeypatch.setattr(
        "backend.app.api.v1.routes.native_integrations._probe_gitlab_pat",
        _fake_probe,
    )

    created = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/native-integrations/gitlab/pat",
        headers=headers,
        json={"host": "gitlab.com", "pat": "gitlab_pat_secret"},
    )
    assert created.status_code == 200, created.text
    installation_id = created.json()["id"]

    response = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/native-integrations/{installation_id}",
        headers=headers,
    )

    assert response.status_code == 204, response.text
    installation = (
        await db_session.execute(select(NativeIntegrationInstallation))
    ).scalar_one()
    assert installation.status == "disabled"
    assert installation.disabled_at is not None

    credential = (
        await db_session.execute(select(NativeIntegrationCredential))
    ).scalar_one()
    assert credential.revoked_at is not None

    actions = (
        await db_session.execute(
            select(NativeIntegrationAuditEvent.action).order_by(
                NativeIntegrationAuditEvent.created_at
            )
        )
    ).scalars().all()
    assert actions == ["native_integration.create", "native_integration.disable"]


@pytest.mark.asyncio
async def test_admin_can_probe_native_integration(
    v1_client,
    db_session,
    seed_workspace,
    monkeypatch,
) -> None:
    from backend.app.db.models.integrations import NativeIntegrationAuditEvent

    _, raw, workspace = seed_workspace
    headers = {"Authorization": f"Bearer {raw}"}
    outcomes = [(False, "GitLab rejected the PAT or required scopes."), (True, None)]

    async def _fake_probe(*, base_url, pat, group):
        assert base_url == "https://gitlab.com"
        assert pat == "gitlab_pat_secret"
        assert group is None
        return outcomes.pop(0)

    monkeypatch.setattr(
        "backend.app.api.v1.routes.native_integrations._probe_gitlab_pat",
        _fake_probe,
    )

    created = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/native-integrations/gitlab/pat",
        headers=headers,
        json={"host": "gitlab.com", "pat": "gitlab_pat_secret"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "error"
    assert body["last_health_error"] == "GitLab rejected the PAT or required scopes."

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/native-integrations/{body['id']}/probe",
        headers=headers,
    )

    assert response.status_code == 200, response.text
    probed = response.json()
    assert probed["status"] == "ready"
    assert probed["last_health_error"] is None
    assert probed["has_credential"] is True

    actions = (
        await db_session.execute(
            select(NativeIntegrationAuditEvent.action).order_by(
                NativeIntegrationAuditEvent.created_at
            )
        )
    ).scalars().all()
    assert actions == ["native_integration.create", "native_integration.probe"]


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
