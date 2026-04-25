"""Agent tracker resolution for native Linear installs."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_resolve_tracker_uses_native_linear_access_token(
    db_session,
    seed_workspace,
) -> None:
    from backend.app.core.config import get_settings
    from backend.app.db.models.integrations import (
        NativeIntegrationAuthMode,
        NativeIntegrationCredential,
        NativeIntegrationInstallation,
        NativeIntegrationProvider,
        NativeIntegrationStatus,
    )
    from backend.app.integrations.linear.tracker_adapter import LinearTracker
    from backend.app.security.encryption import encrypt
    from backend.app.services.agent.tools import ToolBox

    user, _, workspace = seed_workspace
    install = NativeIntegrationInstallation(
        workspace_id=workspace.id,
        provider=NativeIntegrationProvider.LINEAR,
        auth_mode=NativeIntegrationAuthMode.OAUTH,
        external_account_id="default",
        external_account_name="Linear workspace",
        external_account_url="https://linear.app",
        capabilities=["tracker"],
        scopes=["read", "write"],
        config={"scope": "read,write", "token_type": "Bearer"},
        status=NativeIntegrationStatus.READY,
    )
    db_session.add(install)
    await db_session.flush()
    db_session.add(
        NativeIntegrationCredential(
            installation_id=install.id,
            kind="access_token",
            secret_ciphertext=encrypt("native_linear_token"),
            secret_fingerprint="test",
            scopes=["read", "write"],
        )
    )
    await db_session.flush()

    toolbox = ToolBox(
        db_session,
        settings=get_settings(),
        workspace_id=workspace.id,
        user_id=user.id,
    )

    tracker = await toolbox._resolve_tracker(None, None)

    assert isinstance(tracker, LinearTracker)
    assert tracker._token == "native_linear_token"

