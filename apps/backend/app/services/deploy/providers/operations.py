"""Provider lifecycle operation dispatcher.

This is the narrow place where provider identifiers map to concrete adapters.
Routes/services call these functions instead of importing provider REST clients
or adapter-specific lifecycle methods directly.
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.services.deploy.providers.base import (
    ProviderRef,
    ProviderRollbackResult,
)
from backend.app.services.deploy.providers.digitalocean import DigitalOceanAppPlatform
from backend.app.services.deploy.credentials import get_do_token


class ProviderOperationUnsupported(RuntimeError):
    pass


async def get_provider_token(
    session: AsyncSession,
    workspace_id,
    provider: str,
) -> str | None:
    if provider == "digitalocean":
        return await get_do_token(session, workspace_id)
    raise ProviderOperationUnsupported(f"{provider} credentials are not supported")


async def delete_provider_app(*, provider: str, token: str, app_id: str) -> bool:
    if provider == "digitalocean":
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
            adapter = DigitalOceanAppPlatform(
                token=token,
                full_name="",
                private=False,
                http_client=http,
            )
            return await adapter.delete_app(app_id)
    raise ProviderOperationUnsupported(f"{provider} teardown is not supported")


async def rollback_provider_deployment(
    *,
    token: str,
    ref: ProviderRef,
) -> ProviderRollbackResult:
    if ref.provider == "digitalocean":
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
            adapter = DigitalOceanAppPlatform(
                token=token,
                full_name="",
                private=False,
                http_client=http,
            )
            return await adapter.rollback(ref)
    raise ProviderOperationUnsupported(f"{ref.provider} rollback is not supported")


__all__ = [
    "ProviderOperationUnsupported",
    "delete_provider_app",
    "get_provider_token",
    "rollback_provider_deployment",
]
