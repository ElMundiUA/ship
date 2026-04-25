"""First-party native integration install surfaces."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import ROLES_ADMIN, _require_membership
from backend.app.db.models.integrations import (
    NativeIntegrationAuditEvent,
    NativeIntegrationAuthMode,
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationProvider,
    NativeIntegrationStatus,
)
from backend.app.db.session import get_session
from backend.app.security.encryption import encrypt


router = APIRouter(
    prefix="/workspaces/{workspace_id}/native-integrations",
    tags=["native-integrations"],
)


class NativeIntegrationInstallationOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    provider: str
    auth_mode: str
    external_account_id: str
    external_account_name: str | None
    external_account_url: str | None
    capabilities: list[str]
    scopes: list[str]
    config: dict
    status: str
    has_credential: bool
    last_health_at: datetime | None
    last_health_error: str | None
    connected_at: datetime | None
    disabled_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AzureDevOpsPatInstall(BaseModel):
    organization: str = Field(min_length=1, max_length=255)
    pat: str = Field(min_length=1, max_length=8192)
    project: str | None = Field(default=None, max_length=255)
    scopes: list[str] = Field(default_factory=list, max_length=64)


class AtlassianApiTokenInstall(BaseModel):
    site: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    api_token: str = Field(min_length=1, max_length=8192)
    jira_project: str | None = Field(default=None, max_length=64)
    scopes: list[str] = Field(default_factory=list, max_length=64)


def _normalise_azure_org(value: str) -> str:
    org = value.strip().strip("/")
    if not org:
        raise HTTPException(status_code=422, detail="organization cannot be blank")
    if "/" in org:
        # Keep v1 intentionally narrow: callers pass the Azure DevOps org slug,
        # not an arbitrary URL that might include a project path or query.
        raise HTTPException(
            status_code=422,
            detail="organization must be an Azure DevOps organization slug",
        )
    return org


def _normalise_atlassian_site(value: str) -> tuple[str, str]:
    raw = value.strip().strip("/")
    if not raw:
        raise HTTPException(status_code=422, detail="site cannot be blank")
    if raw.startswith("https://"):
        host = raw.removeprefix("https://").strip("/")
    elif raw.startswith("http://"):
        raise HTTPException(
            status_code=422,
            detail="Atlassian Cloud site must use https",
        )
    else:
        host = raw
    if "/" in host:
        raise HTTPException(
            status_code=422,
            detail="site must be an Atlassian host like yourorg.atlassian.net",
        )
    if "." not in host:
        host = f"{host}.atlassian.net"
    return host, f"https://{host}"


async def _row_to_out(
    session: AsyncSession,
    row: NativeIntegrationInstallation,
) -> NativeIntegrationInstallationOut:
    credential_id = (
        await session.execute(
            select(NativeIntegrationCredential.id)
            .where(NativeIntegrationCredential.installation_id == row.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    return NativeIntegrationInstallationOut(
        id=row.id,
        workspace_id=row.workspace_id,
        provider=row.provider,
        auth_mode=row.auth_mode,
        external_account_id=row.external_account_id,
        external_account_name=row.external_account_name,
        external_account_url=row.external_account_url,
        capabilities=list(row.capabilities or []),
        scopes=list(row.scopes or []),
        config=row.config or {},
        status=row.status,
        has_credential=credential_id is not None,
        last_health_at=row.last_health_at,
        last_health_error=row.last_health_error,
        connected_at=row.connected_at,
        disabled_at=row.disabled_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[NativeIntegrationInstallationOut])
async def list_native_integrations(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[NativeIntegrationInstallationOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    rows = (
        await session.execute(
            select(NativeIntegrationInstallation)
            .where(NativeIntegrationInstallation.workspace_id == workspace_id)
            .order_by(
                NativeIntegrationInstallation.provider,
                NativeIntegrationInstallation.external_account_name,
            )
        )
    ).scalars().all()
    return [await _row_to_out(session, row) for row in rows]


@router.post(
    "/azure-devops/pat",
    response_model=NativeIntegrationInstallationOut,
    status_code=status.HTTP_200_OK,
)
async def upsert_azure_devops_pat(
    workspace_id: uuid.UUID,
    payload: AzureDevOpsPatInstall,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> NativeIntegrationInstallationOut:
    """Connect an Azure DevOps organization using a server-side PAT.

    The PAT never leaves the backend after this request. The installation is
    marked ``pending`` until a later Azure probe verifies organization access
    and required permissions for Repos/Pipelines.
    """

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    org = _normalise_azure_org(payload.organization)
    now = datetime.now(timezone.utc)
    account_url = f"https://dev.azure.com/{org}"
    scopes = sorted({scope.strip() for scope in payload.scopes if scope.strip()})
    config = {
        "organization": org,
        "project": payload.project.strip() if payload.project else None,
    }

    stmt = select(NativeIntegrationInstallation).where(
        NativeIntegrationInstallation.workspace_id == workspace_id,
        NativeIntegrationInstallation.provider == NativeIntegrationProvider.AZURE_DEVOPS,
        NativeIntegrationInstallation.external_account_id == org,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    is_new = row is None
    if row is None:
        row = NativeIntegrationInstallation(
            workspace_id=workspace_id,
            provider=NativeIntegrationProvider.AZURE_DEVOPS,
            auth_mode=NativeIntegrationAuthMode.PAT,
            external_account_id=org,
        )
        session.add(row)

    row.external_account_name = org
    row.external_account_url = account_url
    row.capabilities = ["code_host", "orchestrator"]
    row.scopes = scopes
    row.config = config
    row.status = NativeIntegrationStatus.PENDING
    row.last_health_at = None
    row.last_health_error = None
    row.connected_at = row.connected_at or now
    row.disabled_at = None
    row.updated_at = now
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="native integration already exists for this provider account",
        ) from exc

    fingerprint = hashlib.sha256(payload.pat.encode("utf-8")).hexdigest()
    credential = (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == row.id,
                NativeIntegrationCredential.kind == "pat",
            )
        )
    ).scalar_one_or_none()
    if credential is None:
        credential = NativeIntegrationCredential(
            installation_id=row.id,
            kind="pat",
            secret_ciphertext=encrypt(payload.pat),
        )
        session.add(credential)
    else:
        credential.secret_ciphertext = encrypt(payload.pat)
    credential.secret_fingerprint = fingerprint
    credential.scopes = scopes
    credential.last_rotated_at = now
    credential.revoked_at = None
    credential.updated_at = now

    session.add(
        NativeIntegrationAuditEvent(
            workspace_id=workspace_id,
            installation_id=row.id,
            actor_user_id=auth.user.id,
            provider=NativeIntegrationProvider.AZURE_DEVOPS,
            action="native_integration.create" if is_new else "native_integration.update",
            target_kind="installation",
            target_id=str(row.id),
            payload={
                "auth_mode": NativeIntegrationAuthMode.PAT,
                "organization": org,
                "project": config["project"],
                "capabilities": row.capabilities,
                "scopes": scopes,
                "credential_rotated": True,
            },
        )
    )

    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="native integration already exists for this provider account",
        ) from exc
    await session.refresh(row)
    return await _row_to_out(session, row)


@router.post(
    "/atlassian/api-token",
    response_model=NativeIntegrationInstallationOut,
    status_code=status.HTTP_200_OK,
)
async def upsert_atlassian_api_token(
    workspace_id: uuid.UUID,
    payload: AtlassianApiTokenInstall,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> NativeIntegrationInstallationOut:
    """Connect Atlassian Cloud for Jira tickets and Confluence knowledge."""

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    host, site_url = _normalise_atlassian_site(payload.site)
    email = payload.email.strip().lower()
    now = datetime.now(timezone.utc)
    scopes = sorted(
        {
            scope.strip()
            for scope in (
                payload.scopes
                or [
                    "read:jira-work",
                    "write:jira-work",
                    "read:confluence-content.all",
                ]
            )
            if scope.strip()
        }
    )
    jira_project = (
        payload.jira_project.strip().upper() if payload.jira_project else None
    )
    config = {
        "site": host,
        "site_url": site_url,
        "email": email,
        "jira_project": jira_project,
    }

    stmt = select(NativeIntegrationInstallation).where(
        NativeIntegrationInstallation.workspace_id == workspace_id,
        NativeIntegrationInstallation.provider == NativeIntegrationProvider.ATLASSIAN,
        NativeIntegrationInstallation.external_account_id == host,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    is_new = row is None
    if row is None:
        row = NativeIntegrationInstallation(
            workspace_id=workspace_id,
            provider=NativeIntegrationProvider.ATLASSIAN,
            auth_mode=NativeIntegrationAuthMode.PAT,
            external_account_id=host,
        )
        session.add(row)

    row.external_account_name = host
    row.external_account_url = site_url
    row.capabilities = ["tracker", "knowledge"]
    row.scopes = scopes
    row.config = config
    row.status = NativeIntegrationStatus.PENDING
    row.last_health_at = None
    row.last_health_error = None
    row.connected_at = row.connected_at or now
    row.disabled_at = None
    row.updated_at = now
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="native integration already exists for this provider account",
        ) from exc

    fingerprint = hashlib.sha256(payload.api_token.encode("utf-8")).hexdigest()
    credential = (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == row.id,
                NativeIntegrationCredential.kind == "api_token",
            )
        )
    ).scalar_one_or_none()
    if credential is None:
        credential = NativeIntegrationCredential(
            installation_id=row.id,
            kind="api_token",
            secret_ciphertext=encrypt(payload.api_token),
        )
        session.add(credential)
    else:
        credential.secret_ciphertext = encrypt(payload.api_token)
    credential.secret_fingerprint = fingerprint
    credential.scopes = scopes
    credential.last_rotated_at = now
    credential.revoked_at = None
    credential.updated_at = now

    session.add(
        NativeIntegrationAuditEvent(
            workspace_id=workspace_id,
            installation_id=row.id,
            actor_user_id=auth.user.id,
            provider=NativeIntegrationProvider.ATLASSIAN,
            action="native_integration.create" if is_new else "native_integration.update",
            target_kind="installation",
            target_id=str(row.id),
            payload={
                "auth_mode": NativeIntegrationAuthMode.PAT,
                "site": host,
                "jira_project": jira_project,
                "capabilities": row.capabilities,
                "scopes": scopes,
                "credential_rotated": True,
            },
        )
    )
    await session.flush()
    await session.refresh(row)
    return await _row_to_out(session, row)
