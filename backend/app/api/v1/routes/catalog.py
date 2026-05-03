"""Authenticated catalog surface — slimmed to the wizard's bundle preview.

Phase 2.1 cleanup retired the ``/v1/catalog/{presets,collections,patterns,lanes}``
endpoints together with the ``custom_patterns`` AI-author flow and the
public catalog browser in the console. The only remaining surface is
``/v1/catalog/default-bundle``, which the wizard's "Confirm bootstrap"
step calls to render a one-line preview of the Plays the seed PR will
install.

The unauthenticated ``/patterns`` / ``/fetch`` endpoints on
:mod:`backend.app.main` keep working: ``shipctl run`` resolves agent-role
prompt bodies through them at routine fire time. Phase 2.4 renames that
surface to ``specialists`` and drops the ``catalog`` vocabulary entirely.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.services import catalog as catalog_service
from backend.app.services.lane_recipes import (
    DEFAULT_BUNDLE,
    DEFAULT_BUNDLE_REASONS,
)


router = APIRouter(prefix="/catalog", tags=["catalog"])


class DefaultBundleEntryOut(BaseModel):
    """One Play in the canonical install bundle."""

    key: str
    title: str
    reason: str


class DefaultBundleResponse(BaseModel):
    bundle: list[DefaultBundleEntryOut]


@router.get("/default-bundle", response_model=DefaultBundleResponse)
async def get_default_bundle(
    _: AuthContext = Depends(get_current_auth),
) -> DefaultBundleResponse:
    """Canonical Plays bundle installed in every new repo (Wave-8c).

    Returns one entry per pattern in
    :data:`backend.app.services.lane_recipes.DEFAULT_BUNDLE`, in the
    bundle's display order. ``title`` falls back to the pattern key
    when the catalog row is missing or has no ``name`` set so the UI
    never has to render an empty cell. ``reason`` is the short blurb
    from :data:`DEFAULT_BUNDLE_REASONS` (the wizard's source of truth).
    """
    by_id = {entry.id: entry for entry in catalog_service.list_patterns()}
    out: list[DefaultBundleEntryOut] = []
    for key in DEFAULT_BUNDLE:
        entry = by_id.get(key)
        title = (entry.name if entry and entry.name else key) or key
        reason = DEFAULT_BUNDLE_REASONS.get(key, "")
        out.append(DefaultBundleEntryOut(key=key, title=title, reason=reason))
    return DefaultBundleResponse(bundle=out)
