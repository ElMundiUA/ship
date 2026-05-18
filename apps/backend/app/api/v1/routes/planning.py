"""Mass-planning intake endpoints (ELS-170 / M3).

CRUD over :class:`PlanningProposal` rows — the drafts Navigator emits
after running the requirements extractor (M1) on a PDF. Operator
edits the proposal in the Console preview pane; M2's
``/planning/mass-import`` endpoint reads the draft and writes
Linear.

Routes
======
- ``POST /v1/workspaces/{ws}/planning/proposals`` — create draft
- ``GET  /v1/workspaces/{ws}/planning/proposals/{id}`` — read
- ``PATCH /v1/workspaces/{ws}/planning/proposals/{id}`` — edit
- ``DELETE /v1/workspaces/{ws}/planning/proposals/{id}`` — discard

All routes are workspace-scoped + membership-gated.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_READ,
    _require_membership,
)
from backend.app.db.models.planning_proposals import PlanningProposal
from backend.app.db.session import get_session
from backend.app.services.planning.requirements_extraction import (
    MassPlanProposal,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/workspaces/{workspace_id}/planning", tags=["planning"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlanningProposalIn(BaseModel):
    """Create payload. ``source_kind`` defaults to ``pdf`` (Navigator
    intake); future surfaces (manual UI seed, Confluence import) set
    their own."""

    source_kind: str = Field(default="pdf", max_length=32)
    source_ref: str | None = Field(default=None, max_length=2_000)
    thread_id: uuid.UUID | None = None
    payload: dict[str, Any]


class PlanningProposalPatch(BaseModel):
    """Operator edits. Either supply a fully-replacement ``payload``
    (when the Console serialises the whole tree on save), or
    individual field replacements — ``payload`` wins when present.
    """

    payload: dict[str, Any] | None = None


class PlanningProposalOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    thread_id: uuid.UUID | None
    source_kind: str
    source_ref: str | None
    payload: dict[str, Any]
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    committed_at: datetime | None
    committed_ticket_refs: list[str] | None


def _to_out(row: PlanningProposal) -> PlanningProposalOut:
    return PlanningProposalOut(
        id=row.id,
        workspace_id=row.workspace_id,
        thread_id=row.thread_id,
        source_kind=row.source_kind,
        source_ref=row.source_ref,
        payload=row.payload,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        committed_at=row.committed_at,
        committed_ticket_refs=row.committed_ticket_refs,
    )


def _validate_payload_shape(payload: dict[str, Any]) -> None:
    """Run the MassPlanProposal schema validators (cycle, unknown deps,
    key shape, size). Raises 422 on first violation."""
    try:
        MassPlanProposal.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_proposal_payload",
                "errors": exc.errors()[:10],
            },
        ) from exc


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------


@router.post(
    "/proposals",
    response_model=PlanningProposalOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_proposal(
    workspace_id: uuid.UUID,
    body: PlanningProposalIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PlanningProposalOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    _validate_payload_shape(body.payload)

    row = PlanningProposal(
        workspace_id=workspace_id,
        thread_id=body.thread_id,
        source_kind=body.source_kind,
        source_ref=body.source_ref,
        payload=body.payload,
        created_by=auth.user.id,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return _to_out(row)


# ---------------------------------------------------------------------------
# READ
# ---------------------------------------------------------------------------


async def _load_or_404(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
) -> PlanningProposal:
    row = (
        await session.execute(
            select(PlanningProposal).where(
                PlanningProposal.id == proposal_id,
                PlanningProposal.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "proposal_not_found"},
        )
    return row


@router.get("/proposals/{proposal_id}", response_model=PlanningProposalOut)
async def get_proposal(
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PlanningProposalOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    return _to_out(await _load_or_404(session, workspace_id, proposal_id))


# ---------------------------------------------------------------------------
# UPDATE
# ---------------------------------------------------------------------------


@router.patch(
    "/proposals/{proposal_id}", response_model=PlanningProposalOut
)
async def patch_proposal(
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    body: PlanningProposalPatch,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> PlanningProposalOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    row = await _load_or_404(session, workspace_id, proposal_id)
    if row.committed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "proposal_already_committed",
                "committed_at": row.committed_at.isoformat(),
            },
        )
    if body.payload is None:
        # Nothing to change — return current row. Operator may have hit
        # save with no edits; treat as idempotent.
        return _to_out(row)
    _validate_payload_shape(body.payload)
    row.payload = body.payload
    row.updated_at = datetime.now(timezone.utc)
    await session.flush()
    await session.refresh(row)
    return _to_out(row)


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


@router.delete(
    "/proposals/{proposal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_proposal(
    workspace_id: uuid.UUID,
    proposal_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    row = await _load_or_404(session, workspace_id, proposal_id)
    if row.committed_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "proposal_already_committed",
                "committed_at": row.committed_at.isoformat(),
            },
        )
    await session.delete(row)
    await session.flush()


# ---------------------------------------------------------------------------
# COMMIT — ELS-169 / M2
# ---------------------------------------------------------------------------


class MassImportIn(BaseModel):
    """Either reference a stored proposal_id, or inline a fresh
    payload. ``proposal_id`` wins when both are present.
    """

    proposal_id: uuid.UUID | None = None
    proposal: dict[str, Any] | None = None


class MassImportAnchorOut(BaseModel):
    key: str
    ticket_ref: str
    url: str | None = None


class MassImportOut(BaseModel):
    proposal_id: uuid.UUID
    project_id: str
    project_url: str | None
    anchors: list[MassImportAnchorOut]


@router.post(
    "/mass-import",
    response_model=MassImportOut,
)
async def mass_import_commit(
    workspace_id: uuid.UUID,
    body: MassImportIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> MassImportOut:
    """Materialise a mass-planning proposal into Linear: project
    (state=planning / Drafts), N anchor tickets with
    ``planning:anchor`` label, ``blocks`` relations per
    ``depends_on``. The project lands in the workspace's project
    priority list as ``planning`` (Drafts) so agents don't auto-
    pick the anchors — operator promotes via the dashboard.

    Idempotent: re-posting against a ``committed_at``-stamped
    proposal returns the existing committed state with 200.
    """
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    # Resolve the source proposal — either a stored draft or an
    # inline payload (the latter creates an ephemeral row for audit).
    if body.proposal_id is not None:
        row = await _load_or_404(session, workspace_id, body.proposal_id)
    elif body.proposal is not None:
        _validate_payload_shape(body.proposal)
        row = PlanningProposal(
            workspace_id=workspace_id,
            source_kind="inline",
            payload=body.proposal,
            created_by=auth.user.id,
        )
        session.add(row)
        await session.flush()
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "mass_import_empty",
                "message": "Provide proposal_id or inline proposal.",
            },
        )

    # Idempotent replay: already committed → return cached refs.
    if row.committed_at is not None:
        return MassImportOut(
            proposal_id=row.id,
            project_id=str(
                (row.payload or {}).get("_committed_project_id") or ""
            ),
            project_url=(row.payload or {}).get("_committed_project_url"),
            anchors=[
                MassImportAnchorOut(key=k, ticket_ref=r)
                for k, r in zip(
                    (
                        (row.payload or {})
                        .get("_committed_anchor_keys", [])
                    ),
                    row.committed_ticket_refs or [],
                )
            ],
        )

    # Re-validate fresh in case the draft was hand-edited weird.
    _validate_payload_shape(row.payload)
    proposal = MassPlanProposal.model_validate(row.payload)

    # Resolve the Linear tracker for this workspace.
    from backend.app.core.config import get_settings as _gs
    from backend.app.integrations.gateway.tracker import TicketRef as _TR
    from backend.app.services.tracker_resolver import (
        resolve_for_workspace as _resolve,
    )

    resolved_tracker = await _resolve(
        session=session, settings=_gs(), workspace_id=workspace_id
    )
    if resolved_tracker is None or resolved_tracker.kind != "linear":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "tracker_not_linear",
                "message": (
                    "Mass-planning intake currently requires a Linear "
                    "tracker bound to the workspace."
                ),
            },
        )
    gw = resolved_tracker.gateway

    # 1. Create the Linear project. We invent a short description
    #    from the proposal's project section.
    team_id = await gw._resolve_team_id(None)  # noqa: SLF001 — internal helper
    proj_resp = await gw._gql(  # noqa: SLF001
        """
        mutation ShipMassImportProject($input: ProjectCreateInput!) {
          projectCreate(input: $input) {
            success
            project { id name url }
          }
        }
        """,
        {
            "input": {
                "name": proposal.project.name[:160],
                "description": (proposal.project.description or "")[:255],
                "content": proposal.project.description,
                "teamIds": [team_id],
            }
        },
    )
    proj = (proj_resp.get("projectCreate") or {}).get("project") or {}
    project_id = str(proj.get("id") or "")
    project_url = proj.get("url")
    if not project_id:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "linear_project_create_failed"},
        )

    # 2. Create one anchor ticket per epic. Topo order isn't required
    #    by Linear (relations are wired in step 3), but it makes the
    #    Linear project tab read sensibly top-to-bottom.
    key_to_issue_id: dict[str, str] = {}
    anchors_out: list[MassImportAnchorOut] = []
    for epic in _topo_sorted(proposal.epics):
        created = await gw.create_ticket(
            title=epic.title,
            body=epic.brief,
            labels=["planning:anchor"],
            project_id=project_id,
        )
        # ``CreatedTicket`` carries the Linear UUID on .ref.id and
        # the display id (e.g. "ELS-200") on .display_id.
        issue_id = str(created.ref.id)
        key_to_issue_id[epic.key] = issue_id
        anchors_out.append(
            MassImportAnchorOut(
                key=epic.key,
                ticket_ref=created.display_id,
                url=created.url,
            )
        )

    # 3. Wire blocks relations. ``epic.depends_on`` lists blockers
    #    by key — translate to (blocker_id, blocked_id) and call
    #    relate_tickets.
    for epic in proposal.epics:
        blocked_id = key_to_issue_id[epic.key]
        for blocker_key in epic.depends_on:
            blocker_id = key_to_issue_id[blocker_key]
            await gw.relate_tickets(
                blocker=_TR(kind="linear", workspace_hint=None, id=blocker_id),
                blocked=_TR(kind="linear", workspace_hint=None, id=blocked_id),
                kind="blocks",
            )

    # 4. Drop the new project into the workspace priority table as
    #    ``planning`` (Drafts) so the picker doesn't auto-pick the
    #    anchors. Operator promotes via dashboard.
    from sqlalchemy import text as _text

    max_ord_row = await session.execute(
        _text(
            "SELECT COALESCE(MAX(ordinal), 0) FROM workspace_project_priorities "
            "WHERE workspace_id = :ws"
        ),
        {"ws": workspace_id},
    )
    next_ord = int((max_ord_row.scalar() or 0)) + 1
    await session.execute(
        _text(
            """
            INSERT INTO workspace_project_priorities
                (workspace_id, project_native_id, ordinal, state)
            VALUES (:ws, :pid, :ord, 'planning')
            ON CONFLICT (workspace_id, project_native_id)
            DO UPDATE SET state = 'planning'
            """
        ),
        {"ws": workspace_id, "pid": project_id, "ord": next_ord},
    )

    # 5. Stamp the proposal row.
    now = datetime.now(timezone.utc)
    row.committed_at = now
    row.committed_ticket_refs = [a.ticket_ref for a in anchors_out]
    cached = dict(row.payload or {})
    cached["_committed_project_id"] = project_id
    cached["_committed_project_url"] = project_url
    cached["_committed_anchor_keys"] = [a.key for a in anchors_out]
    row.payload = cached
    row.updated_at = now
    await session.flush()

    # 6. Audit.
    from backend.app.db.models.tenancy import AuditLog as _AL

    session.add(
        _AL(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="planning.mass_import.committed",
            target_kind="planning_proposal",
            target_id=str(row.id),
            payload={
                "project_id": project_id,
                "anchor_count": len(anchors_out),
                "dep_count": sum(len(e.depends_on) for e in proposal.epics),
            },
        )
    )
    await session.flush()

    return MassImportOut(
        proposal_id=row.id,
        project_id=project_id,
        project_url=project_url,
        anchors=anchors_out,
    )


def _topo_sorted(epics):  # type: ignore[no-untyped-def]
    """Return epics ordered so each appears after its depends_on. The
    cycle check already passed in :class:`MassPlanProposal` validation,
    so Kahn's here is a one-pass walk."""
    indeg = {e.key: len(e.depends_on) for e in epics}
    by_key = {e.key: e for e in epics}
    ready = [k for k, n in indeg.items() if n == 0]
    out = []
    while ready:
        k = ready.pop(0)
        out.append(by_key[k])
        for e in epics:
            if k in e.depends_on:
                indeg[e.key] -= 1
                if indeg[e.key] == 0:
                    ready.append(e.key)
    return out


__all__ = ["router"]
