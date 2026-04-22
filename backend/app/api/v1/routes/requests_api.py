"""Ad-hoc agent runs ("Requests") — Phase 3 of RFC-0007 lanes/requests.

Two surfaces backing the Console's ``/requests`` page:

- ``POST /v1/workspaces/{ws}/repos/{repo_id}/requests`` — admin-only.
  Accepts a one-shot payload (``agent_slug``, optional ``context_ref``,
  ``prompt``), dispatches the ``adhoc-agent-run.yml`` GitHub Actions
  workflow on the repo's default branch, and inserts an
  :class:`AgentRequest` row as a dispatch receipt. Returns the row so
  the UI can redirect to the detail view.
- ``GET /v1/workspaces/{ws}/requests`` — workspace-wide list (newest
  first), optional ``?repo_id=`` filter.

There's no callback-accepting route here yet — the dispatched workflow
currently falls back to GitHub Actions as the run-log surface. When we
wire ``shipctl callback`` updates, the sink will live next to
``/pipeline-runs/{id}/callback`` and key off ``run_token_hash`` /
``gh_workflow_run_id``.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.pipelines import AgentRequest
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.services import catalog as catalog_service
from backend.app.services.catalog import CatalogArtifact


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/workspaces/{workspace_id}",
    tags=["requests"],
)


# Adhoc workflow filename — lives alongside the other starter YAMLs
# on the repo after the wizard seed lands. Kept in a constant so the
# test + dispatcher agree on the path (the starter_workflows catalog
# is the source of truth but requires a read; this is a cheap mirror).
_ADHOC_WORKFLOW_FILE = "adhoc-agent-run.yml"


# Allowed agent slugs — matches the ``catalog/agents`` surface. Kept
# tight on purpose: an unknown slug would dispatch a workflow that
# silently picks the wrong agent. Extending this is a product choice.
_ALLOWED_AGENT_SLUGS = {"claude", "gpt", "gemini", "custom"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class AgentRequestIn(BaseModel):
    """Body for ``POST /{repo_id}/requests``.

    Two supported shapes driving the Console's ``/requests`` page:

    * **Pattern-backed (RFC-0008 C4, preferred).** Set ``pattern_id``
      to a catalog entry advertising ``modes: request`` (e.g.
      ``role-ba``) and populate ``inputs`` with the key/value map the
      pattern's ``spec.inputs`` demands. ``agent_slug`` / ``prompt``
      become optional — when omitted we fall back to the pattern's
      own definition and Ship's default runner.
    * **Ad-hoc free-form (legacy).** Omit ``pattern_id`` and send
      ``agent_slug`` + ``prompt`` directly. Kept so the form can
      still dispatch a custom-prompt run when no cataloged pattern
      fits the task.

    ``context_ref`` is always optional — when set the workflow
    exposes it as ``inputs.context_ref`` for agent-side grounding.
    """

    pattern_id: str | None = Field(default=None, max_length=120)
    inputs: dict[str, Any] = Field(default_factory=dict)
    agent_slug: str | None = Field(default=None, min_length=1, max_length=64)
    prompt: str | None = Field(default=None, max_length=4096)
    context_ref: str | None = Field(default=None, max_length=1024)


class AgentRequestOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    repo_id: uuid.UUID
    repo_full_name: str
    agent_slug: str
    pattern_id: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    context_ref: str | None
    prompt: str
    status: str
    summary: str | None
    gh_workflow_run_id: int | None
    gh_html_url: str | None
    requested_by_email: str | None
    finished_at: str | None
    created_at: str


class AgentRequestListOut(BaseModel):
    requests: list[AgentRequestOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _load_repo(
    session: AsyncSession, workspace_id: uuid.UUID, repo_id: uuid.UUID
) -> tuple[WorkspaceRepo, GitHubInstallation]:
    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )
    if repo_row.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App isn't installed for this repo. "
                    "Reconnect it before dispatching requests."
                ),
            },
        )
    install_row = await session.get(GitHubInstallation, repo_row.installation_id)
    if install_row is None or install_row.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App installation is missing or "
                    "suspended. Reinstall the Ship app."
                ),
            },
        )
    return repo_row, install_row


def _serialize(row: AgentRequest, repo_full_name: str, requester_email: str | None) -> AgentRequestOut:
    return AgentRequestOut(
        id=row.id,
        workspace_id=row.workspace_id,
        repo_id=row.repo_id,
        repo_full_name=repo_full_name,
        agent_slug=row.agent_slug,
        pattern_id=row.pattern_id,
        inputs=dict(row.inputs or {}),
        context_ref=row.context_ref,
        prompt=row.prompt,
        status=row.status,
        summary=row.summary,
        gh_workflow_run_id=row.gh_workflow_run_id,
        gh_html_url=row.gh_html_url,
        requested_by_email=requester_email,
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


# ---------------------------------------------------------------------------
# Pattern resolution + input validation (RFC-0008 C4)
# ---------------------------------------------------------------------------


class _ResolvedRequest:
    """What the dispatcher needs after ``pattern_id``/``inputs`` normalisation.

    Consolidates the two input shapes (pattern-backed vs ad-hoc) into
    the legacy ``{agent_slug, prompt, context_ref, inputs, pattern_id}``
    tuple the downstream dispatch path already understood.
    """

    __slots__ = ("pattern_id", "pattern", "agent_slug", "prompt", "context_ref", "inputs")

    def __init__(
        self,
        *,
        pattern_id: str | None,
        pattern: CatalogArtifact | None,
        agent_slug: str,
        prompt: str,
        context_ref: str | None,
        inputs: dict[str, Any],
    ) -> None:
        self.pattern_id = pattern_id
        self.pattern = pattern
        self.agent_slug = agent_slug
        self.prompt = prompt
        self.context_ref = context_ref
        self.inputs = inputs


def _coerce_input_value(spec: dict[str, Any], raw: Any) -> str:
    """Convert a submitted input value into a workflow-safe string.

    GitHub Actions ``workflow_dispatch`` inputs are strings; we
    normalise booleans / numbers / lists client-side into a stable
    representation the pattern's prompt template can consume. Enum
    values are validated against ``spec.values`` so a stale UI can't
    dispatch a mistyped choice.
    """
    if raw is None:
        return ""
    kind = str(spec.get("type") or "text").strip().lower()
    if kind == "enum":
        allowed = spec.get("values") or []
        if not isinstance(allowed, list) or not allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_pattern",
                    "message": (
                        f"Pattern input {spec.get('name')!r} declares "
                        "type=enum without any values."
                    ),
                },
            )
        value = str(raw)
        if value not in {str(v) for v in allowed}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "invalid_input_value",
                    "message": (
                        f"{spec.get('name')!r} must be one of "
                        f"{list(allowed)}; got {value!r}."
                    ),
                },
            )
        return value
    if isinstance(raw, bool):
        return "true" if raw else "false"
    return str(raw)


def _resolve_pattern_request(payload: AgentRequestIn) -> _ResolvedRequest:
    """Normalise both request shapes into a single dispatch-ready tuple.

    * When ``pattern_id`` is set we look up the catalog entry, ensure
      it advertises ``modes: request``, fill in missing required
      inputs, coerce enum values, and derive a default prompt +
      agent_slug from pattern metadata if the caller didn't override.
    * When ``pattern_id`` is absent we fall back to the legacy ad-hoc
      shape and require ``agent_slug`` + ``prompt``.
    """
    if payload.pattern_id:
        pattern = next(
            (p for p in catalog_service.list_patterns() if p.id == payload.pattern_id),
            None,
        )
        if pattern is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "pattern_not_found",
                    "message": f"No catalog pattern with id={payload.pattern_id!r}.",
                },
            )
        if "request" not in pattern.modes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "pattern_not_request_mode",
                    "message": (
                        f"Pattern {pattern.id!r} can't be dispatched as a "
                        "one-shot request (missing 'request' in spec.modes)."
                    ),
                },
            )
        raw_inputs = payload.inputs or {}
        normalised: dict[str, str] = {}
        missing: list[str] = []
        for input_spec in pattern.inputs:
            name = input_spec.get("name")
            if not isinstance(name, str):
                continue
            if name in raw_inputs and raw_inputs[name] not in (None, ""):
                normalised[name] = _coerce_input_value(input_spec, raw_inputs[name])
                continue
            default = input_spec.get("default")
            if default not in (None, ""):
                normalised[name] = _coerce_input_value(input_spec, default)
                continue
            if input_spec.get("required"):
                missing.append(name)
        if missing:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "code": "missing_required_inputs",
                    "message": (
                        f"Pattern {pattern.id!r} is missing required "
                        f"input(s): {missing}."
                    ),
                    "missing": missing,
                },
            )
        # Pattern-derived defaults for the ad-hoc columns. A cataloged
        # pattern doesn't need a free-form prompt (``shipctl run`` will
        # render the pattern body), but we still persist a human-
        # readable fallback so the Console's list view has something to
        # show when the caller didn't supply one.
        agent_slug = (payload.agent_slug or "custom").strip() or "custom"
        prompt = (payload.prompt or pattern.description or pattern.id)[:4096]
        return _ResolvedRequest(
            pattern_id=pattern.id,
            pattern=pattern,
            agent_slug=agent_slug,
            prompt=prompt,
            context_ref=payload.context_ref or None,
            inputs=normalised,
        )

    # Legacy ad-hoc shape — ``agent_slug`` + ``prompt`` required.
    agent_slug = (payload.agent_slug or "").strip()
    prompt = (payload.prompt or "").strip()
    if not agent_slug or not prompt:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "missing_required_fields",
                "message": (
                    "Provide either a ``pattern_id`` (preferred) or both "
                    "``agent_slug`` and ``prompt`` for an ad-hoc request."
                ),
            },
        )
    return _ResolvedRequest(
        pattern_id=None,
        pattern=None,
        agent_slug=agent_slug,
        prompt=prompt,
        context_ref=payload.context_ref or None,
        inputs={},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/repos/{repo_id}/requests",
    response_model=AgentRequestOut,
    status_code=status.HTTP_201_CREATED,
)
async def dispatch_request(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    payload: AgentRequestIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AgentRequestOut:
    """Dispatch one ad-hoc agent run.

    Admin-only. Inserts :class:`AgentRequest` *before* the dispatch
    so a failed GitHub call leaves a visible "failed to dispatch" row
    in the Console rather than a silent loss.
    """
    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        dispatch_workflow,
    )

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    resolved = _resolve_pattern_request(payload)

    if resolved.agent_slug not in _ALLOWED_AGENT_SLUGS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "unknown_agent",
                "message": (
                    f"agent_slug must be one of {sorted(_ALLOWED_AGENT_SLUGS)}"
                ),
            },
        )

    repo_row, install_row = await _load_repo(session, workspace_id, repo_id)

    row = AgentRequest(
        workspace_id=workspace_id,
        repo_id=repo_id,
        requested_by_user_id=auth.user.id,
        agent_slug=resolved.agent_slug,
        pattern_id=resolved.pattern_id,
        inputs=dict(resolved.inputs),
        context_ref=resolved.context_ref,
        prompt=resolved.prompt,
        status="dispatching",
    )
    session.add(row)
    await session.flush()

    inputs: dict[str, str] = {
        "agent": resolved.agent_slug,
        "prompt": resolved.prompt,
        "context_ref": resolved.context_ref or "",
        # Keep the callback channel pre-wired so when the callback
        # sink ships, existing rows start reporting retroactively.
        "ship_run_id": str(row.id),
        # The callback sink isn't wired yet — see module docstring.
        # Pass an empty URL so the workflow skips the report step
        # rather than failing loudly.
        "ship_callback_url": "",
        "ship_run_token": "",
    }
    # RFC-0008 C4 — pattern-backed dispatches also forward the pattern
    # id + structured inputs so ``adhoc-agent-run.yml`` can render the
    # cataloged template via ``shipctl run --pattern <id>`` instead of
    # the free-form prompt path. The workflow ignores ``pattern_id``
    # when empty so legacy dispatches stay source-compatible.
    if resolved.pattern_id:
        inputs["pattern_id"] = resolved.pattern_id
        inputs["pattern_inputs_json"] = json.dumps(resolved.inputs, sort_keys=True)

    try:
        await dispatch_workflow(
            repo_row,
            install_row,
            _ADHOC_WORKFLOW_FILE,
            inputs=inputs,
            settings=settings,
        )
    except WorkflowDispatchError as exc:
        row.status = "dispatch_failed"
        row.summary = (exc.message or "GitHub rejected workflow_dispatch")[:512]
        await session.flush()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "dispatch_failed",
                "upstream_status": exc.status_code,
                "message": row.summary,
            },
        ) from exc
    except httpx.HTTPStatusError as exc:
        row.status = "dispatch_failed"
        row.summary = (
            f"GitHub HTTP {exc.response.status_code} on "
            f"workflow_dispatch({_ADHOC_WORKFLOW_FILE})"
        )[:512]
        await session.flush()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "dispatch_failed",
                "upstream_status": exc.response.status_code,
                "message": row.summary,
            },
        ) from exc

    row.status = "dispatched"
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="request.dispatch",
            target_kind="agent_request",
            target_id=str(row.id),
            payload={
                "repo_id": str(repo_id),
                "repo_full_name": repo_row.full_name,
                "agent_slug": resolved.agent_slug,
                "pattern_id": resolved.pattern_id,
                "workflow_file": _ADHOC_WORKFLOW_FILE,
            },
        )
    )
    await session.flush()

    return _serialize(row, repo_row.full_name, auth.user.email)


@router.get("/requests", response_model=AgentRequestListOut)
async def list_requests(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AgentRequestListOut:
    """List recent ad-hoc requests (newest first)."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)

    stmt = (
        select(AgentRequest, WorkspaceRepo.full_name)
        .join(WorkspaceRepo, AgentRequest.repo_id == WorkspaceRepo.id)
        .where(AgentRequest.workspace_id == workspace_id)
        .order_by(desc(AgentRequest.created_at))
        .limit(limit)
    )
    if repo_id is not None:
        stmt = stmt.where(AgentRequest.repo_id == repo_id)

    rows = (await session.execute(stmt)).all()

    out: list[AgentRequestOut] = []
    for row, repo_full_name in rows:
        # Requester email is looked up lazily per row; the dashboard
        # only shows the list tip so N<=200 queries is acceptable.
        requester_email: str | None = None
        if row.requested_by_user_id is not None:
            from backend.app.db.models.tenancy import User

            user = await session.get(User, row.requested_by_user_id)
            requester_email = user.email if user else None
        out.append(_serialize(row, repo_full_name, requester_email))
    return AgentRequestListOut(requests=out)


@router.get(
    "/requests/{request_id}",
    response_model=AgentRequestOut,
)
async def get_request(
    workspace_id: uuid.UUID,
    request_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> AgentRequestOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    row = (
        await session.execute(
            select(AgentRequest).where(
                AgentRequest.id == request_id,
                AgentRequest.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Request not found.",
        )
    repo_row = await session.get(WorkspaceRepo, row.repo_id)
    repo_full_name = repo_row.full_name if repo_row else ""
    requester_email: str | None = None
    if row.requested_by_user_id is not None:
        from backend.app.db.models.tenancy import User

        user = await session.get(User, row.requested_by_user_id)
        requester_email = user.email if user else None
    return _serialize(row, repo_full_name, requester_email)


__all__ = ["router"]
