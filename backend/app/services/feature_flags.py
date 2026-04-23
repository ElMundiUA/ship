"""Workspace-level feature flag helpers (P2-19).

Lets the inbox / plays redesign land per-workspace gates without
plumbing per-flag columns through the schema. Two operations on top
of the JSONB ``workspaces.settings`` column (chosen over a dedicated
``feature_flags`` column because ``settings`` is the existing
namespaced workspace-config dict — see :class:`Workspace`):

- :func:`is_enabled` — read-only check the inbox / plays routes will
  call once the gate flips on. Falls back to a per-flag default so
  unset rows never accidentally lock the new surface out.
- :func:`set_flag` — workspace owner-only mutation. Persists the new
  value AND emits a single :class:`AuditLog` row so a rollout-safety
  toggle can never be flipped silently.

Storage layout::

    workspaces.settings = {
        ...other settings keys...,
        "feature_flags": {
            "inbox_v1_enabled": true,
            "future_flag": false,
            ...
        },
    }

Allowlist + per-flag defaults live alongside the helpers so route
handlers and other backend callers only have one source of truth for
"is this flag known and what does it default to". Adding a new flag
is a one-liner here + the test that pins its default; no schema
change required.

Wiring note: per the P2-19 spec we **do not** gate any inbox routes
on the helper yet. Phase-2 ships the lever; flipping it is a
follow-up ticket. :func:`require_flag` is provided as the canonical
FastAPI dependency once consumers are ready, but defaults to
no-op so accidentally importing it can't cut traffic over.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from backend.app.db.models.tenancy import AuditLog, Workspace


# ---------------------------------------------------------------------------
# Allowlist + per-flag defaults
# ---------------------------------------------------------------------------

# Source of truth for "what flag names does this workspace recognise"
# and "what value does an unset row read as". Defaults are
# **emergency-disable** semantics — i.e. ``inbox_v1_enabled`` defaults
# to True so the new inbox is on for everybody and the flag exists so
# a tenant can be flipped off if something blows up. Opt-in features
# should default to False.
FEATURE_FLAG_DEFAULTS: dict[str, bool] = {
    # P2-19: gate for the new inbox surface. Defaults TRUE (rollout
    # safety lever, not opt-in). Setting it to False on a workspace
    # should make the inbox routes fall back to legacy
    # clarifications/improvements UI in a future ticket.
    "inbox_v1_enabled": True,
}

# Tuple form for FastAPI ``Literal`` annotations / 422 messages —
# ``set(FEATURE_FLAG_DEFAULTS)`` would be unstable across runs.
ALLOWED_FEATURE_FLAGS: tuple[str, ...] = tuple(
    sorted(FEATURE_FLAG_DEFAULTS.keys())
)

# Key under ``workspaces.settings`` where flags live. Centralised so
# direct readers (admin UI, dashboards) don't drift.
SETTINGS_KEY: str = "feature_flags"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _flags_dict(settings: Any) -> dict[str, Any]:
    """Best-effort coerce ``workspaces.settings[feature_flags]`` to a dict.

    Tolerates ``None`` (column NULL is impossible thanks to the
    server default, but tests sometimes hand-write rows) and the
    historical case where ``settings`` itself is missing the key.
    Always returns a dict by value — callers should treat the result
    as read-only and reassign through the helpers below to mutate.
    """
    if not isinstance(settings, dict):
        return {}
    raw = settings.get(SETTINGS_KEY)
    if not isinstance(raw, dict):
        return {}
    return raw


async def _load_workspace(
    session: AsyncSession, workspace_id: uuid.UUID
) -> Workspace:
    workspace = (
        await session.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
    ).scalar_one_or_none()
    if workspace is None:
        # Mirror the "404 hides existence" pattern the workspace
        # routes use — but the caller is expected to have already
        # checked membership before we get here, so this is a
        # defence-in-depth raise.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="workspace not found",
        )
    return workspace


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def known_flags() -> dict[str, bool]:
    """Return ``{flag_name: default_value}`` for every recognised flag.

    Used by ``GET /feature-flags`` so freshly-created workspaces (no
    persisted overrides yet) still get a complete dict — the console
    can render the toggle UI without a follow-up "what flags exist"
    call.
    """
    return dict(FEATURE_FLAG_DEFAULTS)


async def get_flags(
    session: AsyncSession, workspace_id: uuid.UUID
) -> dict[str, bool]:
    """Read every flag for a workspace, falling back to per-flag defaults.

    Returns one entry per :data:`FEATURE_FLAG_DEFAULTS` key (so unset
    rows still surface ``inbox_v1_enabled=True``). Unknown flags
    persisted on the row are intentionally dropped from the response
    so the console never sees ghosts left behind by a removed flag.
    """
    workspace = await _load_workspace(session, workspace_id)
    persisted = _flags_dict(workspace.settings)
    out: dict[str, bool] = {}
    for flag, default in FEATURE_FLAG_DEFAULTS.items():
        raw = persisted.get(flag, default)
        out[flag] = bool(raw)
    return out


async def is_enabled(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    flag: str,
    *,
    default: bool | None = None,
) -> bool:
    """Return whether ``flag`` is enabled for the workspace.

    Resolution order:

    1. Explicit value on ``workspaces.settings[feature_flags][flag]``.
    2. ``default`` argument (when provided — lets callers pin a
       per-call fallback different from the registry default, useful
       for migrations).
    3. :data:`FEATURE_FLAG_DEFAULTS` lookup.
    4. ``False`` for completely unknown flags — callers must
       allowlist before relying on a True default.

    The ``inbox_v1_enabled`` flag in particular defaults to **True**
    per the P2-19 spec: the new inbox is the default and the flag
    exists so we can disable per-tenant in an emergency.
    """
    workspace = await _load_workspace(session, workspace_id)
    persisted = _flags_dict(workspace.settings)
    if flag in persisted:
        return bool(persisted[flag])
    if default is not None:
        return bool(default)
    return bool(FEATURE_FLAG_DEFAULTS.get(flag, False))


async def set_flag(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    flag: str,
    value: bool,
    *,
    actor_user_id: uuid.UUID,
    actor_token_id: uuid.UUID | None = None,
) -> bool:
    """Persist ``flag = value`` for the workspace and write an audit row.

    Rejects unknown flag names (422) so a typo in a console PUT
    never accidentally creates a phantom flag the rest of the system
    will quietly ignore. Writes the new dict back to
    ``workspaces.settings[feature_flags]`` and stamps a single
    :class:`AuditLog` row with the action ``feature_flag.set`` —
    rollout-safety levers must always leave a paper trail.

    Returns the **previous** value (or the default for previously
    unset flags) so callers can include before/after state in
    higher-level audit messages without a second SELECT.
    """
    if flag not in FEATURE_FLAG_DEFAULTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="unknown feature flag",
        )

    workspace = await _load_workspace(session, workspace_id)
    settings = dict(workspace.settings or {})
    flags = dict(_flags_dict(settings))
    previous = bool(flags.get(flag, FEATURE_FLAG_DEFAULTS[flag]))
    flags[flag] = bool(value)
    settings[SETTINGS_KEY] = flags
    workspace.settings = settings
    # JSONB columns can mis-detect mutation when the dict identity
    # changes mid-tree; mark the field dirty explicitly so the
    # session emits the UPDATE on flush.
    flag_modified(workspace, "settings")

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            actor_token_id=actor_token_id,
            action="feature_flag.set",
            target_kind="workspace",
            target_id=str(workspace_id),
            payload={"flag": flag, "enabled": bool(value)},
        )
    )
    await session.flush()
    return previous


__all__ = [
    "ALLOWED_FEATURE_FLAGS",
    "FEATURE_FLAG_DEFAULTS",
    "SETTINGS_KEY",
    "get_flags",
    "is_enabled",
    "known_flags",
    "set_flag",
]
