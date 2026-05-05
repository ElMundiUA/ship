"""Pick a single :class:`TrackerGateway` for a workspace.

E14 model (locked 2026-04-30): a workspace is the project, so the
tracker is workspace-scoped — exactly one per workspace, regardless
of how many repos the workspace hosts. The CLI sends abstract
"transition this ticket from X to Y" calls; this helper centralises
the lookup of the bound tracker so every write-side route resolves
the same way.

Today's catalog:
- ``linear``  — native installation (preferred) or legacy
  workspace-level Linear OAuth row.
- ``jira``    — same shape, deferred until needed.

Two storage shapes coexist while the native-integration migration
is in flight: the legacy ``Integration`` table (with
``secret_ciphertext`` inline) and the newer
``NativeIntegrationInstallation`` + ``NativeIntegrationCredential``
pair. The agent's runtime
(:meth:`backend.app.services.agent.tools.AgentToolset._resolve_tracker`)
prefers the native pair when both exist; this resolver mirrors that
ordering so dashboards and the agent don't accidentally read different
tokens — the symptom of the old (legacy-only) behaviour was the
dashboard 401-ing on Linear while the agent kept working.

Returns ``None`` when no tracker is bound — callers map that to
``no_tracker_bound`` and the agent surfaces ``blocked`` cleanly.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.integrations import (
    NativeIntegrationCredential,
    NativeIntegrationInstallation,
    NativeIntegrationProvider,
    NativeIntegrationStatus,
)
from backend.app.db.models.tenancy import Integration
from backend.app.integrations.gateway.tracker import TrackerGateway
from backend.app.integrations.linear.tracker_adapter import LinearTracker


logger = logging.getLogger(__name__)


TrackerKind = Literal["linear", "jira"]


@dataclass(frozen=True)
class ResolvedTracker:
    kind: TrackerKind
    gateway: TrackerGateway
    # Vendor-specific scope hint (Linear team key, Jira project key).
    # Adapters fall back to "the only available scope" when unset.
    scope_hint: str | None
    # Provenance + health pass-through so the dashboard can render
    # "Linear · synced 2m" without a second query against the same
    # row. ``source`` reflects which storage shape carried the token
    # — native installation row vs. legacy Integration row.
    source: Literal["native", "legacy"] | None = None
    last_health_at: object | None = None
    last_health_error: str | None = None


async def resolve_for_workspace(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
) -> ResolvedTracker | None:
    """Return the workspace's bound tracker, if any.

    Resolution order matches
    :meth:`AgentToolset._resolve_tracker` so dashboard-side reads
    pick the same row the agent is actually authenticating with:

    1. Native ``LINEAR`` installation in ``READY`` state with a live
       ``access_token`` credential.
    2. Legacy ``Integration(kind='linear', repo_id IS NULL)`` row with
       a stored ``secret_ciphertext``.
    """
    del settings  # No vendor-specific knobs needed here yet.
    from backend.app.security.encryption import decrypt

    native = await _resolve_native_linear(session, workspace_id, decrypt)
    if native is not None:
        return native
    return await _resolve_legacy_linear(session, workspace_id, decrypt)


async def _resolve_native_linear(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    decrypt,
) -> ResolvedTracker | None:
    install = (
        await session.execute(
            select(NativeIntegrationInstallation)
            .where(
                NativeIntegrationInstallation.workspace_id == workspace_id,
                NativeIntegrationInstallation.provider
                == NativeIntegrationProvider.LINEAR,
                NativeIntegrationInstallation.status
                == NativeIntegrationStatus.READY,
                NativeIntegrationInstallation.disabled_at.is_(None),
            )
            .order_by(NativeIntegrationInstallation.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if install is None:
        return None

    credential = (
        await session.execute(
            select(NativeIntegrationCredential).where(
                NativeIntegrationCredential.installation_id == install.id,
                NativeIntegrationCredential.kind == "access_token",
                NativeIntegrationCredential.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if credential is None or not credential.secret_ciphertext:
        return None

    try:
        token = decrypt(credential.secret_ciphertext)
    except Exception:  # noqa: BLE001
        logger.warning(
            "native linear token unreadable for installation=%s", install.id
        )
        return None
    if not token:
        return None

    return _build_linear_resolved(
        token,
        install.config or {},
        source="native",
        last_health_at=install.last_health_at,
        last_health_error=install.last_health_error,
    )


async def _resolve_legacy_linear(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    decrypt,
) -> ResolvedTracker | None:
    row = (
        await session.execute(
            select(Integration).where(
                Integration.workspace_id == workspace_id,
                Integration.repo_id.is_(None),
                Integration.kind.in_(("linear", "jira")),
            )
            .order_by(Integration.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None

    if row.kind == "linear":
        token = _decrypt_legacy_token(row, decrypt)
        if token is None:
            return None
        return _build_linear_resolved(
            token,
            row.config or {},
            source="legacy",
            last_health_at=row.last_health_at,
            last_health_error=row.last_health_error,
        )

    # Jira / others — wire when needed.
    logger.warning(
        "workspace %s has tracker kind=%s, not yet wired", workspace_id, row.kind
    )
    return None


def _build_linear_resolved(
    token: str,
    config: dict,
    *,
    source: Literal["native", "legacy"],
    last_health_at: object | None,
    last_health_error: str | None,
) -> ResolvedTracker:
    """Hydrate a ``ResolvedTracker`` from a Linear token + config blob.

    Both the native and legacy code paths converge here so the
    workspace's configured default team / FSM mapping reach the
    adapter regardless of which storage shape carried the token.
    """
    from backend.app.services.linear_provisioner import FSM_TO_LINEAR_STATE

    team_id = config.get("team_id")
    team_key = config.get("team_key")
    gateway = LinearTracker(
        token,
        team_id=team_id,
        team_key=team_key,
        label_id_by_stage=config.get("label_id_by_stage") or {},
        state_id_by_name=config.get("state_id_by_name") or {},
        fsm_to_linear_state=FSM_TO_LINEAR_STATE,
        signal_label_ids=config.get("signal_label_ids") or {},
    )
    return ResolvedTracker(
        kind="linear",
        gateway=gateway,
        scope_hint=team_key,
        source=source,
        last_health_at=last_health_at,
        last_health_error=last_health_error,
    )


def _decrypt_legacy_token(row: Integration, decrypt) -> str | None:
    if not row.secret_ciphertext:
        return None
    try:
        return decrypt(row.secret_ciphertext)
    except Exception:  # noqa: BLE001
        logger.warning("tracker token unreadable for integration=%s", row.id)
        return None
