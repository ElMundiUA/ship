"""Process API — read-only FSM projection over the current runtime.

Phase 1 of the orchestration UI introduces Ship-native process language
without changing the executor yet. The data below is a compatibility
projection: current lanes / pipelines / runs / inbox items are shaped as a
single primary ``development`` process so the Console can stop presenting
plays, automations, and runs as the operator's main model.
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_MAINTAIN,
    ROLES_READ,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_surface import Clarification
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.lanes import Routine
from backend.app.db.models.lanes import RoutineRun
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.services.agent.tools import ToolBox, ToolInvocationError
from backend.app.services.inbox.dual_write import mirror_clarification_create
from backend.app.services.role_templates import default_role_templates


router = APIRouter(
    prefix="/workspaces/{workspace_id}/processes",
    tags=["processes"],
)

Health = Literal["ok", "degraded", "failed"]
TaskStatus = Literal["active", "blocked", "done"]
ProcessLinkType = Literal["handoff", "dependency", "approval", "notification"]
ProcessNodeType = Literal["workspace", "process", "subprocess", "routine", "approval"]

# The seven canonical lifecycle states a stage can sit in. Every
# stage in any process maps to exactly one of these; they are the
# tracker-agnostic projection axis that adapters bind to native columns.
#
#   backlog          ticket exists, no agent picks it up
#   planning         ticket is being scoped/designed (intake/BA/architects)
#   executing        ticket is being built (dev/QA-manual/QA-auto)
#   reviewing        work submitted, awaiting approval
#   awaiting_input   frozen, waiting on a human answer (overlay via label)
#   blocked          frozen, external blocker (overlay via label)
#   closed           terminal
#
# Adding states here breaks tracker projection contracts — extend by
# adding a new stage to a process and mapping it to one of the seven
# instead.
CanonicalState = Literal[
    "backlog",
    "planning",
    "executing",
    "reviewing",
    "awaiting_input",
    "blocked",
    "closed",
]

CANONICAL_STATES: tuple[str, ...] = (
    "backlog",
    "planning",
    "executing",
    "reviewing",
    "awaiting_input",
    "blocked",
    "closed",
)

# Legacy stage id → canonical state. Used when projecting a stage
# from an older config (or our own current default) that doesn't yet
# carry the explicit ``state`` field. Tables in ``.ship/config.yml``
# stay readable; we just impute the bucket.
_LEGACY_STAGE_TO_STATE: dict[str, CanonicalState] = {
    # E16/ELS-123 — current bundle stages.
    "planning": "planning",
    "dev_implementation": "executing",
    "validation": "executing",
    "code_review": "reviewing",
    # Pre-E16 SDLC stages (intake / tech_arch / qa_arch / qa_manual /
    # qa_automation) were absorbed into the ``planning`` and
    # ``validation`` bundles. Kept here as aliases so any in-flight
    # ticket carrying the old stage label still buckets into a
    # canonical state until ELS-124 cuts the legacy labels.
    "task_intake": "planning",
    "bug_triage": "planning",
    "ba_requirements": "planning",
    "tech_arch_plan": "planning",
    "qa_arch_plan": "planning",
    "qa_manual": "executing",
    "qa_automation": "executing",
    # ``pr_review`` was the legacy 5-state name for the final review
    # stage; ``code_review`` is the canonical id. Both map to
    # ``reviewing``.
    "pr_review": "reviewing",
    # Decomposition (E16/ELS-123): one bundle stage replaces the
    # four-step wbs → architecture → test_architecture → tasks chain.
    # Legacy names kept for in-flight anchors.
    "decomposition": "planning",
    "wbs": "planning",
    "architecture": "planning",
    "test_architecture": "planning",
    "tasks": "executing",
    "planning_done": "reviewing",
}

PRIMARY_PROCESS_ID = "development"
DECOMPOSITION_PROCESS_ID = "decomposition"

_SEEDED_PROCESSES: tuple[dict[str, Any], ...] = (
    {
        "id": "development",
        "name": "Development",
        "description": "Ticket-driven SDLC flow from intake through implementation and review.",
        "parent_process_id": None,
        "node_type": "process",
        "template_id": "process-development",
    },
    {
        "id": "development.requirements",
        "name": "Requirements",
        "description": "Clarify scope, acceptance criteria, and handoff notes.",
        "parent_process_id": "development",
        "node_type": "subprocess",
        "template_id": "subprocess-requirements",
    },
    {
        "id": "development.implementation",
        "name": "Implementation",
        "description": "Plan, implement, test, and prepare code changes.",
        "parent_process_id": "development",
        "node_type": "subprocess",
        "template_id": "subprocess-implementation",
    },
    {
        "id": "development.qa",
        "name": "Quality Review",
        "description": "Validate acceptance criteria and release readiness.",
        "parent_process_id": "development",
        "node_type": "subprocess",
        "template_id": "subprocess-qa",
    },
    # Decomposition is a peer top-level process (ELS-79), distinct
    # from the per-ticket SDLC. It runs against the planning anchor
    # of each new project: BA → Tech-arch → QA-arch → Developer slice
    # the brief into a coarse WBS + child tickets. Customer cron
    # drives it via ``shipctl run --routine wbs|architecture|...``;
    # ``planning_done`` flips Drafts → Parked.
    {
        "id": "decomposition",
        "name": "Decomposition",
        "description": (
            "Project-first delivery: BA, Tech-architect, QA-architect "
            "and Developer turn a project brief into a WBS and child "
            "tickets on the planning anchor. Drafts → Parked when done."
        ),
        "parent_process_id": None,
        "node_type": "process",
        "template_id": "process-decomposition",
    },
)

_PROCESS_STATE_ORDER: tuple[str, ...] = (
    # E16/ELS-123 canon — four bundle stages in execution order. The
    # dashboard's process projector iterates this tuple to render the
    # canvas; runtime aggregation skips lane keys not present here.
    # Pre-E16 stage ids are kept as aliases below in
    # :data:`_PROCESS_STATE_ALIASES` so in-flight tickets carrying
    # the old labels still project until ELS-124 wipes them.
    "planning",
    "dev_implementation",
    "validation",
    "code_review",
)

# Pre-E16 stage ids that should still count as process states for
# in-flight tickets seeded against the 7-stage chain. Kept separate
# from ``_PROCESS_STATE_ORDER`` so the canvas only renders the
# canonical four but the runtime aggregator
# (``_runtime_by_state``) doesn't drop pipeline runs that referenced
# the old ids.
_PROCESS_STATE_ALIASES: frozenset[str] = frozenset(
    {
        "task_intake",
        "tech_arch_plan",
        "qa_arch_plan",
        "qa_manual",
        "qa_automation",
        "pr_review",
        "bug_triage",
        "ba_requirements",
    }
)

_ROUTINE_IDS: frozenset[str] = frozenset(
    {
        # Canonical seven (matches lane_recipes.DEFAULT_SEED_LANES).
        "daily",
        "retro",
        "healthcheck",
        "tech_review",
        "qa_review",
        "security_review",
        "process_review",
        # Legacy ids — accepted at projection time so old repos keep
        # working until rewritten, but no longer emitted by the seed.
        # Display layer collapses these to the canonical name where it
        # can; otherwise they pass through as-is.
        "daily_architecture_tests_review",
        "daily_technical_architecture_review",
        "daily_security_review",
        "daily_digest",
        "daily_retro",
        "self_heal",
        "daily_standup",
        "tech_debt",
        "code_map",
        "flow_release_notes",
        "scan_docs_freshness",
        "scan_license_deps",
        "scan_security_deps",
    }
)


class ProcessConditionOut(BaseModel):
    expression: str


class ProcessTriggerOut(BaseModel):
    type: Literal["schedule", "event", "manual"]
    interval: str | None = None
    event: str | None = None


class ProcessScheduleTriggerOut(BaseModel):
    kind: Literal["schedule", "event", "manual"] = "schedule"
    event: str | None = None


class ProcessScheduleSlotOut(BaseModel):
    id: str
    local_time: str
    weekdays: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    specialist_ids: list[str] = Field(default_factory=list)
    label: str | None = None


class ProcessScheduleOut(BaseModel):
    trigger: ProcessScheduleTriggerOut = Field(default_factory=ProcessScheduleTriggerOut)
    time_zone: str = "UTC"
    slots: list[ProcessScheduleSlotOut] = Field(default_factory=list)


class ProcessStateRuntimeOut(BaseModel):
    task_count: int = 0
    blocked_count: int = 0
    last_execution_time: datetime | None = None
    health: Health = "ok"


class ProcessStateOut(BaseModel):
    """A *stage* in a process — work step the operator sees on the canvas.

    Despite the type name (kept stable for now to avoid an API rename
    sweep), each entry is conceptually a "stage": a discrete step where
    a specialist does something. Each stage carries a canonical
    ``state`` field that buckets it into one of the seven lifecycle
    phases (backlog/planning/executing/reviewing/awaiting_input/
    blocked/closed). The tracker projection lives at the canonical
    state level, not at the stage level — so adding a "Security Audit"
    stage with state="reviewing" automatically inherits the existing
    Linear "In Review" mapping.
    """

    id: str
    name: str
    specialist_id: str
    specialist_name: str
    instructions: str
    state: CanonicalState = "planning"
    triggers: list[ProcessTriggerOut] = Field(default_factory=list)
    exit_conditions: list[ProcessConditionOut] = Field(default_factory=list)
    block_conditions: list[ProcessConditionOut] = Field(default_factory=list)
    runtime: ProcessStateRuntimeOut = Field(default_factory=ProcessStateRuntimeOut)


TransitionActor = Literal["user", "agent", "either"]


class ProcessTransitionOut(BaseModel):
    """One arrow between two stages on the canvas.

    ``trigger_actor`` declares who fires this transition:

      - ``user``   — only a human (operator) advances the ticket. Used
                     for backlog → planning (operator drags Backlog to
                     Todo) and reviewing → closed (operator approves).
      - ``agent``  — an agent stage completion advances it. The default
                     for intra-process handoffs.
      - ``either`` — both paths are valid. Used for awaiting_input
                     resume and similar overlay clears.

    The canvas renders different edge styles per actor so the operator
    can scan the FSM and tell at a glance "where do I have to step in"
    vs. "what runs by itself".
    """

    id: str
    from_state_id: str
    to_state_id: str
    conditions: list[ProcessConditionOut] = Field(default_factory=list)
    requires_human: bool = False
    trigger_actor: TransitionActor = "agent"


class ProcessSpecialistOut(BaseModel):
    id: str
    name: str
    role: str
    capabilities: list[str] = Field(default_factory=list)
    agent_profile: str = "auto"
    version: str | None = "ship-default-v1"
    source: str = "ship_managed"


class RoleTemplateOut(BaseModel):
    id: str
    name: str
    description: str
    prompt_template: str
    capabilities: list[str] = Field(default_factory=list)
    default_agent_profile: str = "auto"
    version: str
    source: str
    default_phases: list[str] = Field(default_factory=list)


class ProcessTaskOut(BaseModel):
    id: str
    title: str
    state_id: str
    status: TaskStatus
    last_updated: datetime | None = None
    context: dict[str, Any] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)


class ProcessRoutineOut(BaseModel):
    id: str
    name: str
    specialist_id: str
    specialist_name: str
    schedule: str | None = None
    prompt: str = ""
    instructions: str = ""
    last_run: datetime | None = None
    status: str | None = None
    enabled: bool = True
    description: str = ""
    trigger: dict[str, Any] | None = None
    scope: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    prompt_record: dict[str, Any] | None = None


class ProcessLinkOut(BaseModel):
    id: str
    from_process_id: str
    from_state_id: str | None = None
    from_node_id: str | None = None
    to_process_id: str
    to_state_id: str | None = None
    to_node_id: str | None = None
    type: ProcessLinkType
    conditions: list[ProcessConditionOut] = Field(default_factory=list)
    label: str | None = None
    io_contract: dict[str, Any] = Field(default_factory=dict)


class ProcessNodeOut(BaseModel):
    id: str
    process_id: str
    type: ProcessNodeType
    name: str
    description: str = ""
    parent_process_id: str | None = None
    child_process_id: str | None = None
    template_id: str | None = None
    x: int = 0
    y: int = 0
    status: Health = "ok"


class ProcessGraphOut(BaseModel):
    nodes: list[ProcessNodeOut] = Field(default_factory=list)
    links: list[ProcessLinkOut] = Field(default_factory=list)


class ProcessSummaryOut(BaseModel):
    id: str
    name: str
    primary: bool
    state_count: int
    task_count: int
    blocked_count: int
    health: Health
    description: str = ""
    parent_process_id: str | None = None
    node_type: ProcessNodeType = "process"
    template_id: str | None = None


class ProcessAdapterDiagnosticOut(BaseModel):
    kind: Literal["tracker", "runner", "agent"]
    name: str
    status: Literal["ok", "degraded", "not_configured", "unknown"]
    message: str
    capabilities: list[str] = Field(default_factory=list)
    missing_mappings: list[str] = Field(default_factory=list)


class ProcessListOut(BaseModel):
    primary_process_id: str
    processes: list[ProcessSummaryOut]
    process_graph: ProcessGraphOut = Field(default_factory=ProcessGraphOut)
    adapter_diagnostics: list[ProcessAdapterDiagnosticOut] = Field(default_factory=list)


class ProcessOut(ProcessSummaryOut):
    specialists: list[ProcessSpecialistOut] = Field(default_factory=list)
    states: list[ProcessStateOut] = Field(default_factory=list)
    transitions: list[ProcessTransitionOut] = Field(default_factory=list)
    tasks: list[ProcessTaskOut] = Field(default_factory=list)
    routines: list[ProcessRoutineOut] = Field(default_factory=list)
    schedule: ProcessScheduleOut | None = None
    # canonical → native tracker state mapping no longer surfaces in the
    # process API. The runtime resolves it via the bound integration
    # (``Integration.config.canonical_to_native``, populated by the
    # OAuth provisioner). Adapter migration / process modification
    # don't require operator-edited YAML mapping anymore.
    process_graph: ProcessGraphOut = Field(default_factory=ProcessGraphOut)
    adapter_diagnostics: list[ProcessAdapterDiagnosticOut] = Field(default_factory=list)


class ProcessTicketPickerOut(BaseModel):
    tracker: str | None = None
    tickets: list[dict[str, Any]] = Field(default_factory=list)


class ProcessExitTicketRefIn(BaseModel):
    kind: Literal["github_issues", "linear", "notion", "jira"]
    id: str = Field(min_length=1, max_length=255)
    workspace_hint: str | None = Field(default=None, max_length=255)
    display_id: str | None = Field(default=None, max_length=255)
    url: str | None = Field(default=None, max_length=1024)


class ProcessExitIntentIn(BaseModel):
    type: Literal["clarification", "handoff", "complete_with_pr_or_result"]
    state_id: str = Field(min_length=1, max_length=128)
    ticket: ProcessExitTicketRefIn | None = None
    tracker: str | None = Field(default=None, max_length=32)
    project_hint: str | None = Field(default=None, max_length=255)
    message: str | None = Field(default=None, max_length=16_000)
    to_state_id: str | None = Field(default=None, max_length=128)
    tracker_state: str | None = Field(default=None, max_length=128)
    pr_url: str | None = Field(default=None, max_length=1024)
    result_summary: str | None = Field(default=None, max_length=16_000)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class ProcessExitIntentOut(BaseModel):
    type: str
    status: Literal["accepted", "rejected"]
    process_id: str
    state_id: str
    to_state_id: str | None = None
    audit_action: str
    clarification_id: uuid.UUID | None = None
    tracker_action: str | None = None
    pr_url: str | None = None
    result_summary: str | None = None


@router.get("", response_model=ProcessListOut)
async def list_processes(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ProcessListOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    process = await _build_development_process(session, workspace_id)
    decomposition = await _build_decomposition_process(session, workspace_id)
    summaries = _seeded_process_summaries(process, decomposition)
    return ProcessListOut(
        primary_process_id=PRIMARY_PROCESS_ID,
        processes=summaries,
        process_graph=_workspace_process_graph(summaries),
        adapter_diagnostics=process.adapter_diagnostics,
    )


async def get_process_adapters(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ProcessAdapterDiagnosticOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    return await _adapter_diagnostics(session, workspace_id)


@router.get("/role-templates", response_model=list[RoleTemplateOut])
async def list_role_templates(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[RoleTemplateOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    return [
        RoleTemplateOut(
            id=role.id,
            name=role.name,
            description=role.description,
            prompt_template=role.prompt_template,
            capabilities=list(role.capabilities),
            default_agent_profile=role.default_agent_profile,
            version=role.version,
            source=role.source,
            default_phases=list(role.default_phases),
        )
        for role in default_role_templates()
    ]


@router.get("/{process_id}", response_model=ProcessOut)
async def get_process(
    workspace_id: uuid.UUID,
    process_id: str,
    repo_id: uuid.UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ProcessOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    known_process_ids = {str(row["id"]) for row in _SEEDED_PROCESSES}
    if process_id not in known_process_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="process not found",
        )
    if process_id == DECOMPOSITION_PROCESS_ID:
        return await _build_decomposition_process(session, workspace_id)
    return await _build_development_process(
        session,
        workspace_id,
        repo_id=repo_id,
        process_id=process_id,
    )


async def list_process_tickets(
    workspace_id: uuid.UUID,
    process_id: str,
    tracker: str | None = Query(default=None),
    project_hint: str | None = Query(default=None),
    state: Literal["open", "closed", "all"] | None = Query(default="open"),
    query: str | None = Query(default=None),
    assignee_me: bool = Query(default=False),
    assignee: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=25),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ProcessTicketPickerOut:
    """Read-only tracker picker for selecting ticket context before agent work."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    if process_id != PRIMARY_PROCESS_ID:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="process not found",
        )
    toolbox = ToolBox(
        session,
        settings=settings,
        workspace_id=workspace_id,
        user_id=auth.user.id,
    )
    try:
        raw = await toolbox._tool_ticket_list(  # noqa: SLF001 - read-only reuse of the agent gateway
            {
                "tracker": tracker,
                "project_hint": project_hint,
                "state": state,
                "query": query,
                "assignee_me": assignee_me,
                "assignee": assignee,
                "limit": limit,
            }
        )
        data = json.loads(raw)
    except ToolInvocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "ticket_picker_failed", "message": str(exc)},
        ) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "ticket_picker_bad_response"},
        ) from exc
    tickets = list(data.get("tickets") or [])
    _add_process_audit(
        session,
        workspace_id=workspace_id,
        auth=auth,
        action="process.ticket_picker.listed",
        process_id=process_id,
        payload={
            "tracker": data.get("tracker") or tracker,
            "project_hint": project_hint,
            "state": state,
            "query": query,
            "assignee_me": assignee_me,
            "assignee": assignee,
            "limit": limit,
            "ticket_count": len(tickets),
        },
    )
    await session.flush()
    return ProcessTicketPickerOut(
        tracker=data.get("tracker"),
        tickets=tickets,
    )


