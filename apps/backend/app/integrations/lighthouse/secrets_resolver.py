"""Resolve Lighthouse importer credentials from Ship's own integrations.

When the operator picks an importer type Ship already has an integration
for (GitHub App, Notion / Linear OAuth, GitLab PAT, Atlassian site), we
can fill the secrets — and any config the integration already knows
(``base_url``, account email, ...) — server-side instead of asking the
operator to paste them. Tokens never cross the wire to the browser.

The mapping is intentionally narrow — only the importer types where
Ship has a first-party native installation in
``NativeIntegrationInstallation`` (or a ``GitHubInstallation`` for the
App flow). Bitbucket / Asana / Trello / Slack stay manual.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.integrations import (
    GitHubInstallation,
    NativeIntegrationInstallation,
    NativeIntegrationCredential,
    NativeIntegrationProvider,
    NativeIntegrationStatus,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ResolvedImporterCreds:
    """Credentials + config the integration can pre-fill on the
    importer create request. ``config`` lands under the operator's
    explicit values (operator wins), ``secrets`` merges the same way."""

    secrets: dict[str, str]
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkspaceImporterIntegration:
    """One importer type the workspace has auto-resolvable credentials for.

    ``provides_config_keys`` lists ``config_schema`` properties the
    integration will fill server-side (in addition to the importer's
    ``secret_keys``). The Console hides these inputs when the operator
    opts into the workspace integration.
    """

    importer_type: str
    provider: str
    account_name: str | None
    account_url: str | None
    provides_config_keys: tuple[str, ...] = ()


# Config-schema keys each integration pre-fills server-side. Used by
# the Console form to hide those inputs when the workspace integration
# is selected.
_PROVIDED_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    "gitlab": ("base_url",),
    "confluence": ("base_url", "user_name"),
    "jira": ("server_url", "email"),
}


# Importer types we know how to resolve credentials for.
SUPPORTED_TYPES: frozenset[str] = frozenset(
    {
        "github_repo",
        "github_releases",
        "notion",
        "linear",
        "gitlab",
        "confluence",
        "jira",
    }
)


async def list_workspace_importer_integrations(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[WorkspaceImporterIntegration]:
    """Return the importer types the workspace can auto-authorize.

    One entry per importer type — so ``github_repo`` and
    ``github_releases`` each appear separately when the GitHub App is
    installed; ``confluence`` + ``jira`` each appear when an Atlassian
    site is connected.
    """
    out: list[WorkspaceImporterIntegration] = []

    # ── GitHub App (mint installation tokens JIT) ──
    github = (
        await session.execute(
            select(GitHubInstallation)
            .where(
                GitHubInstallation.workspace_id == workspace_id,
                GitHubInstallation.suspended_at.is_(None),
            )
            .order_by(GitHubInstallation.installed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if github is not None:
        for itype in ("github_repo", "github_releases"):
            out.append(
                WorkspaceImporterIntegration(
                    importer_type=itype,
                    provider="github",
                    account_name=github.account_login,
                    account_url=(
                        f"https://github.com/{github.account_login}"
                        if github.account_login
                        else None
                    ),
                    provides_config_keys=_PROVIDED_CONFIG_KEYS.get(itype, ()),
                )
            )

    # ── Native installations (Notion / Linear / GitLab) ──
    native_pairs: tuple[tuple[str, str], ...] = (
        (NativeIntegrationProvider.NOTION, "notion"),
        (NativeIntegrationProvider.LINEAR, "linear"),
        (NativeIntegrationProvider.GITLAB, "gitlab"),
    )
    for provider, importer_type in native_pairs:
        install = await _latest_ready_install(session, workspace_id, provider)
        if install is None:
            continue
        out.append(
            WorkspaceImporterIntegration(
                importer_type=importer_type,
                provider=provider,
                account_name=install.external_account_name,
                account_url=install.external_account_url,
                provides_config_keys=_PROVIDED_CONFIG_KEYS.get(importer_type, ()),
            )
        )

    # ── Atlassian site → confluence + jira ──
    atlassian = await _latest_ready_install(
        session, workspace_id, NativeIntegrationProvider.ATLASSIAN
    )
    if atlassian is not None:
        for itype in ("confluence", "jira"):
            out.append(
                WorkspaceImporterIntegration(
                    importer_type=itype,
                    provider="atlassian",
                    account_name=atlassian.external_account_name,
                    account_url=atlassian.external_account_url,
                    provides_config_keys=_PROVIDED_CONFIG_KEYS.get(itype, ()),
                )
            )

    return out


async def resolve_workspace_importer_secrets(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    importer_type: str,
    settings: Settings,
) -> ResolvedImporterCreds | None:
    """Return creds (+ optional config patch) for the importer, or
    ``None`` when the workspace has no usable integration for it."""
    if importer_type not in SUPPORTED_TYPES:
        return None

    if importer_type in ("github_repo", "github_releases"):
        token = await _resolve_github_token(session, workspace_id, settings)
        return (
            ResolvedImporterCreds(secrets={"github_token": token})
            if token
            else None
        )
    if importer_type == "notion":
        token = await _resolve_native_access_token(
            session, workspace_id, NativeIntegrationProvider.NOTION
        )
        return (
            ResolvedImporterCreds(secrets={"integration_token": token})
            if token
            else None
        )
    if importer_type == "linear":
        token = await _resolve_native_access_token(
            session, workspace_id, NativeIntegrationProvider.LINEAR
        )
        return (
            ResolvedImporterCreds(secrets={"api_key": token}) if token else None
        )
    if importer_type == "gitlab":
        return await _resolve_gitlab(session, workspace_id)
    if importer_type == "confluence":
        return await _resolve_atlassian(session, workspace_id, target="confluence")
    if importer_type == "jira":
        return await _resolve_atlassian(session, workspace_id, target="jira")

    return None


async def _resolve_github_token(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    settings: Settings,
) -> str | None:
    install = (
        await session.execute(
            select(GitHubInstallation)
            .where(
                GitHubInstallation.workspace_id == workspace_id,
                GitHubInstallation.suspended_at.is_(None),
            )
            .order_by(GitHubInstallation.installed_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if install is None:
        return None

    from backend.app.integrations.github.app_auth import (
        fetch_installation_token,
    )

    try:
        return await fetch_installation_token(
            install.installation_id, settings=settings
        )
    except Exception:
        logger.warning(
            "github installation token mint failed for ws=%s install=%s",
            workspace_id,
            install.installation_id,
            exc_info=True,
        )
        return None


async def _resolve_native_access_token(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    provider: str,
) -> str | None:
    return await _resolve_native_credential(
        session, workspace_id, provider, kind="access_token"
    )


async def _resolve_native_credential(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    provider: str,
    *,
    kind: str,
) -> str | None:
    install = await _latest_ready_install(session, workspace_id, provider)
    if install is None:
        return None

    cred = (
        await session.execute(
            select(NativeIntegrationCredential)
            .where(
                NativeIntegrationCredential.installation_id == install.id,
                NativeIntegrationCredential.kind == kind,
                NativeIntegrationCredential.revoked_at.is_(None),
            )
            .order_by(NativeIntegrationCredential.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if cred is None:
        return None

    from backend.app.security.encryption import decrypt

    try:
        return decrypt(cred.secret_ciphertext)
    except Exception:
        logger.warning(
            "credential decrypt failed for ws=%s provider=%s kind=%s",
            workspace_id,
            provider,
            kind,
            exc_info=True,
        )
        return None


async def _resolve_gitlab(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> ResolvedImporterCreds | None:
    install = await _latest_ready_install(
        session, workspace_id, NativeIntegrationProvider.GITLAB
    )
    if install is None:
        return None
    token = await _resolve_native_credential(
        session, workspace_id, NativeIntegrationProvider.GITLAB, kind="pat"
    )
    if not token:
        return None
    config: dict[str, Any] = {}
    base_url = (install.config or {}).get("base_url")
    if isinstance(base_url, str) and base_url:
        config["base_url"] = base_url
    return ResolvedImporterCreds(
        secrets={"private_token": token}, config=config
    )


async def _resolve_atlassian(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    target: str,
) -> ResolvedImporterCreds | None:
    install = await _latest_ready_install(
        session, workspace_id, NativeIntegrationProvider.ATLASSIAN
    )
    if install is None:
        return None
    token = await _resolve_native_credential(
        session,
        workspace_id,
        NativeIntegrationProvider.ATLASSIAN,
        kind="api_token",
    )
    if not token:
        return None
    cfg = install.config or {}
    site_url = str(cfg.get("site_url") or "").rstrip("/")
    email = str(cfg.get("email") or "")
    if not site_url or not email:
        # The atlassian install is missing the metadata the importer
        # needs — operator should reconnect (or fill manually).
        return None

    if target == "confluence":
        # Confluence's REST endpoint sits under /wiki on Atlassian Cloud.
        return ResolvedImporterCreds(
            secrets={"api_token": token},
            config={
                "base_url": f"{site_url}/wiki",
                "user_name": email,
            },
        )
    # jira
    return ResolvedImporterCreds(
        secrets={"api_token": token},
        config={
            "server_url": site_url,
            "email": email,
        },
    )


async def _latest_ready_install(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    provider: str,
) -> NativeIntegrationInstallation | None:
    return (
        await session.execute(
            select(NativeIntegrationInstallation)
            .where(
                NativeIntegrationInstallation.workspace_id == workspace_id,
                NativeIntegrationInstallation.provider == provider,
                NativeIntegrationInstallation.status == NativeIntegrationStatus.READY,
                NativeIntegrationInstallation.disabled_at.is_(None),
            )
            .order_by(NativeIntegrationInstallation.connected_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
