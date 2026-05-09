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


TrackerKind = Literal["linear", "jira", "notion"]


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
    # OAuth scopes the stored token was issued with. Surfaced into
    # the dashboard so an admin can eyeball whether ``read`` is
    # actually granted before chasing 401 ghosts.
    scopes: tuple[str, ...] = ()


async def resolve_for_workspace(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
) -> ResolvedTracker | None:
    """Return the workspace's bound tracker, if any.

    A workspace binds to one tracker. We probe for the bound row in
    this order:

    1. Native ``LINEAR`` installation (READY + live ``access_token``).
    2. Native ``ATLASSIAN`` installation (READY + live ``api_token``)
       — produces a JiraTracker.
    3. Legacy ``Integration(kind='linear', repo_id IS NULL)`` row.

    The legacy fallback exists only because ``linear_oauth.py`` still
    writes the FSM-provisioned config (team_id / team_key /
    state_id_by_name / label_id_by_stage) to the legacy row; native
    installations produced before that migration land here. New
    bindings ride exclusively on the native installation row.
    """
    from backend.app.security.encryption import decrypt

    legacy_row = await _load_legacy_linear_row(session, workspace_id)
    legacy_config = (legacy_row.config or {}) if legacy_row else {}

    # ``settings`` is threaded into the native Linear path so the
    # token-refresh service has access to LINEAR_CLIENT_ID /
    # LINEAR_CLIENT_SECRET when it has to call Linear's token
    # endpoint mid-resolve. Other adapters ignore it.
    native = await _resolve_native_linear(
        session,
        workspace_id,
        decrypt,
        legacy_config=legacy_config,
        settings=settings,
    )
    if native is not None:
        return native

    jira = await _resolve_native_jira(session, workspace_id, decrypt)
    if jira is not None:
        return jira

    if legacy_row is None:
        return None
    return _resolve_from_legacy_row(legacy_row, decrypt)


async def _resolve_native_jira(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    decrypt,
) -> ResolvedTracker | None:
    from backend.app.integrations.jira.tracker_adapter import JiraTracker

    install = (
        await session.execute(
            select(NativeIntegrationInstallation)
            .where(
                NativeIntegrationInstallation.workspace_id == workspace_id,
                NativeIntegrationInstallation.provider
                == NativeIntegrationProvider.ATLASSIAN,
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
                NativeIntegrationCredential.kind == "api_token",
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
            "native atlassian token unreadable for installation=%s", install.id
        )
        return None
    if not token:
        return None

    config = install.config or {}
    site_url = str(config.get("site_url") or "").strip()
    email = str(config.get("email") or "").strip()
    if not site_url or not email:
        logger.warning(
            "atlassian installation=%s missing site_url/email config", install.id
        )
        return None

    gateway = JiraTracker(
        site_url=site_url,
        email=email,
        api_token=token,
        default_project=config.get("jira_project"),
    )
    return ResolvedTracker(
        kind="jira",
        gateway=gateway,
        scope_hint=config.get("jira_project"),
        source="native",
        last_health_at=install.last_health_at,
        last_health_error=install.last_health_error,
        scopes=tuple(install.scopes or ()),
    )


async def _load_legacy_linear_row(
    session: AsyncSession, workspace_id: uuid.UUID
) -> Integration | None:
    return (
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


async def _resolve_native_linear(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    decrypt,
    *,
    legacy_config: dict | None = None,
    settings=None,
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

    # Token freshness is owned by the scheduled
    # ``linear_token_refresh_tick`` cron (every
    # ``LINEAR_TOKEN_REFRESH_HOURS`` hours), not by this hot path.
    # Resolving in-line per-request would add a second-long Linear
    # round-trip to the picker; doing it on a fixed cadence keeps a
    # rolling-fresh access token in the credential row regardless of
    # how often the workspace is hit. ``settings`` stays in the
    # signature for any future inline knobs but is intentionally
    # unused here.
    del settings

    # ``linear_oauth.py`` writes the FSM-provisioned config (team_id /
    # team_key / state_id_by_name / label_id_by_stage / signal_label_ids)
    # to the *legacy* ``Integration.config`` row. The native row only
    # gets ``{scope, token_type}``. Layering the legacy blob under
    # native lets the dashboard scope ``list_projects`` to the right
    # team and the agent transition tickets without a second lookup.
    config = dict(install.config or {})
    if legacy_config:
        for key in (
            "team_id",
            "team_key",
            "label_id_by_stage",
            "state_id_by_name",
            "signal_label_ids",
        ):
            if key not in config and key in legacy_config:
                config[key] = legacy_config[key]
    return _build_linear_resolved(
        token,
        config,
        source="native",
        last_health_at=install.last_health_at,
        last_health_error=install.last_health_error,
        scopes=tuple(install.scopes or ()),
    )


def _resolve_from_legacy_row(
    row: Integration, decrypt
) -> ResolvedTracker | None:
    if row.kind == "linear":
        token = _decrypt_legacy_token(row, decrypt)
        if token is None:
            return None
        scope_raw = (row.config or {}).get("scope")
        scopes = tuple(
            s.strip() for s in str(scope_raw or "").split(",") if s.strip()
        )
        return _build_linear_resolved(
            token,
            row.config or {},
            source="legacy",
            last_health_at=row.last_health_at,
            last_health_error=row.last_health_error,
            scopes=scopes,
        )

    # Jira / others — wire when needed.
    logger.warning(
        "workspace has tracker kind=%s, not yet wired", row.kind
    )
    return None


def _build_linear_resolved(
    token: str,
    config: dict,
    *,
    source: Literal["native", "legacy"],
    last_health_at: object | None,
    last_health_error: str | None,
    scopes: tuple[str, ...] = (),
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
        scopes=scopes,
    )


def _decrypt_legacy_token(row: Integration, decrypt) -> str | None:
    if not row.secret_ciphertext:
        return None
    try:
        return decrypt(row.secret_ciphertext)
    except Exception:  # noqa: BLE001
        logger.warning("tracker token unreadable for integration=%s", row.id)
        return None
