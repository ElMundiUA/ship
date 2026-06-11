"""Generic per-workspace config scopes — discovery + mutation under one roof.

Phase 1d of the Navigator surface refactor consolidates a half-dozen
one-off workspace settings (agent provider, default agent profile,
catalog sources, …) behind a single pair of tools the LLM sees:

  * :func:`config_help(scope?)` — return the JSONSchema for a scope
    plus its current value, or list all available scopes with a one-
    line description each when ``scope`` is omitted.
  * :func:`config_put(scope, value)` — validate the payload against
    the scope's schema, apply it via the scope's writer, and emit an
    audit event under the scope's canonical action name.

Adding a new scope is a one-place change: declare a :class:`ConfigScope`
with a reader, writer, schema, and audit event name, then add it to
:data:`SCOPES`. Console UI auto-renders every scope from
``config_help`` output (Phase 1f), so a new scope shows up as a form
without any FE changes.

Design choices:

* **JSONSchema** as the lingua franca — same shape ToolSpec already
  uses to advertise tool parameters, so the LLM doesn't need a second
  vocabulary and the console can pipe it through ``@rjsf/core`` (or
  any JSONSchema-driven renderer).
* **Audit through the scope's own action name** so historical queries
  against, say, ``workspace.agent_provider.set`` keep matching after
  the surface collapses into the registry — no audit-log replay
  needed.
* **No fsm.stages / dispatch.routine / inbox.routing in this slice** —
  those touch multiple rows and need a richer schema (per-stage
  on/off vs. cron tuples vs. rule lists). They'll join the registry
  once the simpler scalar scopes prove the shape.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.tenancy import AuditLog, Workspace


# -----------------------------------------------------------------------------
# Scope contract
# -----------------------------------------------------------------------------


# A scope reader takes the workspace and returns the current value (any
# JSON-serialisable shape that matches the scope's schema). Async so it
# can hit the DB when the underlying field isn't on Workspace itself.
ScopeReader = Callable[[AsyncSession, Workspace], Awaitable[Any]]

# A scope writer takes the workspace + the validated new value and
# applies it (mutating the ORM row, inserting/deleting child rows, etc.).
# The caller flushes after; writer doesn't commit.
ScopeWriter = Callable[[AsyncSession, Workspace, Any], Awaitable[None]]


@dataclass(frozen=True)
class ConfigScope:
    """One configurable surface — schema, reader, writer, audit."""

    slug: str  # dotted, e.g. "agent.provider"
    description: str  # one line for help index
    schema: dict[str, Any]  # JSONSchema for the *value*
    read: ScopeReader
    write: ScopeWriter
    audit_event: str  # canonical action name for AuditLog


# -----------------------------------------------------------------------------
# Built-in scope handlers
# -----------------------------------------------------------------------------


async def _read_agent_provider(_s: AsyncSession, ws: Workspace) -> Any:
    return ws.agent_provider


async def _write_agent_provider(_s: AsyncSession, ws: Workspace, value: Any) -> None:
    from backend.app.services.agent_provider_resolver import SUPPORTED_PROVIDERS

    if value not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"agent_provider must be one of {sorted(SUPPORTED_PROVIDERS)}"
        )
    ws.agent_provider = str(value)


async def _read_default_agent_profile(_s: AsyncSession, ws: Workspace) -> Any:
    return ws.default_agent_profile


async def _write_default_agent_profile(
    _s: AsyncSession, ws: Workspace, value: Any
) -> None:
    # Local import: route module owns the canonical set; avoid the
    # circular import that would happen if the route imported the
    # registry instead.
    from backend.app.api.v1.routes.repos import _PROCESS_AGENT_PROFILES

    if value is not None and value not in _PROCESS_AGENT_PROFILES:
        raise ValueError(
            "default_agent_profile must be one of "
            f"{sorted(_PROCESS_AGENT_PROFILES)} (or null to clear)"
        )
    ws.default_agent_profile = value


_CONSOLE_SURFACES = ("full", "residual", "off")


async def _read_console_surface(_s: AsyncSession, ws: Workspace) -> Any:
    raw = (ws.settings or {}).get("console") or {}
    value = raw.get("surface")
    return value if value in _CONSOLE_SURFACES else "full"


async def _write_console_surface(_s: AsyncSession, ws: Workspace, value: Any) -> None:
    if value not in _CONSOLE_SURFACES:
        raise ValueError(
            f"console.surface must be one of {sorted(_CONSOLE_SURFACES)}"
        )
    settings = dict(ws.settings or {})
    console = dict(settings.get("console") or {})
    console["surface"] = str(value)
    settings["console"] = console
    # Reassign (not mutate) so SQLAlchemy's JSONB change detection fires.
    ws.settings = settings


async def _read_autonomy_profile(_s: AsyncSession, ws: Workspace) -> Any:
    from backend.app.services.agent_provider_resolver import (
        DEFAULT_AUTONOMY,
        SUPPORTED_AUTONOMY_PROFILES,
    )

    value = ws.autonomy
    return value if value in SUPPORTED_AUTONOMY_PROFILES else DEFAULT_AUTONOMY


async def _write_autonomy_profile(_s: AsyncSession, ws: Workspace, value: Any) -> None:
    from backend.app.services.agent_provider_resolver import (
        SUPPORTED_AUTONOMY_PROFILES,
    )

    if value not in SUPPORTED_AUTONOMY_PROFILES:
        raise ValueError(
            "autonomy.profile must be one of "
            f"{sorted(SUPPORTED_AUTONOMY_PROFILES)}"
        )
    ws.autonomy = str(value)


_CATALOG_KEYS = ("global", "workspace", "project")


async def _read_catalog_sources(_s: AsyncSession, ws: Workspace) -> Any:
    # Workspace.catalog_sources is a JSONB dict of bool flags. Return
    # the full shape (filling in missing keys with the default True)
    # so the LLM gets a complete picture even on a partial workspace.
    raw = dict(ws.catalog_sources or {})
    return {k: bool(raw.get(k, True)) for k in _CATALOG_KEYS}


async def _write_catalog_sources(
    _s: AsyncSession, ws: Workspace, value: Any
) -> None:
    if not isinstance(value, dict):
        raise ValueError("catalog_sources must be an object of bool flags")
    unknown = set(value) - set(_CATALOG_KEYS)
    if unknown:
        raise ValueError(
            f"unknown catalog source keys: {sorted(unknown)} "
            f"(expected: {sorted(_CATALOG_KEYS)})"
        )
    merged = {**(ws.catalog_sources or {}), **value}
    ws.catalog_sources = {k: bool(v) for k, v in merged.items()}


_SCOPE_LIST: tuple[ConfigScope, ...] = (
    ConfigScope(
        slug="agent.provider",
        description=(
            "Which agent runtime ``shipctl run`` spawns per FSM tick: "
            "Cursor / OpenAI Codex / Anthropic Claude Code. Each "
            "runtime reads its own API key from the GHA runner env."
        ),
        schema={
            "type": "string",
            "enum": ["cursor", "codex", "claude"],
            "description": (
                "Active agent runtime. Switching takes effect on the "
                "next cron tick — no in-flight runs interrupted."
            ),
        },
        read=_read_agent_provider,
        write=_write_agent_provider,
        audit_event="workspace.agent_provider.set",
    ),
    ConfigScope(
        slug="agent.default_profile",
        description=(
            "Default agent profile applied to brand-new pipeline / "
            "routine rows. Empty (null) means \"no default — the "
            "operator picks per-row\"."
        ),
        schema={
            "type": ["string", "null"],
            "description": (
                "Profile slug (matches ``_PROCESS_AGENT_PROFILES`` "
                "registry). Pass null to clear."
            ),
        },
        read=_read_default_agent_profile,
        write=_write_default_agent_profile,
        audit_event="workspace.default_agent_profile.set",
    ),
    ConfigScope(
        slug="catalog.sources",
        description=(
            "Which catalog layers feed this workspace's agent-role + "
            "pattern registry: ``global`` (Ship's defaults), "
            "``workspace`` (this workspace's overrides), ``project`` "
            "(per-repo .ship/config.yml). Toggle independently."
        ),
        schema={
            "type": "object",
            "properties": {
                "global": {"type": "boolean"},
                "workspace": {"type": "boolean"},
                "project": {"type": "boolean"},
            },
            "additionalProperties": False,
            "description": (
                "Partial updates merge into the existing flags."
            ),
        },
        read=_read_catalog_sources,
        write=_write_catalog_sources,
        audit_event="workspace.catalog_sources.set",
    ),
    ConfigScope(
        slug="console.surface",
        description=(
            "How much of the web console this workspace renders: "
            "``full`` (everything), ``residual`` (status + Inbox + "
            "irreducible controls only), ``off`` (health page only — "
            "the Inbox approval surface stays reachable). The headless "
            "kill-switch for the Phase-4 console strangler."
        ),
        schema={
            "type": "string",
            "enum": list(_CONSOLE_SURFACES),
            "description": (
                "Console surface mode. Default is ``full``; flipping "
                "is reversible at any time."
            ),
        },
        read=_read_console_surface,
        write=_write_console_surface,
        audit_event="workspace.console_surface.set",
    ),
    ConfigScope(
        slug="autonomy.profile",
        description=(
            "Thesis-7 autonomy dial: how much the agent may do on its "
            "own (skip approvals, self-merge, self-pick work). One of "
            "``high`` / ``balanced`` / ``conservative``. Never touches "
            "the lease/cap/cascade control plane."
        ),
        schema={
            "type": "string",
            "enum": ["high", "balanced", "conservative"],
            "description": (
                "Agent action-rights profile. ``balanced`` is the "
                "default; ``high`` is opt-in and requires a populated "
                "knowledge surface to work well."
            ),
        },
        read=_read_autonomy_profile,
        write=_write_autonomy_profile,
        audit_event="workspace.autonomy.set",
    ),
)


SCOPES: dict[str, ConfigScope] = {s.slug: s for s in _SCOPE_LIST}


# -----------------------------------------------------------------------------
# Public surface
# -----------------------------------------------------------------------------


def list_scopes() -> list[dict[str, str]]:
    """Return the slug + one-line description for every scope. Used
    by ``config_help()`` (no scope argument) and the console UI's
    settings landing page."""
    return [{"slug": s.slug, "description": s.description} for s in _SCOPE_LIST]


async def help_scope(
    session: AsyncSession, workspace: Workspace, slug: str
) -> dict[str, Any]:
    """Return the scope's JSONSchema + current value. Caller is
    responsible for the membership check; this helper assumes the
    user has read access to the workspace."""
    scope = SCOPES.get(slug)
    if scope is None:
        raise KeyError(slug)
    current = await scope.read(session, workspace)
    return {
        "slug": scope.slug,
        "description": scope.description,
        "schema": scope.schema,
        "current_value": current,
    }


async def put_scope(
    session: AsyncSession,
    workspace: Workspace,
    slug: str,
    value: Any,
    *,
    actor_user_id: uuid.UUID | None,
    actor_token_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Validate + write a new value. Raises :class:`KeyError` if the
    scope is unknown, :class:`ValueError` for shape violations the
    writer rejects. Emits an audit-log row under the scope's canonical
    action name so existing oncall queries still match."""
    scope = SCOPES.get(slug)
    if scope is None:
        raise KeyError(slug)
    await scope.write(session, workspace, value)
    session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=actor_user_id,
            actor_token_id=actor_token_id,
            action=scope.audit_event,
            target_kind="workspace",
            target_id=str(workspace.id),
            payload={"scope": slug, "value": value},
        )
    )
    await session.flush()
    new_value = await scope.read(session, workspace)
    return {
        "slug": slug,
        "value": new_value,
        "audit_event": scope.audit_event,
    }


__all__ = [
    "ConfigScope",
    "SCOPES",
    "list_scopes",
    "help_scope",
    "put_scope",
]