async def submit_process_exit_intent(
    workspace_id: uuid.UUID,
    process_id: str,
    payload: ProcessExitIntentIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ProcessExitIntentOut:
    """Ship-owned side effects for specialist exit intents."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_MAINTAIN)
    process = await _require_process(session, workspace_id, process_id)
    _require_state(process, payload.state_id)

    if payload.type == "clarification":
        return await _handle_clarification_exit(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
            process_id=process_id,
            auth=auth,
            payload=payload,
        )
    if payload.type == "handoff":
        return await _handle_handoff_exit(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
            process=process,
            process_id=process_id,
            auth=auth,
            payload=payload,
        )
    return await _handle_completion_exit(
        session=session,
        workspace_id=workspace_id,
        process_id=process_id,
        auth=auth,
        payload=payload,
    )


async def _require_process(
    session: AsyncSession, workspace_id: uuid.UUID, process_id: str
) -> ProcessOut:
    if process_id != PRIMARY_PROCESS_ID:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="process not found",
        )
    return await _build_development_process(session, workspace_id)


def _require_state(process: ProcessOut, state_id: str) -> ProcessStateOut:
    for state in process.states:
        if state.id == state_id:
            return state
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "unknown_process_state", "state_id": state_id},
    )


def _find_transition(
    process: ProcessOut, from_state_id: str, to_state_id: str
) -> ProcessTransitionOut | None:
    for transition in process.transitions:
        if (
            transition.from_state_id == from_state_id
            and transition.to_state_id == to_state_id
        ):
            return transition
    return None


async def _handle_clarification_exit(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
    process_id: str,
    auth: AuthContext,
    payload: ProcessExitIntentIn,
) -> ProcessExitIntentOut:
    message = (payload.message or "").strip()
    if not message:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "clarification_message_required"},
        )
    if payload.ticket is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "ticket_required_for_clarification"},
        )

    ticket_ref = _to_ticket_ref(payload.ticket)
    tracker = await _resolve_tracker_for_exit(
        session=session,
        settings=settings,
        workspace_id=workspace_id,
        user_id=auth.user.id,
        tracker=payload.tracker or payload.ticket.kind,
        project_hint=payload.project_hint,
    )
    await tracker.comment(ticket_ref, body=_render_clarification_comment(message))

    clarification = Clarification(
        workspace_id=workspace_id,
        ticket_ref=payload.ticket.display_id or payload.ticket.id,
        question=message,
        status="open",
        context={
            "process_id": process_id,
            "state_id": payload.state_id,
            "exit_type": payload.type,
            "ticket": payload.ticket.model_dump(),
        },
        source="tracker",
        tracker_provider=payload.ticket.kind,
        tracker_issue_key=payload.ticket.display_id or payload.ticket.id,
        tracker_issue_url=payload.ticket.url,
    )
    session.add(clarification)
    await session.flush()
    await mirror_clarification_create(
        session,
        clarification=clarification,
        actor_user_id=auth.user.id,
    )

    _add_process_audit(
        session,
        workspace_id=workspace_id,
        auth=auth,
        action="process.exit.clarification_posted",
        process_id=process_id,
        payload={
            "state_id": payload.state_id,
            "ticket": payload.ticket.model_dump(),
            "clarification_id": str(clarification.id),
        },
    )
    await session.flush()
    return ProcessExitIntentOut(
        type=payload.type,
        status="accepted",
        process_id=process_id,
        state_id=payload.state_id,
        audit_action="process.exit.clarification_posted",
        clarification_id=clarification.id,
        tracker_action="comment",
    )


async def _handle_handoff_exit(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
    process: ProcessOut,
    process_id: str,
    auth: AuthContext,
    payload: ProcessExitIntentIn,
) -> ProcessExitIntentOut:
    to_state_id = (payload.to_state_id or "").strip()
    if not to_state_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "to_state_id_required"},
        )
    _require_state(process, to_state_id)
    transition = _find_transition(process, payload.state_id, to_state_id)
    if transition is None:
        _add_process_audit(
            session,
            workspace_id=workspace_id,
            auth=auth,
            action="process.exit.handoff_rejected",
            process_id=process_id,
            payload={
                "from_state_id": payload.state_id,
                "to_state_id": to_state_id,
                "reason": "transition_not_configured",
            },
        )
        await session.flush()
        return ProcessExitIntentOut(
            type=payload.type,
            status="rejected",
            process_id=process_id,
            state_id=payload.state_id,
            to_state_id=to_state_id,
            audit_action="process.exit.handoff_rejected",
        )

    tracker_action: str | None = None
    if payload.ticket is not None:
        tracker = await _resolve_tracker_for_exit(
            session=session,
            settings=settings,
            workspace_id=workspace_id,
            user_id=auth.user.id,
            tracker=payload.tracker or payload.ticket.kind,
            project_hint=payload.project_hint,
        )
        await tracker.transition(
            _to_ticket_ref(payload.ticket),
            to_state=(payload.tracker_state or to_state_id),
        )
        tracker_action = "transition"

    _add_process_audit(
        session,
        workspace_id=workspace_id,
        auth=auth,
        action="process.exit.handoff_completed",
        process_id=process_id,
        payload={
            "from_state_id": payload.state_id,
            "to_state_id": to_state_id,
            "transition_id": transition.id,
            "ticket": payload.ticket.model_dump() if payload.ticket else None,
            "tracker_state": payload.tracker_state or to_state_id,
        },
    )
    await session.flush()
    return ProcessExitIntentOut(
        type=payload.type,
        status="accepted",
        process_id=process_id,
        state_id=payload.state_id,
        to_state_id=to_state_id,
        audit_action="process.exit.handoff_completed",
        tracker_action=tracker_action,
    )


async def _handle_completion_exit(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    process_id: str,
    auth: AuthContext,
    payload: ProcessExitIntentIn,
) -> ProcessExitIntentOut:
    summary = (payload.result_summary or "").strip()
    pr_url = (payload.pr_url or "").strip() or None
    if not summary and pr_url is None and not payload.artifacts:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "completion_result_required"},
        )
    _add_process_audit(
        session,
        workspace_id=workspace_id,
        auth=auth,
        action="process.exit.completed",
        process_id=process_id,
        payload={
            "state_id": payload.state_id,
            "pr_url": pr_url,
            "result_summary": summary or None,
            "artifacts": payload.artifacts,
        },
    )
    await session.flush()
    return ProcessExitIntentOut(
        type=payload.type,
        status="accepted",
        process_id=process_id,
        state_id=payload.state_id,
        audit_action="process.exit.completed",
        pr_url=pr_url,
        result_summary=summary or None,
    )


async def _resolve_tracker_for_exit(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    tracker: str | None,
    project_hint: str | None,
):
    toolbox = ToolBox(
        session,
        settings=settings,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    try:
        return await toolbox._resolve_tracker(tracker, project_hint)  # noqa: SLF001 - Ship-owned side effect gateway
    except ToolInvocationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "tracker_resolution_failed", "message": str(exc)},
        ) from exc


def _to_ticket_ref(ticket: ProcessExitTicketRefIn) -> TicketRef:
    return TicketRef(
        kind=ticket.kind,
        workspace_hint=ticket.workspace_hint,
        id=ticket.id,
    )


def _render_clarification_comment(message: str) -> str:
    return f"> **@ship clarification:**\n{message.strip()}\n"


def _add_process_audit(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    auth: AuthContext,
    action: str,
    process_id: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action=action,
            target_kind="process",
            target_id=process_id,
            payload=payload,
        )
    )


async def _build_decomposition_process(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> ProcessOut:
    """Project the decomposition FSM as a peer top-level process.

    Source of truth is :func:`catalog.default_planning_process_config`.
    Today decomposition has no separate ``Routine`` / ``Pipeline`` rows
    (the planning anchor is a tracker concept, not a workspace
    pipeline), so this projection is static — runtime aggregation is
    intentionally omitted until decomposition picks up its own runs
    table. The dashboard renders the canvas + routine cards from the
    static states + the four routines defined in the catalog.
    """
    from backend.app.services.catalog import default_planning_process_config

    config = default_planning_process_config()
    specialists = _specialists()
    raw_states = config.get("states") or []
    states: list[ProcessStateOut] = []
    for entry in raw_states:
        if not isinstance(entry, dict):
            continue
        stage_id = str(entry.get("id") or "")
        if not stage_id:
            continue
        spec = entry.get("specialist") or {}
        specialist_id = str(spec.get("id") or "developer")
        # ``_specialists`` is keyed by role-template id (``business_analyst``
        # etc.), not the kebab-case agent-role slug, so a direct lookup
        # often misses for decomposition specialists. Fall back to the
        # SDLC stage's specialist when the kebab-case slug is unknown.
        if specialist_id not in specialists:
            specialist_id = _specialist_for_lane(stage_id)
        states.append(
            ProcessStateOut(
                id=stage_id,
                name=str(entry.get("name") or _titleize(stage_id)),
                specialist_id=specialist_id,
                specialist_name=specialists[specialist_id].name,
                instructions=str(entry.get("instructions") or ""),
                state=_canonical_state_for(stage_id),
                triggers=[ProcessTriggerOut(type="event", event="anchor_handoff")],
                exit_conditions=[],
                block_conditions=[],
                runtime=ProcessStateRuntimeOut(health="ok"),
            )
        )

    transitions: list[ProcessTransitionOut] = []
    for left, right in zip(states, states[1:]):
        transitions.append(
            ProcessTransitionOut(
                id=f"{left.id}_to_{right.id}",
                from_state_id=left.id,
                to_state_id=right.id,
                conditions=[],
                requires_human=False,
                trigger_actor=_transition_actor(left.state, right.state),
            )
        )

    routines: list[ProcessRoutineOut] = []
    raw_routines = config.get("routines") or {}
    if isinstance(raw_routines, dict):
        for routine_id, routine in raw_routines.items():
            if not isinstance(routine, dict):
                continue
            specialist_slug = str(routine.get("specialist") or "")
            specialist_template_id = _specialist_for_lane(routine_id)
            specialist_template = specialists.get(specialist_template_id)
            specialist_name = (
                specialist_template.name
                if specialist_template
                else specialist_slug or routine_id
            )
            trigger = routine.get("trigger") or {}
            cron = (
                str(trigger.get("cron"))
                if isinstance(trigger, dict) and trigger.get("cron")
                else None
            )
            routines.append(
                ProcessRoutineOut(
                    id=str(routine_id),
                    name=str(routine.get("name") or _titleize(str(routine_id))),
                    specialist_id=specialist_template_id,
                    specialist_name=specialist_name,
                    schedule=cron,
                    prompt="",
                    instructions=str(routine.get("description") or ""),
                    last_run=None,
                    status=None,
                    enabled=bool(routine.get("enabled", True)),
                    description=str(routine.get("description") or ""),
                    trigger=trigger if isinstance(trigger, dict) else None,
                )
            )

    process_meta = _seeded_process_meta(DECOMPOSITION_PROCESS_ID)
    adapter_diagnostics = await _adapter_diagnostics(session, workspace_id)
    return ProcessOut(
        id=DECOMPOSITION_PROCESS_ID,
        name=str(process_meta["name"]),
        primary=False,
        state_count=len(states),
        task_count=0,
        blocked_count=0,
        health="ok",
        description=str(process_meta["description"]),
        parent_process_id=process_meta["parent_process_id"],
        node_type=process_meta["node_type"],
        template_id=process_meta["template_id"],
        specialists=list(specialists.values()),
        states=states,
        transitions=transitions,
        tasks=[],
        routines=routines,
        schedule=_default_schedule(states),
        process_graph=_inner_process_graph(
            DECOMPOSITION_PROCESS_ID, states, transitions
        ),
        adapter_diagnostics=adapter_diagnostics,
    )


async def _build_development_process(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID | None = None,
    process_id: str = PRIMARY_PROCESS_ID,
) -> ProcessOut:
    repo_rows = list(
        (
            await session.execute(
                select(WorkspaceRepo).where(WorkspaceRepo.workspace_id == workspace_id)
            )
        )
        .scalars()
        .all()
    )
    repos = {row.id: row.full_name for row in repo_rows}
    if repo_id is not None and repo_id not in repos:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="repo not found",
        )

    lane_stmt = select(Routine).where(Routine.workspace_id == workspace_id)
    if repo_id is not None:
        lane_stmt = lane_stmt.where(Routine.repo_id == repo_id)
    lanes = list(
        (
            await session.execute(
                lane_stmt.order_by(Routine.created_at)
            )
        )
        .scalars()
        .all()
    )

    routine_ids = {row.id for row in lanes}
    if repo_id is not None and not routine_ids:
        runs = []
    else:
        run_stmt = select(RoutineRun).where(RoutineRun.workspace_id == workspace_id)
        if repo_id is not None:
            run_stmt = run_stmt.where(RoutineRun.routine_id.in_(routine_ids))
        runs = list(
            (
                await session.execute(
                    run_stmt.order_by(
                        desc(RoutineRun.started_at),
                        desc(RoutineRun.created_at),
                    ).limit(75)
                )
            )
            .scalars()
            .all()
        )
    inbox_items = list(
        (
            await session.execute(
                select(InboxItem)
                .where(
                    InboxItem.workspace_id == workspace_id,
                    InboxItem.status.in_(("new", "snoozed")),
                )
                .order_by(desc(InboxItem.created_at))
                .limit(75)
            )
        )
        .scalars()
        .all()
    )
    lane_by_uuid = {lane.id: lane for lane in lanes}
    lane_keys = _state_lane_ids(lanes)
    specialists = _specialists()
    state_runtime = _runtime_by_state(lane_keys, runs, lane_by_uuid)
    blocked_by_state = _blocked_counts(lane_keys, inbox_items)

    states: list[ProcessStateOut] = []
    routines: list[ProcessRoutineOut] = []
    for lane_key in lane_keys:
        lane = next((row for row in lanes if row.lane_id == lane_key), None)
        specialist_id = _specialist_for_lane(lane_key)
        runtime = state_runtime[lane_key]
        runtime.blocked_count = blocked_by_state[lane_key]
        if runtime.blocked_count > 0 and runtime.health == "ok":
            runtime.health = "degraded"
        is_known_state = (
            lane_key in _PROCESS_STATE_ORDER or lane_key in _PROCESS_STATE_ALIASES
        )
        if not is_known_state or lane_key in _ROUTINE_IDS:
            routine_text = _routine_instructions(lane_key)
            routines.append(
                ProcessRoutineOut(
                    id=lane_key,
                    name=_titleize(lane_key),
                    specialist_id=specialist_id,
                    specialist_name=specialists[specialist_id].name,
                    schedule=lane.cron if lane else None,
                    prompt=routine_text,
                    instructions=routine_text,
                    last_run=runtime.last_execution_time,
                    status=lane.last_run_status if lane else None,
                    enabled=lane.enabled if lane else True,
                    description=_routine_description(lane_key),
                )
            )
            continue

        states.append(
            ProcessStateOut(
                id=lane_key,
                name=_titleize(lane_key),
                specialist_id=specialist_id,
                specialist_name=specialists[specialist_id].name,
                instructions=_state_instructions(lane_key),
                state=_canonical_state_for(lane_key),
                triggers=_triggers_for(lane),
                exit_conditions=[],
                block_conditions=[],
                runtime=runtime,
            )
        )

    if not states:
        states = _default_states(specialists)

    # Default sequential transitions: each arrow's trigger_actor is
    # derived from the buckets of the two stages it connects. Cross-
    # bucket arrows where the SOURCE bucket is human-gated (``backlog``
    # → planning, ``reviewing`` → closed) are user-only; everything
    # else is agent-driven by default. See _transition_actor() for the
    # full table.
    transitions = [
        ProcessTransitionOut(
            id=f"{left.id}_to_{right.id}",
            from_state_id=left.id,
            to_state_id=right.id,
            conditions=[],
            requires_human=False,
            trigger_actor=_transition_actor(left.state, right.state),
        )
        for left, right in zip(states, states[1:])
    ]
    tasks = _tasks_for(states, runs, inbox_items, lane_by_uuid, repos)
    state_count = len(states)
    task_count = len(tasks)
    blocked_count = sum(1 for task in tasks if task.status == "blocked")
    health = _process_health(states, blocked_count)
    adapter_diagnostics = await _adapter_diagnostics(session, workspace_id)

    process_meta = _seeded_process_meta(process_id)
    scoped_states = _states_for_process_id(process_id, states)
    scoped_transitions = [
        transition
        for transition in transitions
        if any(state.id == transition.from_state_id for state in scoped_states)
        and any(state.id == transition.to_state_id for state in scoped_states)
    ]
    scoped_tasks = [
        task for task in tasks if any(state.id == task.state_id for state in scoped_states)
    ]
    scoped_blocked_count = sum(1 for task in scoped_tasks if task.status == "blocked")
    scoped_health = _process_health(scoped_states, scoped_blocked_count)

    return ProcessOut(
        id=process_id,
        name=str(process_meta["name"]),
        primary=process_id == PRIMARY_PROCESS_ID,
        state_count=len(scoped_states),
        task_count=len(scoped_tasks),
        blocked_count=scoped_blocked_count,
        health=scoped_health,
        description=str(process_meta["description"]),
        parent_process_id=process_meta["parent_process_id"],
        node_type=process_meta["node_type"],
        template_id=process_meta["template_id"],
        specialists=list(specialists.values()),
        states=scoped_states,
        transitions=scoped_transitions,
        tasks=scoped_tasks,
        routines=routines,
        schedule=_default_schedule(scoped_states),
        process_graph=_inner_process_graph(process_id, scoped_states, scoped_transitions),
        adapter_diagnostics=adapter_diagnostics,
    )


def _seeded_process_meta(process_id: str) -> dict[str, Any]:
    return next(
        (row for row in _SEEDED_PROCESSES if row["id"] == process_id),
        _SEEDED_PROCESSES[0],
    )


def _seeded_process_summaries(
    process: ProcessOut,
    decomposition: ProcessOut | None = None,
) -> list[ProcessSummaryOut]:
    summaries: list[ProcessSummaryOut] = []
    for row in _SEEDED_PROCESSES:
        is_primary = row["id"] == PRIMARY_PROCESS_ID
        is_decomp = row["id"] == DECOMPOSITION_PROCESS_ID
        if is_primary:
            state_count = process.state_count
            task_count = process.task_count
            blocked_count = process.blocked_count
            health = process.health
        elif is_decomp and decomposition is not None:
            state_count = decomposition.state_count
            task_count = decomposition.task_count
            blocked_count = decomposition.blocked_count
            health = decomposition.health
        else:
            state_count = 0
            task_count = 0
            blocked_count = 0
            health = "ok"
        summaries.append(
            ProcessSummaryOut(
                id=str(row["id"]),
                name=str(row["name"]),
                primary=is_primary,
                state_count=state_count,
                task_count=task_count,
                blocked_count=blocked_count,
                health=health,
                description=str(row["description"]),
                parent_process_id=row["parent_process_id"],
                node_type=row["node_type"],
                template_id=row["template_id"],
            )
        )
    return summaries


def _workspace_process_graph(summaries: list[ProcessSummaryOut]) -> ProcessGraphOut:
    top_level_summaries = [
        summary for summary in summaries if summary.parent_process_id is None
    ]
    positions = {
        "workspace": (340, 270),
        "development": (340, 42),
        "decomposition": (620, 42),
    }
    nodes = [
        ProcessNodeOut(
            id="node-workspace",
            process_id="workspace",
            type="workspace",
            name="Workspace",
            description="Root orchestration map for this workspace.",
            x=positions["workspace"][0],
            y=positions["workspace"][1],
            status="ok",
        ),
        *[
        ProcessNodeOut(
            id=f"node-{summary.id}",
            process_id=summary.id,
            type=summary.node_type,
            name=summary.name,
            description=summary.description,
            parent_process_id=summary.parent_process_id,
            child_process_id=summary.id if summary.node_type == "subprocess" else None,
            template_id=summary.template_id,
            x=positions.get(summary.id, (80, 80))[0],
            y=positions.get(summary.id, (80, 80))[1],
            status=summary.health,
        )
        for summary in top_level_summaries
        ],
    ]
    return ProcessGraphOut(
        nodes=nodes,
        links=[
            ProcessLinkOut(
                id="workspace-to-development",
                from_process_id="workspace",
                from_node_id="node-workspace",
                to_process_id="development",
                to_node_id="node-development",
                type="handoff",
                label="Development process",
                conditions=[ProcessConditionOut(expression="workspace.process == 'development'")],
                io_contract={"passes": ["workspace_policies", "canonical_state"]},
            ),
            ProcessLinkOut(
                id="workspace-to-decomposition",
                from_process_id="workspace",
                from_node_id="node-workspace",
                to_process_id="decomposition",
                to_node_id="node-decomposition",
                type="handoff",
                label="Decomposition process",
                conditions=[
                    ProcessConditionOut(
                        expression="workspace.process == 'decomposition'"
                    )
                ],
                io_contract={"passes": ["project_brief", "planning_anchor"]},
            ),
        ],
    )


def _inner_process_graph(
    process_id: str,
    states: list[ProcessStateOut],
    transitions: list[ProcessTransitionOut],
) -> ProcessGraphOut:
    nodes = [
        ProcessNodeOut(
            id=f"{process_id}.{state.id}",
            process_id=process_id,
            type="subprocess" if process_id != PRIMARY_PROCESS_ID else "process",
            name=state.name,
            description=state.instructions,
            parent_process_id=process_id,
            template_id=state.specialist_id,
            x=120 + index * 240,
            y=120,
            status=state.runtime.health if state.runtime else "ok",
        )
        for index, state in enumerate(states)
    ]
    links = [
        ProcessLinkOut(
            id=f"{process_id}.{transition.id}",
            from_process_id=process_id,
            from_state_id=transition.from_state_id,
            from_node_id=f"{process_id}.{transition.from_state_id}",
            to_process_id=process_id,
            to_state_id=transition.to_state_id,
            to_node_id=f"{process_id}.{transition.to_state_id}",
            type="handoff",
            label="Next state",
            conditions=transition.conditions,
        )
        for transition in transitions
    ]
    return ProcessGraphOut(nodes=nodes, links=links)


def _states_for_process_id(
    process_id: str,
    states: list[ProcessStateOut],
) -> list[ProcessStateOut]:
    if process_id == "development.requirements":
        return states[:2] or _default_states(_specialists())[:2]
    if process_id == "development.implementation":
        return states[1:4] or _default_states(_specialists())[1:4]
    if process_id == "development.qa":
        return states[-2:] or _default_states(_specialists())[-2:]
    if process_id == "documentation":
        return _placeholder_process_states(
            "docs_review",
            "Documentation review",
            "technical_writer",
            "Review merged changes, update docs, and prepare release notes.",
        )
    if process_id == "marketing":
        return _placeholder_process_states(
            "launch_review",
            "Launch review",
            "product_marketer",
            "Check whether the change needs customer-facing launch work.",
        )
    return states


def _placeholder_process_states(
    state_id: str,
    name: str,
    specialist_id: str,
    instructions: str,
) -> list[ProcessStateOut]:
    specialists = _specialists()
    specialist = specialists.get(specialist_id) or next(iter(specialists.values()))
    return [
        ProcessStateOut(
            id=state_id,
            name=name,
            specialist_id=specialist.id,
            specialist_name=specialist.name,
            instructions=instructions,
            state=_canonical_state_for(state_id),
            triggers=[ProcessTriggerOut(type="event", event="process_graph.handoff")],
            exit_conditions=[],
            block_conditions=[],
            runtime=ProcessStateRuntimeOut(health="ok"),
        )
    ]


def _state_lane_ids(lanes: list[Routine]) -> list[str]:
    """Routine ids the canvas + routines columns project.

    Source of truth is :class:`Routine` (the ``routines`` table), which
    ``lanes_sync`` keeps in lockstep with the repo's
    ``.ship/config.yml`` — rows for routines that disappear from the
    config are hard-deleted on next sync.
    """
    seen = {row.lane_id for row in lanes}
    ordered = list(_PROCESS_STATE_ORDER)
    extras = sorted(seen - set(ordered))
    return ordered + extras


def _specialists() -> dict[str, ProcessSpecialistOut]:
    rows = [
        ProcessSpecialistOut(
            id=role.id,
            name=role.name,
            role=role.description,
            capabilities=list(role.capabilities),
            agent_profile=role.default_agent_profile,
            version=role.version,
            source=role.source,
        )
        for role in default_role_templates()
    ]
    return {row.id: row for row in rows}


def _specialist_for_lane(lane_id: str) -> str:
    # Direct map for the canonical nine + the legacy ``pr_review``
    # alias. Names on the right are :data:`role_templates.py` ids
    # (the registry the dashboard reads via :func:`_specialists`);
    # the runtime resolver reads ``specialist:`` straight from the
    # YAML and uses agent_roles slugs instead, so this is purely the
    # cosmetic badge for the canvas.
    direct: dict[str, str] = {
        "task_intake": "intake",
        "bug_triage": "intake",
        "ba_requirements": "business_analyst",
        "tech_arch_plan": "technical_architect",
        "qa_arch_plan": "qa_engineer",
        "dev_implementation": "developer",
        "qa_manual": "qa_engineer",
        "qa_automation": "qa_engineer",
        "code_review": "code_reviewer",
        "pr_review": "code_reviewer",
        # Decomposition stages (ELS-79). Without these the substring
        # fallback below maps ``wbs`` / ``tasks`` / ``planning_done``
        # to the default ``devops_platform`` (or fails entirely),
        # which mis-renders the canvas badge AND raises a KeyError
        # when ``_build_decomposition_process`` looks up the
        # specialist by id. Pin them explicitly so the canvas shows
        # the right role and the projection never blows up.
        "wbs": "business_analyst",
        "architecture": "technical_architect",
        "test_architecture": "qa_engineer",
        "tasks": "developer",
        "planning_done": "developer",
    }
    if lane_id in direct:
        return direct[lane_id]
    # Substring fallback for non-canonical / custom lane ids that
    # operators add by hand. Order matters — narrower first.
    if "intake" in lane_id or "triage" in lane_id:
        return "intake"
    if "ba" in lane_id or "requirements" in lane_id:
        return "business_analyst"
    if "arch" in lane_id or "tech" in lane_id:
        return "technical_architect"
    if "dev" in lane_id or "implementation" in lane_id:
        return "developer"
    if "qa" in lane_id or "test" in lane_id:
        return "qa_engineer"
    if "review" in lane_id or "pr" in lane_id:
        return "code_reviewer"
    return "devops_platform"


def _canonical_state_for(stage_id: str) -> CanonicalState:
    """Bucket a stage id into one of the seven canonical lifecycle states.

    For our seeded stage ids we use the explicit ``_LEGACY_STAGE_TO_STATE``
    table. For anything we don't recognise (a custom stage the operator
    added) we default to ``planning`` — the safest bucket because a
    planning stage doesn't accidentally claim done-ness or block the
    flow. The operator can always override the canonical state by
    setting it explicitly in ``.ship/config.yml``.
    """
    return _LEGACY_STAGE_TO_STATE.get(stage_id, "planning")


# Cross-bucket transitions where the human is the trigger. Anything not
# in this set defaults to ``agent`` (the agent stage's own completion
# advances the ticket). The set covers:
#   - (backlog → planning): operator drags Backlog → Todo to start work
#   - (reviewing → closed): operator approves the merge / closes the issue
#   - (reviewing → planning): operator returns the work for rework
#   - (awaiting_input → *): operator answers the clarification, ticket
#     resumes from where it was frozen
#   - (blocked → *): operator clears the external blocker
_USER_TRANSITION_PAIRS: frozenset[tuple[CanonicalState, CanonicalState]] = frozenset(
    {
        ("backlog", "planning"),
        ("reviewing", "closed"),
        ("reviewing", "planning"),
        ("reviewing", "executing"),
        ("awaiting_input", "planning"),
        ("awaiting_input", "executing"),
        ("awaiting_input", "reviewing"),
        ("blocked", "planning"),
        ("blocked", "executing"),
        ("blocked", "reviewing"),
    }
)


def _transition_actor(
    from_state: CanonicalState, to_state: CanonicalState
) -> TransitionActor:
    """Pick the default actor for a transition between two canonical states."""
    if (from_state, to_state) in _USER_TRANSITION_PAIRS:
        return "user"
    return "agent"


def _default_schedule(states: list[ProcessStateOut]) -> ProcessScheduleOut:
    """Empty Capacity calendar by default.

    The previous version auto-seeded every weekday × time cell with
    the same specialist quartet, which made the calendar look like
    "everything is already configured" — operators couldn't tell
    template from real data, and the visual signal "no coverage" was
    impossible to read because every cell was full.

    Capacity now starts empty; operators drag specialists into cells
    deliberately. The FE renders an explicit empty state so the
    coverage gap is the obvious thing on the page.
    """
    return ProcessScheduleOut(
        trigger=ProcessScheduleTriggerOut(kind="schedule"),
        time_zone="UTC",
        slots=[],
    )


def _runtime_by_state(
    lane_keys: list[str],
    runs: list[RoutineRun],
    lane_by_uuid: dict[uuid.UUID, Routine],
) -> dict[str, ProcessStateRuntimeOut]:
    out = defaultdict(ProcessStateRuntimeOut)
    for key in lane_keys:
        out[key] = ProcessStateRuntimeOut()
    for run in runs:
        lane_key = _lane_key_for_run(run, lane_by_uuid)
        if lane_key is None:
            continue
        runtime = out[lane_key]
        if run.status in {"running", "queued", "pending"}:
            runtime.task_count += 1
        timestamp = run.started_at or run.created_at
        if timestamp and (
            runtime.last_execution_time is None or timestamp > runtime.last_execution_time
        ):
            runtime.last_execution_time = timestamp
        if run.status in {"failed", "error", "cancelled"}:
            runtime.health = "failed"
        elif run.status not in {"succeeded", "success", "ok"} and runtime.health == "ok":
            runtime.health = "degraded"
    return out


def _lane_key_for_run(
    run: RoutineRun,
    lane_by_uuid: dict[uuid.UUID, Routine],
) -> str | None:
    routine = lane_by_uuid.get(run.routine_id)
    return routine.lane_id if routine else None


def _blocked_counts(
    lane_keys: list[str], inbox_items: list[InboxItem]
) -> dict[str, int]:
    out = {key: 0 for key in lane_keys}
    fallback = lane_keys[0] if lane_keys else "task_intake"
    for item in inbox_items:
        payload = item.payload or {}
        state_id = str(
            payload.get("state_id")
            or payload.get("lane_id")
            or payload.get("process_state_id")
            or fallback
        )
        if state_id not in out:
            state_id = fallback
        out[state_id] += 1
    return out


def _tasks_for(
    states: list[ProcessStateOut],
    runs: list[RoutineRun],
    inbox_items: list[InboxItem],
    lane_by_uuid: dict[uuid.UUID, Routine],
    repos: dict[uuid.UUID, str],
) -> list[ProcessTaskOut]:
    state_ids = [state.id for state in states]
    fallback = state_ids[0] if state_ids else "task_intake"
    tasks: list[ProcessTaskOut] = []

    for item in inbox_items[:25]:
        payload = item.payload or {}
        state_id = str(
            payload.get("state_id")
            or payload.get("lane_id")
            or payload.get("process_state_id")
            or fallback
        )
        if state_id not in state_ids:
            state_id = fallback
        tasks.append(
            ProcessTaskOut(
                id=str(item.id),
                title=item.title,
                state_id=state_id,
                status="blocked",
                last_updated=item.created_at,
                context={
                    "source": "inbox",
                    "type": item.type,
                    "summary": item.summary,
                    "payload": payload,
                },
                blockers=[item.summary or item.title],
            )
        )

    for run in runs[:25]:
        routine = lane_by_uuid.get(run.routine_id)
        state_id = routine.lane_id if routine else fallback
        if state_id not in state_ids:
            state_id = fallback
        status_value: TaskStatus = (
            "done"
            if run.status in {"succeeded", "success", "ok"}
            else "blocked"
            if run.status in {"failed", "error", "cancelled"}
            else "active"
        )
        repo_name = repos.get(routine.repo_id) if routine and routine.repo_id else None
        tasks.append(
            ProcessTaskOut(
                id=str(run.id),
                title=_run_title(run, routine),
                state_id=state_id,
                status=status_value,
                last_updated=run.finished_at or run.started_at or run.created_at,
                context={
                    "source": "execution_window",
                    "trigger": run.trigger,
                    "status": run.status,
                    "repo": repo_name,
                    "summary": run.summary,
                    "outcome": run.outcome or {},
                },
                blockers=[run.summary] if status_value == "blocked" and run.summary else [],
            )
        )
    return tasks[:40]


def _run_title(run: RoutineRun, routine: Routine | None) -> str:
    base = _titleize(routine.lane_id) if routine else "Execution window"
    if run.summary:
        return run.summary[:120]
    return f"{base} · {run.status}"


def _default_states(
    specialists: dict[str, ProcessSpecialistOut]
) -> list[ProcessStateOut]:
    rows = [
        ("task_intake", "intake"),
        ("ba_requirements", "business_analyst"),
        ("tech_arch_plan", "technical_architect"),
        ("dev_implementation", "developer"),
        ("qa_manual", "qa_engineer"),
        ("pr_review", "code_reviewer"),
    ]
    return [
        ProcessStateOut(
            id=state_id,
            name=_titleize(state_id),
            specialist_id=specialist_id,
            specialist_name=specialists[specialist_id].name,
            instructions=_state_instructions(state_id),
            state=_canonical_state_for(state_id),
            triggers=[ProcessTriggerOut(type="manual")],
            exit_conditions=[],
            block_conditions=[],
        )
        for state_id, specialist_id in rows
    ]


def _triggers_for(lane: Routine | None) -> list[ProcessTriggerOut]:
    raw = lane.config_blob if lane else {}
    if lane and lane.kind == "schedule":
        return [ProcessTriggerOut(type="schedule", interval=lane.cron)]
    if lane and lane.kind == "event":
        event = raw.get("trigger") or raw.get("event") or raw.get("on")
        return [ProcessTriggerOut(type="event", event=str(event or "event"))]
    if lane and lane.kind == "once":
        return [ProcessTriggerOut(type="manual")]
    return [ProcessTriggerOut(type="manual")]


def _state_instructions(lane_id: str) -> str:
    return (
        "Execute this state using the task context, specialist role, and "
        "runtime pattern discovery. Do not assume a static pattern mapping."
    )


def _routine_instructions(lane_id: str) -> str:
    return (
        "Run this supporting routine on its configured cadence and attach "
        "evidence back to the process history."
    )


def _routine_description(lane_id: str) -> str:
    """Short human copy for the routine card; distinct from long-form instructions."""
    known = {
        # Canonical six.
        "daily": "Morning digest of in-flight work, blockers, and risks.",
        "retro": "End-of-day retro: what went well, what to improve, next actions.",
        "healthcheck": "Reconcile CI, workflows, and guardrails after failed runs.",
        "tech_review": "Architecture drift and design consistency review.",
        "qa_review": "Test architecture, coverage, and flakiness signals.",
        "security_review": "Security posture and dependency signal sweep.",
        # Legacy ids — kept around so existing repos with stale config
        # still get sensible copy until they're rewritten.
        "daily_architecture_tests_review": "Test architecture, coverage, and flakiness signals.",
        "daily_technical_architecture_review": "Architecture drift and design consistency review.",
        "daily_security_review": "Security posture and dependency signal sweep.",
        "daily_digest": "Morning digest of in-flight work, blockers, and risks.",
        "daily_retro": "End-of-day retro: what went well, what to improve, next actions.",
        "self_heal": "Reconcile CI, workflows, and guardrails after failed runs.",
        "daily_standup": "Asynchronous standup nudge with lane status.",
        "tech_debt": "Triage and size technical-debt work for upcoming cycles.",
    }
    if lane_id in known:
        return known[lane_id]
    return _titleize(lane_id)


def _process_health(states: list[ProcessStateOut], blocked_count: int) -> Health:
    if any(state.runtime.health == "failed" for state in states):
        return "failed"
    if blocked_count > 0 or any(state.runtime.health == "degraded" for state in states):
        return "degraded"
    return "ok"


async def _adapter_diagnostics(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[ProcessAdapterDiagnosticOut]:
    repo_count = (
        await session.execute(
            select(WorkspaceRepo).where(WorkspaceRepo.workspace_id == workspace_id).limit(1)
        )
    ).scalars().first()
    return [
        ProcessAdapterDiagnosticOut(
            kind="tracker",
            name="Tracker adapter",
            status="unknown",
            message=(
                "Tracker-specific FSM mapping is not configured in the process "
                "adapter yet; this view uses Ship-managed projection."
            ),
            capabilities=["shadow_state", "inbox_blockers"],
        ),
        ProcessAdapterDiagnosticOut(
            kind="runner",
            name="GitHub Actions runner",
            status="ok" if repo_count else "not_configured",
            message=(
                "Execution windows are projected from existing pipeline runs "
                "and lane callbacks."
            ),
            capabilities=["workflow_dispatch", "callback", "execution_windows"],
        ),
        ProcessAdapterDiagnosticOut(
            kind="agent",
            name="Default agent profile",
            status="unknown",
            message=(
                "Agent backend selection is currently automatic; explicit "
                "AgentProfile support lands in a later phase."
            ),
            capabilities=["auto_select", "pattern_discovery_planned"],
        ),
    ]


def _titleize(value: str) -> str:
    labels = {
        "task_intake": "Intake",
        "ba_requirements": "Requirements",
        "tech_arch_plan": "Solution Plan",
        "qa_arch_plan": "Quality Plan",
        "dev_implementation": "Implementation",
        "qa_manual": "Quality Review",
        "qa_automation": "Automated Checks",
        "pr_review": "Final Review",
    }
    if value in labels:
        return labels[value]
    return value.replace("_", " ").replace("-", " ").title()
