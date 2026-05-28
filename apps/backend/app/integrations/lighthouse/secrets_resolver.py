"""Resolve Lighthouse importer secrets from Ship's own integrations.

When the operator picks an importer type Ship already has an integration
for (GitHub App, Notion OAuth, Linear OAuth, …), we can fill the
secrets dict server-side instead of asking the operator to paste a
token. The token never crosses the wire to the browser.

The mapping is intentionally narrow — only the importer types where
Ship has a first-party native installation in
``NativeIntegrationInstallation`` (or a ``GitHubInstallation`` for the
App flow). Bitbucket / Asana / Trello / Slack stay manual.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

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
class WorkspaceImporterIntegration:
    """One importer type the workspace has auto-resolvable credentials for."""

    importer_type: str
    provider: str
    account_name: str | None
    account_url: str | None


# Importer types we know how to resolve credentials for.
SUPPORTED_TYPES: frozenset[str] = frozenset(
    {
        "github_repo",
        "github_releases",
        "notion",
        "linear",
    }
)


async def list_workspace_importer_integrations(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> list[WorkspaceImporterIntegration]:
    """Return the importer types the workspace can auto-authorize.

    One entry per importer type that has a working Ship integration —
    so ``github_repo`` and ``github_releases`` each appear separately
    when the GitHub App is installed, even though they share one
    installation row.
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
                )
            )

    # ── Native OAuth installations (Notion, Linear) ──
    native_pairs = (
        (NativeIntegrationProvider.NOTION, "notion"),
        (NativeIntegrationProvider.LINEAR, "linear"),
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
            )
        )

    return out


async def resolve_workspace_importer_secrets(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    importer_type: str,
    settings: Settings,
) -> dict[str, str] | None:
    """Return the secret dict to inject into a Lighthouse importer
    request, or ``None`` when the workspace has no usable integration
    for this importer type."""
    if importer_type not in SUPPORTED_TYPES:
        return None

    if importer_type in ("github_repo", "github_releases"):
        return await _resolve_github(session, workspace_id, settings)
    if importer_type == "notion":
        token = await _resolve_native_access_token(
            session, workspace_id, NativeIntegrationProvider.NOTION
        )
        return {"integration_token": token} if token else None
    if importer_type == "linear":
        token = await _resolve_native_access_token(
            session, workspace_id, NativeIntegrationProvider.LINEAR
        )
        return {"api_key": token} if token else None

    return None


async def _resolve_github(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    settings: Settings,
) -> dict[str, str] | None:
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

    # Lazy import to avoid pulling jose / private-key paths in unrelated
    # codepaths (and to keep import time fast).
    from backend.app.integrations.github.app_auth import (
        fetch_installation_token,
    )

    try:
        token = await fetch_installation_token(
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
    return {"github_token": token}


async def _resolve_native_access_token(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    provider: str,
) -> str | None:
    install = await _latest_ready_install(session, workspace_id, provider)
    if install is None:
        return None

    cred = (
        await session.execute(
            select(NativeIntegrationCredential)
            .where(
                NativeIntegrationCredential.installation_id == install.id,
                NativeIntegrationCredential.kind == "access_token",
                NativeIntegrationCredential.revoked_at.is_(None),
            )
            .order_by(NativeIntegrationCredential.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if cred is None:
        return None

    # Lazy import for the same reason as github.
    from backend.app.security.encryption import decrypt

    try:
        return decrypt(cred.secret_ciphertext)
    except Exception:
        logger.warning(
            "credential decrypt failed for ws=%s provider=%s",
            workspace_id,
            provider,
            exc_info=True,
        )
        return None


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
