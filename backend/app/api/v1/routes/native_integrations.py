"""First-party native integration install surfaces."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
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
    NativeIntegrationBinding,
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationProvider,
    NativeIntegrationStatus,
)
from backend.app.db.models.tenancy import Integration
from backend.app.db.session import get_session
from backend.app.integrations.azure_devops.ci_adapter import AzureDevOpsPipelines
from backend.app.integrations.azure_devops.code_host_adapter import AzureDevOpsCodeHost
from backend.app.integrations.gateway.ci import CIGateway
from backend.app.integrations.gateway.code_host import CodeHostGateway, RepoSummary
from backend.app.integrations.gitlab.ci_adapter import GitLabCI
from backend.app.integrations.gitlab.code_host_adapter import GitLabCodeHost
from backend.app.security.encryption import decrypt, encrypt


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


class GitLabPatInstall(BaseModel):
    host: str = Field(default="gitlab.com", min_length=1, max_length=255)
    pat: str = Field(min_length=1, max_length=8192)
    group: str | None = Field(default=None, max_length=255)
    scopes: list[str] = Field(default_factory=list, max_length=64)


class AtlassianApiTokenInstall(BaseModel):
    site: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    api_token: str = Field(min_length=1, max_length=8192)
    jira_project: str | None = Field(default=None, max_length=64)
    scopes: list[str] = Field(default_factory=list, max_length=64)


class NativeResourceOut(BaseModel):
    resource_type: str
    external_id: str
    display_name: str
    external_url: str | None
    provider: str
    config: dict
    bound: bool


class NativeBindingOut(BaseModel):
    id: uuid.UUID
    installation_id: uuid.UUID
    provider: str
    resource_type: str
    external_id: str
    display_name: str
    external_url: str | None
    config: dict
    status: str
    last_synced_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class NativeBindingReplaceIn(BaseModel):
    resource_type: str = Field(default="repo", max_length=64)
    external_ids: list[str] = Field(default_factory=list, max_length=500)


class NativeCiRunOut(BaseModel):
    id: str | int | None
    name: str | None
    status: str | None
    conclusion: str | None
    url: str | None
    created_at: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None
    ref: str | None = None
    sha: str | None = None
    source_branch: str | None = None
    source_version: str | None = None


class NativeCiLogsOut(BaseModel):
    run_id: str
    logs: str


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


def _normalise_gitlab_host(value: str) -> tuple[str, str]:
    raw = value.strip().strip("/")
    if not raw:
        raise HTTPException(status_code=422, detail="host cannot be blank")
    if raw.startswith("https://"):
        host = raw.removeprefix("https://").strip("/")
    elif raw.startswith("http://"):
        raise HTTPException(status_code=422, detail="GitLab host must use https")
    else:
        host = raw
    if "/" in host:
        raise HTTPException(
            status_code=422,
            detail="host must be a GitLab host like gitlab.com",
        )
    return host, f"https://{host}"


def _normalise_gitlab_group(value: str | None) -> str | None:
    group = (value or "").strip().strip("/")
    return group or None


async def _probe_azure_devops_pat(
    *,
    organization: str,
    pat: str,
    project: str | None,
) -> tuple[bool, str | None]:
    """Verify PAT reachability without exposing token details in errors."""

    base = f"https://dev.azure.com/{quote(organization, safe='')}"
    if project:
        path = f"/{quote(project, safe='')}/_apis/git/repositories"
    else:
        path = "/_apis/projects"
    url = f"{base}{path}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.get(
                url,
                params={"api-version": "7.1", "$top": "1"},
                auth=("", pat),
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        return False, f"Azure DevOps probe failed: {exc.__class__.__name__}"

    if response.status_code < 400:
        return True, None
    if response.status_code in {401, 403}:
        return False, "Azure DevOps rejected the PAT or required scopes."
    if response.status_code == 404:
        target = f"project {project!r}" if project else f"organization {organization!r}"
        return False, f"Azure DevOps {target} was not found."
    return False, f"Azure DevOps probe returned HTTP {response.status_code}."


async def _probe_gitlab_pat(
    *,
    base_url: str,
    pat: str,
    group: str | None,
) -> tuple[bool, str | None]:
    """Verify GitLab PAT reachability without logging token material."""

    if group:
        path = f"/api/v4/groups/{quote(group, safe='')}/projects"
        params = {"per_page": "1"}
    else:
        path = "/api/v4/user"
        params = {}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.get(
                f"{base_url}{path}",
                params=params,
                headers={"Accept": "application/json", "PRIVATE-TOKEN": pat},
            )
    except httpx.HTTPError as exc:
        return False, f"GitLab probe failed: {exc.__class__.__name__}"

    if response.status_code < 400:
        return True, None
    if response.status_code in {401, 403}:
        return False, "GitLab rejected the PAT or required scopes."
    if response.status_code == 404:
        target = f"group {group!r}" if group else "current user"
        return False, f"GitLab {target} was not found."
    return False, f"GitLab probe returned HTTP {response.status_code}."


async def _probe_atlassian_api_token(
    *,
    site_url: str,
    email: str,
    api_token: str,
) -> tuple[bool, str | None]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.get(
                f"{site_url.rstrip('/')}/rest/api/3/myself",
                auth=(email, api_token),
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        return False, f"Atlassian probe failed: {exc.__class__.__name__}"

    if response.status_code < 400:
        return True, None
    if response.status_code in {401, 403}:
        return False, "Atlassian rejected the API token or account email."
    return False, f"Atlassian probe returned HTTP {response.status_code}."


async def _probe_linear_access_token(access_token: str) -> tuple[bool, str | None]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.post(
                "https://api.linear.app/graphql",
                json={"query": "query ShipProbe { viewer { id } }"},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        return False, f"Linear probe failed: {exc.__class__.__name__}"

    if response.status_code in {401, 403}:
        return False, "Linear rejected the OAuth access token."
    if response.status_code >= 400:
        return False, f"Linear probe returned HTTP {response.status_code}."
    try:
        body = response.json()
    except ValueError:
        return False, "Linear probe returned invalid JSON."
    if body.get("errors"):
        return False, "Linear probe returned a GraphQL error."
    return True, None


async def _probe_notion_access_token(access_token: str) -> tuple[bool, str | None]:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            response = await client.get(
                "https://api.notion.com/v1/users/me",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                    "Notion-Version": "2022-06-28",
                },
            )
    except httpx.HTTPError as exc:
        return False, f"Notion probe failed: {exc.__class__.__name__}"

    if response.status_code < 400:
        return True, None
    if response.status_code in {401, 403}:
        return False, "Notion rejected the OAuth access token."
    return False, f"Notion probe returned HTTP {response.status_code}."


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
            .where(NativeIntegrationCredential.revoked_at.is_(None))
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


def _binding_to_out(row: NativeIntegrationBinding) -> NativeBindingOut:
    return NativeBindingOut(
        id=row.id,
        installation_id=row.installation_id,
        provider=row.provider,
        resource_type=row.resource_type,
        external_id=row.external_id,
        display_name=row.display_name,
        external_url=row.external_url,
        config=row.config or {},
        status=row.status,
        last_synced_at=row.last_synced_at,
        last_error=row.last_error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _load_native_installation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
) -> NativeIntegrationInstallation:
    row = (
        await session.execute(
            select(NativeIntegrationInstallation).where(
                NativeIntegrationInstallation.id == installation_id,
                NativeIntegrationInstallation.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="native integration not found")
    if row.disabled_at is not None:
        raise HTTPException(status_code=409, detail="native integration is disabled")
    return row


async def _load_native_secret(
    session: AsyncSession,
    row: NativeIntegrationInstallation,
    *,
    kind: str,
) -> str:
    credential = (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == row.id,
                NativeIntegrationCredential.kind == kind,
                NativeIntegrationCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if credential is None:
        raise HTTPException(
            status_code=409,
            detail=f"native integration has no active {kind} credential",
        )
    try:
        return decrypt(credential.secret_ciphertext)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=409,
            detail="native integration credential is unreadable",
        ) from exc


async def _code_host_for_native_installation(
    session: AsyncSession,
    row: NativeIntegrationInstallation,
) -> CodeHostGateway:
    if row.provider == NativeIntegrationProvider.GITLAB:
        pat = await _load_native_secret(session, row, kind="pat")
        config = row.config or {}
        base_url = str(config.get("base_url") or row.external_account_url or "").strip()
        if not base_url:
            raise HTTPException(status_code=409, detail="GitLab base_url is missing")
        group = config.get("group")
        return GitLabCodeHost(
            base_url=base_url,
            pat=pat,
            group=str(group).strip("/") if group else None,
        )
    if row.provider == NativeIntegrationProvider.AZURE_DEVOPS:
        pat = await _load_native_secret(session, row, kind="pat")
        config = row.config or {}
        organization = str(config.get("organization") or row.external_account_id)
        project = config.get("project")
        return AzureDevOpsCodeHost(
            organization=organization,
            pat=pat,
            project=str(project).strip() if project else None,
        )
    raise HTTPException(
        status_code=400,
        detail=f"provider {row.provider!r} does not expose code-host resources",
    )


async def _ci_for_native_installation(
    session: AsyncSession,
    row: NativeIntegrationInstallation,
) -> CIGateway:
    if row.provider == NativeIntegrationProvider.GITLAB:
        pat = await _load_native_secret(session, row, kind="pat")
        config = row.config or {}
        base_url = str(config.get("base_url") or row.external_account_url or "").strip()
        if not base_url:
            raise HTTPException(status_code=409, detail="GitLab base_url is missing")
        return GitLabCI(base_url=base_url, pat=pat)
    if row.provider == NativeIntegrationProvider.AZURE_DEVOPS:
        pat = await _load_native_secret(session, row, kind="pat")
        config = row.config or {}
        organization = str(config.get("organization") or row.external_account_id)
        project = config.get("project")
        return AzureDevOpsPipelines(
            organization=organization,
            pat=pat,
            project=str(project).strip() if project else None,
        )
    raise HTTPException(
        status_code=400,
        detail=f"provider {row.provider!r} does not expose CI resources",
    )


async def _load_native_binding(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> NativeIntegrationBinding:
    binding = (
        await session.execute(
            select(NativeIntegrationBinding).where(
                NativeIntegrationBinding.id == binding_id,
                NativeIntegrationBinding.workspace_id == workspace_id,
                NativeIntegrationBinding.installation_id == installation_id,
            )
        )
    ).scalar_one_or_none()
    if binding is None:
        raise HTTPException(status_code=404, detail="native binding not found")
    if binding.status == "disabled":
        raise HTTPException(status_code=409, detail="native binding is disabled")
    return binding


def _repo_ref_from_binding(binding: NativeIntegrationBinding) -> RepoRef:
    config = binding.config or {}
    owner = str(config.get("owner") or "")
    name = str(config.get("name") or "")
    full_name = str(config.get("full_name") or binding.display_name)
    if not owner or not name:
        owner, _, name = full_name.rpartition("/")
    if not owner or not name:
        raise HTTPException(status_code=409, detail="repo binding lacks owner/name")
    if binding.provider == NativeIntegrationProvider.GITLAB:
        return RepoRef(kind="gitlab", owner=owner, repo=name)
    if binding.provider == NativeIntegrationProvider.AZURE_DEVOPS:
        return RepoRef(kind="azure_devops", owner=owner, repo=name)
    raise HTTPException(
        status_code=400,
        detail=f"provider {binding.provider!r} does not expose repo refs",
    )


def _repo_resource_from_summary(
    provider: str,
    summary: RepoSummary,
    *,
    bound_ids: set[str],
) -> NativeResourceOut:
    external_id = str(summary.external_id)
    return NativeResourceOut(
        resource_type="repo",
        external_id=external_id,
        display_name=summary.full_name,
        external_url=summary.html_url,
        provider=provider,
        config={
            "full_name": summary.full_name,
            "owner": summary.ref.owner,
            "name": summary.ref.repo,
            "default_branch": summary.default_branch,
            "private": summary.private,
            "description": summary.description,
        },
        bound=external_id in bound_ids,
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


@router.get(
    "/{installation_id}/resources/repos",
    response_model=list[NativeResourceOut],
)
async def list_native_repo_resources(
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[NativeResourceOut]:
    """Live repo discovery for native code-host integrations."""

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _load_native_installation(
        session, workspace_id=workspace_id, installation_id=installation_id
    )
    gateway = await _code_host_for_native_installation(session, row)
    bound_ids = {
        value
        for (value,) in (
            await session.execute(
                select(NativeIntegrationBinding.external_id).where(
                    NativeIntegrationBinding.workspace_id == workspace_id,
                    NativeIntegrationBinding.installation_id == row.id,
                    NativeIntegrationBinding.resource_type == "repo",
                    NativeIntegrationBinding.status != "disabled",
                )
            )
        ).all()
    }
    try:
        summaries = await gateway.list_repo_summaries()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"{row.provider} rejected repo discovery (HTTP {exc.response.status_code})",
        ) from exc
    return [
        _repo_resource_from_summary(row.provider, summary, bound_ids=bound_ids)
        for summary in summaries
    ]


@router.get(
    "/{installation_id}/bindings",
    response_model=list[NativeBindingOut],
)
async def list_native_bindings(
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[NativeBindingOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = await _load_native_installation(
        session, workspace_id=workspace_id, installation_id=installation_id
    )
    bindings = (
        await session.execute(
            select(NativeIntegrationBinding)
            .where(
                NativeIntegrationBinding.workspace_id == workspace_id,
                NativeIntegrationBinding.installation_id == row.id,
            )
            .order_by(
                NativeIntegrationBinding.resource_type,
                NativeIntegrationBinding.display_name,
            )
        )
    ).scalars().all()
    return [_binding_to_out(binding) for binding in bindings]


@router.put(
    "/{installation_id}/bindings",
    response_model=list[NativeBindingOut],
)
async def replace_native_bindings(
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    payload: NativeBindingReplaceIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[NativeBindingOut]:
    """Replace selected native resources for an installation.

    v1 supports repo bindings for GitLab and Azure DevOps. Existing rows are
    soft-disabled instead of deleted so audits and future sync state stay
    understandable.
    """

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    if payload.resource_type != "repo":
        raise HTTPException(status_code=400, detail="only repo bindings are supported")
    row = await _load_native_installation(
        session, workspace_id=workspace_id, installation_id=installation_id
    )
    gateway = await _code_host_for_native_installation(session, row)
    summaries = await gateway.list_repo_summaries()
    by_id = {str(summary.external_id): summary for summary in summaries}
    desired_ids = {str(external_id).strip() for external_id in payload.external_ids}
    desired_ids.discard("")
    unknown = sorted(desired_ids - set(by_id))
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"repo ids are not visible to this native installation: {unknown}",
        )

    now = datetime.now(timezone.utc)
    existing = (
        await session.execute(
            select(NativeIntegrationBinding).where(
                NativeIntegrationBinding.workspace_id == workspace_id,
                NativeIntegrationBinding.installation_id == row.id,
                NativeIntegrationBinding.resource_type == "repo",
            )
        )
    ).scalars().all()
    existing_by_id = {binding.external_id: binding for binding in existing}

    for external_id, binding in existing_by_id.items():
        if external_id not in desired_ids:
            binding.status = "disabled"
            binding.updated_at = now

    out: list[NativeIntegrationBinding] = []
    for external_id in sorted(desired_ids):
        summary = by_id[external_id]
        binding = existing_by_id.get(external_id)
        config = _repo_resource_from_summary(
            row.provider, summary, bound_ids=set()
        ).config
        if binding is None:
            binding = NativeIntegrationBinding(
                workspace_id=workspace_id,
                installation_id=row.id,
                provider=row.provider,
                resource_type="repo",
                external_id=external_id,
                display_name=summary.full_name,
                external_url=summary.html_url,
                config=config,
                status="ready",
            )
            session.add(binding)
        else:
            binding.provider = row.provider
            binding.display_name = summary.full_name
            binding.external_url = summary.html_url
            binding.config = config
            binding.status = "ready"
            binding.last_error = None
            binding.updated_at = now
        out.append(binding)

    session.add(
        NativeIntegrationAuditEvent(
            workspace_id=workspace_id,
            installation_id=row.id,
            actor_user_id=auth.user.id,
            provider=row.provider,
            action="native_integration.bindings.replace",
            target_kind="installation",
            target_id=str(row.id),
            payload={
                "resource_type": "repo",
                "external_ids": sorted(desired_ids),
                "disabled": sorted(set(existing_by_id) - desired_ids),
            },
        )
    )
    await session.flush()
    return [_binding_to_out(binding) for binding in out]


@router.get(
    "/{installation_id}/bindings/{binding_id}/ci/runs",
    response_model=list[NativeCiRunOut],
)
async def list_native_ci_runs(
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    binding_id: uuid.UUID,
    limit: int = 25,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[NativeCiRunOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    installation = await _load_native_installation(
        session, workspace_id=workspace_id, installation_id=installation_id
    )
    binding = await _load_native_binding(
        session,
        workspace_id=workspace_id,
        installation_id=installation.id,
        binding_id=binding_id,
    )
    ci = await _ci_for_native_installation(session, installation)
    repo_ref = _repo_ref_from_binding(binding)
    runs = await ci.list_runs(repo_ref, limit=max(1, min(limit, 100)))
    return [NativeCiRunOut(**run) for run in runs]


@router.post(
    "/{installation_id}/bindings/{binding_id}/ci/runs/{run_id}/rerun",
    status_code=status.HTTP_202_ACCEPTED,
)
async def rerun_native_ci_run(
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    binding_id: uuid.UUID,
    run_id: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    installation = await _load_native_installation(
        session, workspace_id=workspace_id, installation_id=installation_id
    )
    binding = await _load_native_binding(
        session,
        workspace_id=workspace_id,
        installation_id=installation.id,
        binding_id=binding_id,
    )
    ci = await _ci_for_native_installation(session, installation)
    repo_ref = _repo_ref_from_binding(binding)
    await ci.rerun(repo_ref, run_id=run_id)
    session.add(
        NativeIntegrationAuditEvent(
            workspace_id=workspace_id,
            installation_id=installation.id,
            actor_user_id=auth.user.id,
            provider=installation.provider,
            action="native_integration.ci.rerun",
            target_kind="binding",
            target_id=str(binding.id),
            payload={"run_id": run_id, "repo": binding.display_name},
        )
    )
    await session.flush()
    return {"status": "accepted"}


@router.get(
    "/{installation_id}/bindings/{binding_id}/ci/runs/{run_id}/logs",
    response_model=NativeCiLogsOut,
)
async def get_native_ci_logs(
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    binding_id: uuid.UUID,
    run_id: str,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> NativeCiLogsOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    installation = await _load_native_installation(
        session, workspace_id=workspace_id, installation_id=installation_id
    )
    binding = await _load_native_binding(
        session,
        workspace_id=workspace_id,
        installation_id=installation.id,
        binding_id=binding_id,
    )
    ci = await _ci_for_native_installation(session, installation)
    repo_ref = _repo_ref_from_binding(binding)
    logs = await ci.get_logs(repo_ref, run_id=run_id)
    return NativeCiLogsOut(run_id=run_id, logs=logs)


@router.post(
    "/{installation_id}/probe",
    response_model=NativeIntegrationInstallationOut,
    status_code=status.HTTP_200_OK,
)
async def probe_native_integration(
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> NativeIntegrationInstallationOut:
    """Refresh a native provider install's health status."""

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = (
        await session.execute(
            select(NativeIntegrationInstallation).where(
                NativeIntegrationInstallation.id == installation_id,
                NativeIntegrationInstallation.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="native integration not found")
    if row.disabled_at is not None:
        raise HTTPException(status_code=409, detail="native integration is disabled")

    credential_kind = _probe_credential_kind(row.provider)
    if credential_kind is None:
        raise HTTPException(
            status_code=400,
            detail=f"native probe is not supported for provider {row.provider!r}",
        )

    now = datetime.now(timezone.utc)
    credential = (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == row.id,
                NativeIntegrationCredential.kind == credential_kind,
                NativeIntegrationCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if credential is None:
        probe_ok = False
        probe_error = "Native integration has no active credential."
    else:
        try:
            secret = decrypt(credential.secret_ciphertext)
        except Exception:  # noqa: BLE001
            probe_ok = False
            probe_error = "Native integration credential is unreadable."
        else:
            probe_ok, probe_error = await _probe_native_provider(row, secret)

    row.status = (
        NativeIntegrationStatus.READY if probe_ok else NativeIntegrationStatus.ERROR
    )
    row.last_health_at = now
    row.last_health_error = probe_error
    row.updated_at = now
    session.add(
        NativeIntegrationAuditEvent(
            workspace_id=workspace_id,
            installation_id=row.id,
            actor_user_id=auth.user.id,
            provider=row.provider,
            action="native_integration.probe",
            target_kind="installation",
            target_id=str(row.id),
            payload={
                "external_account_id": row.external_account_id,
                "ok": probe_ok,
                "error": probe_error,
            },
        )
    )
    await session.flush()
    await session.refresh(row)
    return await _row_to_out(session, row)


def _probe_credential_kind(provider: str) -> str | None:
    if provider in {
        NativeIntegrationProvider.AZURE_DEVOPS,
        NativeIntegrationProvider.GITLAB,
    }:
        return "pat"
    if provider == NativeIntegrationProvider.ATLASSIAN:
        return "api_token"
    if provider in {
        NativeIntegrationProvider.LINEAR,
        NativeIntegrationProvider.NOTION,
    }:
        return "access_token"
    return None


async def _probe_native_provider(
    row: NativeIntegrationInstallation,
    secret: str,
) -> tuple[bool, str | None]:
    config = row.config or {}
    if row.provider == NativeIntegrationProvider.AZURE_DEVOPS:
        organization = str(config.get("organization") or row.external_account_id)
        project = config.get("project")
        return await _probe_azure_devops_pat(
            organization=organization,
            pat=secret,
            project=str(project).strip() if project else None,
        )
    if row.provider == NativeIntegrationProvider.GITLAB:
        base_url = str(config.get("base_url") or row.external_account_url or "").strip()
        group = config.get("group")
        if not base_url:
            return False, "GitLab integration is missing base_url config."
        return await _probe_gitlab_pat(
            base_url=base_url.rstrip("/"),
            pat=secret,
            group=str(group).strip() if group else None,
        )
    if row.provider == NativeIntegrationProvider.ATLASSIAN:
        site_url = str(config.get("site_url") or row.external_account_url or "").strip()
        email = str(config.get("email") or "").strip()
        if not site_url or not email:
            return False, "Atlassian integration is missing site_url/email config."
        return await _probe_atlassian_api_token(
            site_url=site_url,
            email=email,
            api_token=secret,
        )
    if row.provider == NativeIntegrationProvider.LINEAR:
        return await _probe_linear_access_token(secret)
    if row.provider == NativeIntegrationProvider.NOTION:
        return await _probe_notion_access_token(secret)
    return False, f"Native probe is not supported for provider {row.provider!r}."


@router.delete(
    "/{installation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def disable_native_integration(
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft-disable a native provider install and revoke stored credentials."""

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    row = (
        await session.execute(
            select(NativeIntegrationInstallation).where(
                NativeIntegrationInstallation.id == installation_id,
                NativeIntegrationInstallation.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="native integration not found")

    now = datetime.now(timezone.utc)
    row.status = NativeIntegrationStatus.DISABLED
    row.disabled_at = now
    row.last_health_error = None
    row.updated_at = now

    credentials = (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == row.id,
                NativeIntegrationCredential.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    for credential in credentials:
        credential.revoked_at = now
        credential.updated_at = now

    await _disable_legacy_shadow_rows(session, row, now)

    session.add(
        NativeIntegrationAuditEvent(
            workspace_id=workspace_id,
            installation_id=row.id,
            actor_user_id=auth.user.id,
            provider=row.provider,
            action="native_integration.disable",
            target_kind="installation",
            target_id=str(row.id),
            payload={
                "external_account_id": row.external_account_id,
                "credentials_revoked": len(credentials),
            },
        )
    )
    await session.flush()


async def _disable_legacy_shadow_rows(
    session: AsyncSession,
    row: NativeIntegrationInstallation,
    now: datetime,
) -> None:
    """Turn off legacy rows maintained for compatibility with old adapters."""

    legacy_kinds: tuple[str, ...]
    if row.provider == NativeIntegrationProvider.LINEAR:
        legacy_kinds = ("linear",)
    elif row.provider == NativeIntegrationProvider.NOTION:
        legacy_kinds = ("notion",)
    elif row.provider == NativeIntegrationProvider.ATLASSIAN:
        legacy_kinds = ("confluence",)
    else:
        legacy_kinds = ()

    if not legacy_kinds:
        return
    legacy_rows = (
        await session.execute(
            select(Integration).where(
                Integration.workspace_id == row.workspace_id,
                Integration.kind.in_(legacy_kinds),
                Integration.repo_id.is_(None),
            )
        )
    ).scalars().all()
    for legacy in legacy_rows:
        legacy.status = "disabled"
        legacy.secret_ciphertext = None
        legacy.last_health_at = now
        legacy.last_health_error = None
        legacy.updated_at = now


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
    project = payload.project.strip() if payload.project else None
    probe_ok, probe_error = await _probe_azure_devops_pat(
        organization=org,
        pat=payload.pat,
        project=project,
    )
    config = {
        "organization": org,
        "project": project,
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
    row.status = (
        NativeIntegrationStatus.READY if probe_ok else NativeIntegrationStatus.ERROR
    )
    row.last_health_at = now
    row.last_health_error = probe_error
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
    "/gitlab/pat",
    response_model=NativeIntegrationInstallationOut,
    status_code=status.HTTP_200_OK,
)
async def upsert_gitlab_pat(
    workspace_id: uuid.UUID,
    payload: GitLabPatInstall,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> NativeIntegrationInstallationOut:
    """Connect GitLab.com or a self-hosted GitLab instance with a PAT."""

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)
    host, base_url = _normalise_gitlab_host(payload.host)
    group = _normalise_gitlab_group(payload.group)
    now = datetime.now(timezone.utc)
    scopes = sorted({scope.strip() for scope in payload.scopes if scope.strip()})
    probe_ok, probe_error = await _probe_gitlab_pat(
        base_url=base_url,
        pat=payload.pat,
        group=group,
    )
    config = {
        "host": host,
        "base_url": base_url,
        "group": group,
    }

    stmt = select(NativeIntegrationInstallation).where(
        NativeIntegrationInstallation.workspace_id == workspace_id,
        NativeIntegrationInstallation.provider == NativeIntegrationProvider.GITLAB,
        NativeIntegrationInstallation.external_account_id == host,
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    is_new = row is None
    if row is None:
        row = NativeIntegrationInstallation(
            workspace_id=workspace_id,
            provider=NativeIntegrationProvider.GITLAB,
            auth_mode=NativeIntegrationAuthMode.PAT,
            external_account_id=host,
        )
        session.add(row)

    row.external_account_name = group or host
    row.external_account_url = base_url
    row.capabilities = ["code_host", "orchestrator"]
    row.scopes = scopes
    row.config = config
    row.status = (
        NativeIntegrationStatus.READY if probe_ok else NativeIntegrationStatus.ERROR
    )
    row.last_health_at = now
    row.last_health_error = probe_error
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
            provider=NativeIntegrationProvider.GITLAB,
            action="native_integration.create" if is_new else "native_integration.update",
            target_kind="installation",
            target_id=str(row.id),
            payload={
                "auth_mode": NativeIntegrationAuthMode.PAT,
                "host": host,
                "group": group,
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

    confluence_row = (
        await session.execute(
            select(Integration).where(
                Integration.workspace_id == workspace_id,
                Integration.kind == "confluence",
                Integration.repo_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if confluence_row is None:
        confluence_row = Integration(
            workspace_id=workspace_id,
            kind="confluence",
            config={
                "site": host,
                "site_url": site_url,
                "email": email,
            },
            status="ok",
        )
        session.add(confluence_row)
    else:
        merged = dict(confluence_row.config or {})
        merged.update({"site": host, "site_url": site_url, "email": email})
        confluence_row.config = merged
        confluence_row.status = "ok"
    confluence_row.secret_ciphertext = encrypt(payload.api_token)
    confluence_row.last_health_at = now
    confluence_row.last_health_error = None
    confluence_row.updated_at = now

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
