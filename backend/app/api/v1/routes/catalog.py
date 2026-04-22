"""Authenticated catalog surface — ``/v1/catalog/*``.

Exposes the artifact catalog (patterns, tools, collections) through the
v1 API so the operator console, CLI, and wizards can render presets and
pin versions without re-implementing the frontmatter parser on the
client side.

The unauthenticated ``/patterns`` / ``/collections`` / ``/tools``
endpoints on :mod:`backend.app.main` remain for public read-only
access (they're what `shipctl` talks to without a PAT). This router
layers the same data behind the workspace auth context so presets can
be gated by login when needed — and keeps the response shape thin
(summary only, no markdown body) because the console uses it for
picker UIs, not content rendering.

RFC-0007 Phase 6 retired ``artifact_kind=workflow`` from the public
catalog; the Pipeline install flow keeps its own internal lookup via
:mod:`backend.app.services.starter_workflows`.
"""

from __future__ import annotations

from typing import Any

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.custom_patterns import CustomPattern
from backend.app.db.session import get_session
from backend.app.services import catalog as catalog_service
from backend.app.services.lane_recipes import list_lane_recipes


router = APIRouter(prefix="/catalog", tags=["catalog"])


class CatalogEntryOut(BaseModel):
    """Public-facing summary of a catalog artifact."""

    kind: str
    id: str
    name: str | None
    version: str | None
    channel: str | None
    group: str | None
    tags: list[str]
    description: str
    # Pre-RFC-0008 frontmatter occasionally stored a numeric ``0``
    # placeholder instead of a real digest (see RFC-0009 Wave 2
    # pattern backfill). Coerce any non-string scalar to its ``str``
    # form so the picker endpoints keep responding 200 while the
    # catalog backfill is in flight.
    content_sha256: str | None
    updated_at: Any | None = None

    @field_validator("content_sha256", mode="before")
    @classmethod
    def _coerce_content_sha256(cls, value: Any) -> Any:
        if value is None or isinstance(value, str):
            return value
        return str(value)
    deprecated: bool
    replaced_by: str | None
    yanked: bool
    # Preset-only field (``None`` unless ``spec.preset_id`` is set).
    preset_id: str | None = None
    # RFC-0008 metadata. ``None``/``[]`` on pre-RFC artifacts so the UI
    # can treat missing values as "legacy, show everywhere". Phase-1
    # (rename) populates them for every pattern.
    category: str | None = None
    modes: list[str] = []
    default_trigger: dict[str, Any] | None = None
    lane_workflow: str | None = None
    # Convenience — backend-computed starter YAML id (falls back to a
    # default when ``lane_workflow`` is absent). The UI reads this when
    # it renders the "Advanced → Override" widget.
    resolved_lane_workflow: str | None = None
    include: list[str] = []
    inputs: list[dict[str, Any]] = []
    enabled_on_install: dict[str, Any] = {}
    # RFC-0008 §H (PR-6) — ``"builtin"`` for filesystem patterns,
    # ``"workspace"`` for rows authored via the Console's AI author.
    # Pickers surface a "custom" badge + delete action only on the
    # latter.
    source: str = "builtin"


def _serialise(entries: list) -> list[CatalogEntryOut]:
    return [CatalogEntryOut(**entry.to_summary()) for entry in entries]


@router.get("/presets", response_model=list[CatalogEntryOut])
async def list_presets(
    _: AuthContext = Depends(get_current_auth),
) -> list[CatalogEntryOut]:
    """Collections with ``group: preset`` — drives the wizard preset picker."""
    return _serialise(catalog_service.list_presets())


@router.get("/collections", response_model=list[CatalogEntryOut])
async def list_collections(
    _: AuthContext = Depends(get_current_auth),
) -> list[CatalogEntryOut]:
    """Every collection (presets, addendums, agent-rules) for tooling pickers."""
    return _serialise(catalog_service.list_collections())


