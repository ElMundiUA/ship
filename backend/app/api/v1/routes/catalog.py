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

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.services import catalog as catalog_service
from backend.app.services.default_pipelines import DEFAULT_PIPELINES


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
    content_sha256: str | None
    updated_at: Any | None = None
    deprecated: bool
    replaced_by: str | None
    yanked: bool
    # Preset-only field (``None`` unless ``spec.preset_id`` is set).
    preset_id: str | None = None


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

# Human-readable summaries for each built-in kind. Lives here rather
# than on ``DefaultPipelineSpec`` because that struct is currently the
# machine contract for the seeder; the summary is purely presentational
# copy that belongs with the surface that renders it.
_LANE_SUMMARIES: dict[str, str] = {
    "pr_review": (
        "Reviews every pull request against your gates (lint, tests, "
        "security, architecture). Posts findings as PR comments."
    ),
    "daily_standup": (
        "Weekday digest of open PRs, failing checks and FSM "
        "transitions. Lands in your tracker or Slack."
    ),
    "tech_debt": (
        "Weekly parallel audit: security, perf, type coverage, dead "
        "code. Files the findings as tracker tickets."
    ),
    "self_heal": (
        "Nightly sweep of Ship-owned workflows — re-runs flaky CI, "
        "opens a PR when a starter template drifts."
    ),
}


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


class LaneCatalogResponse(BaseModel):
    entries: list[LaneCatalogEntryOut]


@router.get("/lanes", response_model=LaneCatalogResponse)
async def list_lane_catalog(
    _: AuthContext = Depends(get_current_auth),
) -> LaneCatalogResponse:
    """Built-in lane recipes the console Library tab can propose.

    Filters out resolver-only specs (``lane_trigger is None``) — they
    aren't user-installable as ``.ship/config.yml`` lanes. The wire
    format flattens ``lane_trigger`` into explicit ``event`` /
    ``schedule`` / ``pattern`` / ``idempotency_key`` slots so the UI
    doesn't have to guess which key is the discriminator.
    """
    entries: list[LaneCatalogEntryOut] = []
    for spec in DEFAULT_PIPELINES:
        if spec.lane_trigger is None:
            continue
        trigger = spec.lane_trigger
        entries.append(
            LaneCatalogEntryOut(
                kind=spec.kind,
                title=spec.name,
                summary=_LANE_SUMMARIES.get(spec.kind, spec.name),
                workflow_id=spec.workflow_id,
                default_enabled=spec.enabled,
                event=trigger.get("event"),
                pattern=trigger.get("pattern"),
                schedule=trigger.get("schedule"),
                idempotency_key=trigger.get("idempotency_key"),
            )
        )
    return LaneCatalogResponse(entries=entries)
