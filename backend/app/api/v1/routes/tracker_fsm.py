"""Tracker FSM catalog surface (Wizard v2 iter 7).

The iter 5 seed PR drops ``.ship/tracker-fsm.md`` into every repo;
iter 7 adds a read-only console page that mirrors the same spec
back to humans without cloning the repo. This endpoint is the
source of truth for that page:

* ``GET /v1/workspaces/{ws}/tracker-fsm`` — returns the canonical
  ``SHIP_DEFAULT_STATES``, the per-tracker mapping hints, and
  (unless ``?repos=false``) one rendered markdown body per
  activated repo keyed on that repo's effective tracker binding
  (per-repo override → workspace default → ``none``). The
  rendered payload is the exact string the seed PR would
  (re)write today, so the console can show a "this is what lands
  in your repo when we seed" preview.

Read-only — no PUT / POST / DELETE. The source of truth is the
committed markdown file; if a team wants to customise the FSM
they edit the file in-repo and ``shipctl`` picks it up on the
next lane dispatch.

Scoped to any workspace member (``ROLES_READ``) because the FSM
is informational and knowing "Ship uses a state called `rework`"
doesn't leak anything a PR reviewer couldn't already see.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import ROLES_READ, _require_membership
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.tenancy import Integration
from backend.app.db.session import get_session
from backend.app.services.tracker_fsm import (
    FSM_INSTALL_PATH,
    SHIP_DEFAULT_STATES,
    TRACKER_MAPPING_HINTS,
    render_tracker_fsm,
)


router = APIRouter(prefix="/workspaces/{workspace_id}/tracker-fsm", tags=["tracker-fsm"])


# Ticket-router kinds that can hold a per-repo tracker binding. Keep in
# sync with ``tracker_binding.TRACKER_KINDS`` — duplicated here only so
# this file doesn't reach across into the binding module (circular-ish).
_TRACKER_KINDS: frozenset[str] = frozenset({"linear", "github", "jira"})


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class FsmStateOut(BaseModel):
    """One node in the canonical Ship FSM."""

    id: str
    label: str
    description: str
    # IDs of states this state can transition *to*. Success edges only;
    # rework loops back via ``rework → in_progress → in_review`` and is
    # described in the markdown body rather than exploded here.
    transitions: list[str] = Field(default_factory=list)


class RepoFsmOut(BaseModel):
    """Per-repo rendered FSM preview."""

    repo_id: uuid.UUID
    full_name: str
    # Tracker kind the FSM was rendered against. ``null`` when neither
    # the repo nor the workspace has a binding yet — UI should surface
    # the "bind a tracker" nudge instead of the mapping table.
    tracker_kind: str | None = None
    # Where the tracker kind came from. ``"repo"`` = per-repo row;
    # ``"workspace"`` = inherited default; ``"none"`` = nothing bound.
    source: str
    # Fully-rendered markdown body — the exact bytes the seed PR
    # would (re)write today. Not persisted anywhere on Ship; computed
    # per-request so it stays in sync with any canonical change.
    markdown: str


class TrackerFsmOut(BaseModel):
    """Top-level response for ``GET /v1/workspaces/{ws}/tracker-fsm``."""

    install_path: str = Field(
        default=FSM_INSTALL_PATH,
        description=(
            "Path inside the customer repo where the wizard writes the "
            "rendered markdown. Console surfaces it so operators know "
            "where to edit (the file on disk is still the source of "
            "truth for `shipctl`)."
        ),
    )
    states: list[FsmStateOut]
    # Native-status hints per tracker kind. Keyed by tracker slug
    # (``linear`` / ``github`` / ``jira``) → FSM state id → native
    # status name. Dict-of-dicts, not a list, so the UI can render
    # the table without a join step.
    mapping_hints: dict[str, dict[str, str]] = Field(default_factory=dict)
    # Workspace-level default tracker kind, if any. Resolved from the
    # ``integrations`` table (``repo_id IS NULL``) — same source the
    # tracker-binding GET falls back to.
    workspace_default_kind: str | None = None
    # Per-repo rendered previews. Empty when ``?repos=false`` or when
    # the workspace has no activated repos yet.
    repos: list[RepoFsmOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------


@router.get("", response_model=TrackerFsmOut)
async def get_tracker_fsm(
    workspace_id: uuid.UUID,
    include_repos: bool = Query(
        default=True,
        alias="repos",
        description=(
            "When true (default) the response carries one rendered "
            "markdown body per activated repo. Pass ``?repos=false`` "
            "from UIs that only need the canonical states + mapping "
            "hints (e.g. the settings page's top summary card)."
        ),
    ),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> TrackerFsmOut:
    """Return the canonical Ship FSM for ``workspace_id``."""

    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    # Canonical state list. ``transitions`` is a tuple in the service
    # layer — pydantic wants a list for the out schema.
    states = [
        FsmStateOut(
            id=s.id,
            label=s.label,
            description=s.description,
            transitions=list(s.transitions),
        )
        for s in SHIP_DEFAULT_STATES
    ]

    # Workspace default: first tracker-kind Integration whose
    # ``repo_id IS NULL`` (wizard v2 partial-unique index guarantees
    # uniqueness per workspace + kind in the global scope). We don't
    # pre-filter by kind because the UI wants to know "is *any*
    # tracker configured" even when it's something we wouldn't render
    # hints for — but only tracker kinds count as a "default" for FSM
    # rendering purposes.
    workspace_default_kind: str | None = None
    default_row = await session.scalar(
        select(Integration.kind)
        .where(
            Integration.workspace_id == workspace_id,
            Integration.repo_id.is_(None),
            Integration.kind.in_(list(_TRACKER_KINDS)),
        )
        .order_by(Integration.created_at.desc())
        .limit(1)
    )
    if default_row:
        workspace_default_kind = str(default_row)

    repos_out: list[RepoFsmOut] = []
    if include_repos:
        # Pull every activated repo with a single query, then the
        # per-repo tracker-binding rows in one more scan. O(2) DB
        # hits regardless of repo count.
        activated = (
            await session.scalars(
                select(WorkspaceRepo)
                .where(WorkspaceRepo.workspace_id == workspace_id)
                .order_by(WorkspaceRepo.created_at.asc())
            )
        ).all()

        if activated:
            repo_ids = [r.id for r in activated]
            binding_rows = (
                await session.scalars(
                    select(Integration).where(
                        Integration.workspace_id == workspace_id,
                        Integration.repo_id.in_(repo_ids),
                        Integration.kind.in_(list(_TRACKER_KINDS)),
                    )
                )
            ).all()
            bindings_by_repo: dict[uuid.UUID, Integration] = {
                b.repo_id: b for b in binding_rows if b.repo_id is not None
            }

            for repo in activated:
                per_repo = bindings_by_repo.get(repo.id)
                if per_repo is not None:
                    effective_kind = per_repo.kind
                    source = "repo"
                elif workspace_default_kind is not None:
                    effective_kind = workspace_default_kind
                    source = "workspace"
                else:
                    effective_kind = None
                    source = "none"

                markdown = render_tracker_fsm(
                    effective_kind,
                    workspace_default_kind=workspace_default_kind,
                    repo_full_name=repo.full_name,
                )
                repos_out.append(
                    RepoFsmOut(
                        repo_id=repo.id,
                        full_name=repo.full_name,
                        tracker_kind=effective_kind,
                        source=source,
                        markdown=markdown,
                    )
                )

    # ``mapping_hints`` is already a dict[str, dict[str, str]] in
    # the service layer; copy to keep the response immutable from the
    # caller's perspective.
    return TrackerFsmOut(
        states=states,
        mapping_hints={k: dict(v) for k, v in TRACKER_MAPPING_HINTS.items()},
        workspace_default_kind=workspace_default_kind,
        repos=repos_out,
    )
