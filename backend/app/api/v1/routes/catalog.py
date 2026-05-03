"""Authenticated catalog surface — slimmed to the wizard's bundle preview.

Phase 2.1 cleanup retired the ``/v1/catalog/{presets,collections,patterns,lanes}``
endpoints together with the ``custom_patterns`` AI-author flow and the
public catalog browser in the console. Phase 2.4 Step D retired the
underlying pattern catalog. The only remaining surface is
``/v1/catalog/default-bundle``, which the wizard's "Confirm bootstrap"
step calls to render a one-line preview of the Plays the seed PR will
install.

Names are pulled from the agent-role registry
(:mod:`backend.app.services.agent_roles`); reasons live in
:data:`backend.app.services.lane_recipes.DEFAULT_BUNDLE_REASONS`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.services import agent_roles as agent_roles_svc
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

    Returns one entry per slug in
    :data:`backend.app.services.lane_recipes.DEFAULT_BUNDLE`, in the
    bundle's display order. ``title`` falls back to the slug when the
    agent-role registry has no matching default so the UI never has
    to render an empty cell. ``reason`` is the short blurb from
    :data:`DEFAULT_BUNDLE_REASONS` (the wizard's source of truth).
    """
    by_slug = {d.slug: d for d in agent_roles_svc.list_defaults()}
    out: list[DefaultBundleEntryOut] = []
    for slug in DEFAULT_BUNDLE:
        default = by_slug.get(slug)
        title = (default.name if default and default.name else slug) or slug
        reason = DEFAULT_BUNDLE_REASONS.get(slug, "")
        out.append(DefaultBundleEntryOut(key=slug, title=title, reason=reason))
    return DefaultBundleResponse(bundle=out)
