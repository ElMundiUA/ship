"""Resolve a decrypted DigitalOcean access token for a workspace.

Looks up the workspace's ``digitalocean`` native integration installation,
finds the ``access_token`` credential, and decrypts it. Returns ``None``
when the workspace has no connected DigitalOcean account.

This is the single place where deploy code touches the credential store;
everything downstream receives a plain ``str`` token.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.integrations import (
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationProvider,
    NativeIntegrationStatus,
)
from backend.app.security.encryption import safe_decrypt


logger = logging.getLogger(__name__)


async def get_do_token(
    session: AsyncSession, workspace_id: uuid.UUID
) -> str | None:
    """Return the decrypted DO access token for ``workspace_id``, or None."""
    native = (
        await session.execute(
            select(NativeIntegrationInstallation).where(
                NativeIntegrationInstallation.workspace_id == workspace_id,
                NativeIntegrationInstallation.provider
                == NativeIntegrationProvider.DIGITALOCEAN,
                NativeIntegrationInstallation.status == NativeIntegrationStatus.READY,
            )
        )
    ).scalar_one_or_none()
    if native is None:
        return None

    cred = (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == native.id,
                NativeIntegrationCredential.kind == "access_token",
                NativeIntegrationCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if cred is None:
        return None

    token = safe_decrypt(cred.secret_ciphertext)
    if token is None:
        logger.warning(
            "DO access token for workspace %s could not be decrypted "
            "(key rotation needed?)",
            workspace_id,
        )
    return token


__all__ = ["get_do_token"]
