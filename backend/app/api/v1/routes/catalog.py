"""Authenticated catalog surface — ``/v1/catalog/*``.

Exposes the artifact catalog (patterns, tools, workflows, collections)
through the v1 API so the operator console, CLI, and wizards can render
presets, show install-available workflows, and pin versions without
re-implementing the frontmatter parser on the client side.

The unauthenticated ``/patterns`` / ``/workflows`` / ``/collections``
endpoints on :mod:`backend.app.main` remain for public read-only
access (they're what `shipctl` talks to without a PAT). This router
layers the same data behind the workspace auth context so presets and
workflows can be gated by login when needed — and keeps the response
shape thin (summary only, no markdown body) because the console uses
it for picker UIs, not content rendering.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.services import catalog as catalog_service


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
    # Workflow-only fields (``None`` for other kinds).
    install_target: str | None = None
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


@router.get("/workflows", response_model=list[CatalogEntryOut])
async def list_workflows(
    _: AuthContext = Depends(get_current_auth),
) -> list[CatalogEntryOut]:
    """Workflow artifacts — drives the "available workflows" list on the dashboard."""
    return _serialise(catalog_service.list_workflows())


@router.get("/workflows/{workflow_id}", response_model=CatalogEntryOut)
async def get_workflow(
    workflow_id: str,
    _: AuthContext = Depends(get_current_auth),
) -> CatalogEntryOut:
    entry = catalog_service.get_workflow(workflow_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CatalogEntryOut(**entry.to_summary())


@router.get("/collections", response_model=list[CatalogEntryOut])
async def list_collections(
    _: AuthContext = Depends(get_current_auth),
) -> list[CatalogEntryOut]:
    """Every collection (presets, addendums, agent-rules) for tooling pickers."""
    return _serialise(catalog_service.list_collections())