@router.get("/patterns", response_model=list[CatalogEntryOut])
async def list_patterns(
    mode: str | None = Query(
        default=None,
        description=(
            "Filter to patterns whose ``spec.modes`` contains this "
            "invocation surface. Accepts ``lane`` or ``request``; omit "
            "to return every non-``common-*`` pattern."
        ),
    ),
    workspace_id: uuid.UUID | None = Query(
        default=None,
        description=(
            "Merge workspace-private patterns (RFC-0008 §H / PR-6) on "
            "top of the baked-in catalog. Caller must be a member of "
            "the workspace. Omit to return baked-in only."
        ),
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[CatalogEntryOut]:
    """Pattern catalog with RFC-0008 metadata — drives Lanes Library + Requests grid.

    Non-``common-*`` patterns only; the ``common-*`` helpers (empty
    ``modes``) are never user-facing entry points. When ``mode`` is
    set, legacy pre-RFC-0008 patterns (no ``modes`` + no ``category``)
    are treated as ``[lane, request]`` so they keep surfacing during
    the catalog-reform transition. When ``workspace_id`` is set, the
    workspace-private catalog layer (``custom_patterns``) is merged
    on top — collisions resolve in favour of the workspace row.
    """
    if mode is not None and mode not in {"lane", "request"}:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_mode",
                "message": "mode must be one of 'lane', 'request', or omitted.",
            },
        )

    custom_rows: list[CustomPattern] = []
    if workspace_id is not None:
        await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
        custom_rows = list(
            (
                await session.execute(
                    select(CustomPattern)
                    .where(CustomPattern.workspace_id == workspace_id)
                    .order_by(asc(CustomPattern.pattern_id))
                )
            )
            .scalars()
            .all()
        )

    if mode is None:
        merged = catalog_service.list_patterns_for_workspace(custom_rows)
        entries = [
            p
            for p in merged
            # Hide ``common-*`` shared fragments (empty modes list) —
            # they're included by other patterns, never picked directly.
            if not (p.modes == [] and (p.category or "") == "common")
        ]
    else:
        entries = catalog_service.list_patterns_by_mode_for_workspace(
            mode, custom_rows
        )
    return _serialise(entries)


# ---------------------------------------------------------------------------
# Lane recipe catalog — drives the Lanes Library tab in the console
# ---------------------------------------------------------------------------
#
# The console needs a *lane-shaped* view of the built-in recipes it can
# add to ``.ship/config.yml`` (not the artifact-shaped view the other
# catalog endpoints return). We shape the response around the slots the
# Library row renders — trigger type, cron/glob, idempotency template —
# so the UI doesn't need to re-derive them from ``lane_trigger`` by
# hand. Resolver-only specs (``code_map``) are filtered out because they
# never land in config.yml by design; showing them as "available
# recipes" would mis-communicate the contract.

class LaneCatalogEntryOut(BaseModel):
    """Lane recipe as the console Library tab wants to render it."""

    kind: str
    title: str
    summary: str
    workflow_id: str
    default_enabled: bool
    # Exactly one of ``event`` / ``schedule`` is non-null; Phase 3 adds
    # ``once`` for resolver-triggered lanes.
    event: str | None
    pattern: str | None
    schedule: str | None
    idempotency_key: str | None
    # RFC-0008 C3.1 — canonical multi-pattern list. ``pattern`` above
    # is retained for single-pattern back-compat; ``patterns`` is always
    # populated so the Library UI can count patterns and decide whether
    # to surface the fan-out picker.
    patterns: list[str] = []
    fanout: str = "matrix"


class LaneCatalogResponse(BaseModel):
    entries: list[LaneCatalogEntryOut]


@router.get("/lanes", response_model=LaneCatalogResponse)
async def list_lane_catalog(
    _: AuthContext = Depends(get_current_auth),
) -> LaneCatalogResponse:
    """Built-in lane recipes the console Library tab can propose.

    Filters out resolver-only recipes (``trigger is None``) — they
    aren't user-installable as ``.ship/config.yml`` lanes. The wire
    format flattens ``trigger`` into explicit ``event`` / ``schedule``
    / ``pattern`` / ``idempotency_key`` slots so the UI doesn't have
    to guess which key is the discriminator, and surfaces the
    multi-pattern ``patterns`` list + ``fanout`` alongside so the
    Library card can render the fan-out picker.
    """
    entries: list[LaneCatalogEntryOut] = []
    for recipe in list_lane_recipes():
        trigger = recipe.trigger
        if trigger is None:
            continue
        patterns = list(recipe.patterns)
        entries.append(
            LaneCatalogEntryOut(
                kind=recipe.lane_id,
                title=recipe.name,
                summary=recipe.summary or recipe.name,
                workflow_id=recipe.workflow_id,
                default_enabled=recipe.default_enabled,
                event=trigger.get("event"),
                pattern=patterns[0] if len(patterns) == 1 else trigger.get("pattern"),
                schedule=trigger.get("schedule"),
                idempotency_key=trigger.get("idempotency_key"),
                patterns=patterns,
                fanout=recipe.fanout,
            )
        )
    return LaneCatalogResponse(entries=entries)
