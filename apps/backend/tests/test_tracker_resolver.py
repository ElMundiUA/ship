"""Unit tests for :mod:`backend.app.services.tracker_resolver`."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from backend.app.core.config import get_settings
from backend.app.db.models.integrations import (
    NativeIntegrationAuthMode,
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationProvider,
    NativeIntegrationStatus,
)
from backend.app.db.models.tenancy import Integration
from backend.app.integrations.jira.tracker_adapter import JiraTracker
from backend.app.integrations.linear.tracker_adapter import LinearTracker
from backend.app.services.tracker_resolver import resolve_for_workspace


def _tracker_resolver_settings():
    """CI sets SHIP_USE_MEMORY_ADAPTERS=true; resolver tests need real trackers."""
    return get_settings().model_copy(update={"use_memory_adapters": False})


_LEGACY_FSM_CONFIG = {
    "team_id": "team-legacy-uuid",
    "team_key": "LEG",
    "state_id_by_name": {"Todo": "state-todo"},
    "label_id_by_stage": {"planning": "label-plan"},
    "signal_label_ids": {"needs_clarification": "label-clarify"},
}


async def _seed_native_linear(
    db_session,
    workspace_id: uuid.UUID,
    *,
    install_config: dict | None = None,
    with_credential: bool = True,
) -> NativeIntegrationInstallation:
    install = NativeIntegrationInstallation(
        workspace_id=workspace_id,
        provider=NativeIntegrationProvider.LINEAR,
        auth_mode=NativeIntegrationAuthMode.OAUTH,
        external_account_id="default",
        external_account_name="Linear workspace",
        capabilities=["tracker"],
        scopes=["read", "write"],
        config=install_config
        if install_config is not None
        else {"scope": "read,write", "token_type": "Bearer"},
        status=NativeIntegrationStatus.READY,
    )
    db_session.add(install)
    await db_session.flush()
    if with_credential:
        db_session.add(
            NativeIntegrationCredential(
                installation_id=install.id,
                kind="access_token",
                secret_ciphertext=b"native-cipher",
                scopes=["read", "write"],
            )
        )
        await db_session.flush()
    return install


async def _seed_legacy_linear(
    db_session,
    workspace_id: uuid.UUID,
    *,
    config: dict | None = None,
    with_secret: bool = True,
) -> Integration:
    row = Integration(
        workspace_id=workspace_id,
        repo_id=None,
        kind="linear",
        config=config if config is not None else _LEGACY_FSM_CONFIG,
        status="ok",
        secret_ciphertext=b"legacy-cipher" if with_secret else None,
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _seed_native_jira(
    db_session,
    workspace_id: uuid.UUID,
    *,
    with_credential: bool = True,
) -> NativeIntegrationInstallation:
    install = NativeIntegrationInstallation(
        workspace_id=workspace_id,
        provider=NativeIntegrationProvider.ATLASSIAN,
        auth_mode=NativeIntegrationAuthMode.PAT,
        external_account_id="site-1",
        external_account_name="Atlassian site",
        capabilities=["tracker"],
        scopes=[],
        config={
            "site_url": "https://acme.atlassian.net",
            "email": "bot@acme.com",
            "jira_project": "SHIP",
        },
        status=NativeIntegrationStatus.READY,
    )
    db_session.add(install)
    await db_session.flush()
    if with_credential:
        db_session.add(
            NativeIntegrationCredential(
                installation_id=install.id,
                kind="api_token",
                secret_ciphertext=b"jira-cipher",
                scopes=[],
            )
        )
        await db_session.flush()
    return install


def _decrypt_side_effect(ciphertext: bytes) -> str:
    mapping = {
        b"native-cipher": "native-linear-token",
        b"legacy-cipher": "legacy-linear-token",
        b"jira-cipher": "jira-api-token",
    }
    return mapping.get(ciphertext, "unknown-token")


@pytest.mark.asyncio
async def test_native_linear_wins_and_layers_legacy_fsm_config(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    await _seed_native_linear(db_session, workspace.id, install_config={})
    await _seed_legacy_linear(db_session, workspace.id, config=_LEGACY_FSM_CONFIG)
    await db_session.commit()

    with patch(
        "backend.app.security.encryption.decrypt",
        side_effect=_decrypt_side_effect,
    ):
        resolved = await resolve_for_workspace(
            session=db_session,
            settings=_tracker_resolver_settings(),
            workspace_id=workspace.id,
        )

    assert resolved is not None
    assert resolved.source == "native"
    assert resolved.kind == "linear"
    gateway = resolved.gateway
    assert isinstance(gateway, LinearTracker)
    assert gateway._token == "native-linear-token"
    assert gateway._team_id == "team-legacy-uuid"
    assert gateway._team_key == "LEG"
    assert gateway._state_id_by_name == {"Todo": "state-todo"}
    assert gateway._label_id_by_stage == {"planning": "label-plan"}
    assert gateway._signal_label_ids == {"needs_clarification": "label-clarify"}


@pytest.mark.asyncio
async def test_legacy_linear_only(db_session, seed_workspace) -> None:
    _, _, workspace = seed_workspace
    await _seed_legacy_linear(db_session, workspace.id)
    await db_session.commit()

    with patch(
        "backend.app.security.encryption.decrypt",
        side_effect=_decrypt_side_effect,
    ):
        resolved = await resolve_for_workspace(
            session=db_session,
            settings=_tracker_resolver_settings(),
            workspace_id=workspace.id,
        )

    assert resolved is not None
    assert resolved.source == "legacy"
    assert isinstance(resolved.gateway, LinearTracker)
    assert resolved.gateway._token == "legacy-linear-token"


@pytest.mark.asyncio
async def test_native_jira(db_session, seed_workspace) -> None:
    _, _, workspace = seed_workspace
    await _seed_native_jira(db_session, workspace.id)
    await db_session.commit()

    with patch(
        "backend.app.security.encryption.decrypt",
        side_effect=_decrypt_side_effect,
    ):
        resolved = await resolve_for_workspace(
            session=db_session,
            settings=_tracker_resolver_settings(),
            workspace_id=workspace.id,
        )

    assert resolved is not None
    assert resolved.kind == "jira"
    assert resolved.source == "native"
    assert isinstance(resolved.gateway, JiraTracker)
    assert resolved.gateway._token == "jira-api-token"
    assert resolved.scope_hint == "SHIP"


@pytest.mark.asyncio
async def test_returns_none_when_unbound(db_session, seed_workspace) -> None:
    _, _, workspace = seed_workspace
    await db_session.commit()

    resolved = await resolve_for_workspace(
        session=db_session,
        settings=_tracker_resolver_settings(),
        workspace_id=workspace.id,
    )
    assert resolved is None


@pytest.mark.asyncio
async def test_returns_none_when_decrypt_returns_empty(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    await _seed_native_linear(db_session, workspace.id)
    await db_session.commit()

    with patch(
        "backend.app.security.encryption.decrypt",
        return_value="",
    ):
        resolved = await resolve_for_workspace(
            session=db_session,
            settings=_tracker_resolver_settings(),
            workspace_id=workspace.id,
        )

    assert resolved is None


@pytest.mark.asyncio
async def test_returns_none_when_native_credential_unreadable(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    await _seed_native_linear(db_session, workspace.id)
    await db_session.commit()

    with patch(
        "backend.app.security.encryption.decrypt",
        side_effect=RuntimeError("bad cipher"),
    ):
        resolved = await resolve_for_workspace(
            session=db_session,
            settings=_tracker_resolver_settings(),
            workspace_id=workspace.id,
        )

    assert resolved is None


@pytest.mark.asyncio
async def test_native_wins_over_legacy_token(db_session, seed_workspace) -> None:
    _, _, workspace = seed_workspace
    await _seed_native_linear(db_session, workspace.id)
    await _seed_legacy_linear(db_session, workspace.id)
    await db_session.commit()

    with patch(
        "backend.app.security.encryption.decrypt",
        side_effect=_decrypt_side_effect,
    ):
        resolved = await resolve_for_workspace(
            session=db_session,
            settings=_tracker_resolver_settings(),
            workspace_id=workspace.id,
        )

    assert resolved is not None
    assert resolved.source == "native"
    assert resolved.gateway._token == "native-linear-token"


@pytest.mark.asyncio
async def test_falls_through_to_legacy_when_native_has_no_credential(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    await _seed_native_linear(
        db_session, workspace.id, with_credential=False
    )
    await _seed_legacy_linear(db_session, workspace.id)
    await db_session.commit()

    with patch(
        "backend.app.security.encryption.decrypt",
        side_effect=_decrypt_side_effect,
    ):
        resolved = await resolve_for_workspace(
            session=db_session,
            settings=_tracker_resolver_settings(),
            workspace_id=workspace.id,
        )

    assert resolved is not None
    assert resolved.source == "legacy"
    assert resolved.gateway._token == "legacy-linear-token"
