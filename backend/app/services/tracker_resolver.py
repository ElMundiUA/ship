"""Pick a single :class:`TrackerGateway` for a workspace.

E14 model (locked 2026-04-30): a workspace is the project, so the
tracker is workspace-scoped — exactly one per workspace, regardless
of how many repos the workspace hosts. The CLI sends abstract
"transition this ticket from X to Y" calls; this helper centralises
the lookup of the bound tracker so every write-side route resolves
the same way.

Today's catalog:
- ``linear``  — workspace-level Linear OAuth row.
- ``jira``    — same shape, deferred until needed.

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


async def resolve_for_workspace(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
) -> ResolvedTracker | None:
    """Return the workspace's bound tracker, if any."""
    from backend.app.api.v1.routes.integrations import decrypt  # lazy

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
        token = await _decrypt_token(row, decrypt)
        if token is None:
            return None
        cfg = row.config or {}
        team_id = cfg.get("team_id")
        team_key = cfg.get("team_key")
        label_id_by_stage = cfg.get("label_id_by_stage") or {}
        state_id_by_name = cfg.get("state_id_by_name") or {}
        from backend.app.services.linear_provisioner import FSM_TO_LINEAR_STATE
        gateway = LinearTracker(
            token,
            team_id=team_id,
            team_key=team_key,
            label_id_by_stage=label_id_by_stage,
            state_id_by_name=state_id_by_name,
            fsm_to_linear_state=FSM_TO_LINEAR_STATE,
        )
        return ResolvedTracker(kind="linear", gateway=gateway, scope_hint=team_key)

    # Jira / others — wire when needed.
    logger.warning("workspace %s has tracker kind=%s, not yet wired", workspace_id, row.kind)
    return None


async def _decrypt_token(row: Integration, decrypt) -> str | None:
    if not row.secret_ciphertext:
        return None
    try:
        return decrypt(row.secret_ciphertext)
    except Exception:  # noqa: BLE001
        logger.warning("tracker token unreadable for integration=%s", row.id)
        return None
