"""Global process-template surface.

What "process" means here: one Ship process is a bundle of agent roles
(intake, BA, tech-architect, qa-architect, developer, …) that together
make up the SDLC pipeline. The "default process" is the canonical
14-role bundle Ship ships out of the box; new workspaces install it
verbatim from the seed bundle.

The single endpoint:

  ``GET /v1/processes/default`` — list every role in the default
  bundle (in display order) with its short reason blurb. The console
  wizard reads this on "Confirm bootstrap" to show the operator what
  will be installed.

This replaces ``/v1/catalog/default-bundle`` (Phase 2.1 leftover named
back when Ship spoke "Plays + catalog" — there's no "catalog" any
more, and these aren't "plays").
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


router = APIRouter(prefix="/processes", tags=["processes"])


class DefaultProcessEntryOut(BaseModel):
    """One agent role in the default process bundle."""

    key: str
    title: str
    reason: str


class DefaultProcessResponse(BaseModel):
    bundle: list[DefaultProcessEntryOut]


@router.get("/default", response_model=DefaultProcessResponse)
async def get_default_process(
    _: AuthContext = Depends(get_current_auth),
) -> DefaultProcessResponse:
    """The canonical process Ship installs in every new repo.

    Returns one entry per slug in
    :data:`backend.app.services.lane_recipes.DEFAULT_BUNDLE`, in
    display order. ``title`` falls back to the slug when the agent-
    role registry has no matching default (so the UI never has to
    render an empty cell). ``reason`` is the one-line blurb from
    :data:`DEFAULT_BUNDLE_REASONS`.
    """
    by_slug = {d.slug: d for d in agent_roles_svc.list_defaults()}
    out: list[DefaultProcessEntryOut] = []
    for slug in DEFAULT_BUNDLE:
        default = by_slug.get(slug)
        title = (default.name if default and default.name else slug) or slug
        reason = DEFAULT_BUNDLE_REASONS.get(slug, "")
        out.append(
            DefaultProcessEntryOut(key=slug, title=title, reason=reason)
        )
    return DefaultProcessResponse(bundle=out)
