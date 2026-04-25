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
from backend.app.db.models.lanes import Lane
from backend.app.db.models.pipelines import Pipeline, PipelineRun
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.services.agent.tools import ToolBox, ToolInvocationError
from backend.app.services.inbox.dual_write import mirror_clarification_create


router = APIRouter(
    prefix="/workspaces/{workspace_id}/processes",
    tags=["processes"],
)

Health = Literal["ok", "degraded", "failed"]
TaskStatus = Literal["active", "blocked", "done"]
ProcessLinkType = Literal["handoff", "dependency", "approval", "notification"]

PRIMARY_PROCESS_ID = "development"

_PROCESS_STATE_ORDER: tuple[str, ...] = (
    "task_intake",
    "ba_requirements",
    "tech_arch_plan",
    "qa_arch_plan",
    "dev_implementation",
    "qa_manual",
    "qa_automation",
    "pr_review",
)

_ROUTINE_IDS: frozenset[str] = frozenset(
    {
        "daily_architecture_tests_review",
        "daily_technical_architecture_review",
        "daily_security_review",
        "daily_digest",
        "daily_retro",
        "self_heal",
        "daily_standup",
        "tech_debt",
    }
)


class ProcessConditionOut(BaseModel):
    expression: str


class ProcessTriggerOut(BaseModel):
    type: Literal["schedule", "event", "manual"]
    interval: str | None = None
    event: str | None = None


class ProcessStateRuntimeOut(BaseModel):
    task_count: int = 0
    blocked_count: int = 0
    last_execution_time: datetime | None = None
    health: Health = "ok"


class ProcessStateOut(BaseModel):
    id: str
    name: str
    specialist_id: str
    specialist_name: str
    instructions: str
    triggers: list[ProcessTriggerOut] = Field(default_factory=list)
    exit_conditions: list[ProcessConditionOut] = Field(default_factory=list)
    block_conditions: list[ProcessConditionOut] = Field(default_factory=list)
    runtime: ProcessStateRuntimeOut = Field(default_factory=ProcessStateRuntimeOut)


class ProcessTransitionOut(BaseModel):
    id: str
    from_state_id: str
    to_state_id: str
    conditions: list[ProcessConditionOut] = Field(default_factory=list)


class ProcessSpecialistOut(BaseModel):
    id: str
    name: str
    role: str
    capabilities: list[str] = Field(default_factory=list)
    agent_profile: str = "auto"


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
    instructions: str
    last_run: datetime | None = None
    status: str | None = None


class ProcessLinkOut(BaseModel):
    id: str
    from_process_id: str
    from_state_id: str | None = None
    to_process_id: str
    to_state_id: str | None = None
    type: ProcessLinkType
    conditions: list[ProcessConditionOut] = Field(default_factory=list)


class ProcessGraphOut(BaseModel):
    links: list[ProcessLinkOut] = Field(default_factory=list)


class ProcessSummaryOut(BaseModel):
    id: str
    name: str
    primary: bool
    state_count: int
    task_count: int
    blocked_count: int
    health: Health


class ProcessAdapterDiagnosticOut(BaseModel):
    kind: Literal["tracker", "runner", "agent"]
    name: str
    status: Literal["ok", "degraded", "not_configured", "unknown"]
    message: str
    capabilities: list[str] = Field(default_factory=list)


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
    return ProcessListOut(
        primary_process_id=PRIMARY_PROCESS_ID,
        processes=[
            ProcessSummaryOut(
                id=process.id,
                name=process.name,
                primary=process.primary,
                state_count=process.state_count,
                task_count=process.task_count,
                blocked_count=process.blocked_count,
                health=process.health,
            )
        ],
        process_graph=process.process_graph,
        adapter_diagnostics=process.adapter_diagnostics,
    )


@router.get("/adapters", response_model=list[ProcessAdapterDiagnosticOut])
async def get_process_adapters(
    workspace_id: uuid.UUID,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> list[ProcessAdapterDiagnosticOut]:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    return await _adapter_diagnostics(session, workspace_id)


@router.get("/{process_id}", response_model=ProcessOut)
async def get_process(
    workspace_id: uuid.UUID,
    process_id: str,
    repo_id: uuid.UUID | None = Query(default=None),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> ProcessOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    if process_id != PRIMARY_PROCESS_ID:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="process not found",
        )
    return await _build_development_process(session, workspace_id, repo_id=repo_id)


@router.get("/{process_id}/tickets", response_model=ProcessTicketPickerOut)
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
        raw = await toolbox._tool_list_tickets(  # noqa: SLF001 - read-only reuse of the agent gateway
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


@router.post("/{process_id}/exits", response_model=ProcessExitIntentOut)
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


async def _build_development_process(
    session: AsyncSession, workspace_id: uuid.UUID, repo_id: uuid.UUID | None = None
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

    lane_stmt = select(Lane).where(Lane.workspace_id == workspace_id)
    if repo_id is not None:
        lane_stmt = lane_stmt.where(Lane.repo_id == repo_id)
    lanes = list(
        (
            await session.execute(
                lane_stmt.order_by(Lane.created_at)
            )
        )
        .scalars()
        .all()
    )

    pipeline_stmt = select(Pipeline).where(Pipeline.workspace_id == workspace_id)
    if repo_id is not None:
        pipeline_stmt = pipeline_stmt.where(Pipeline.repo_id == repo_id)
    pipelines = list(
        (
            await session.execute(
                pipeline_stmt.order_by(Pipeline.created_at)
            )
        )
        .scalars()
        .all()
    )
    pipeline_ids = {pipeline.id for pipeline in pipelines}
    if repo_id is not None and not pipeline_ids:
        runs = []
    else:
        run_stmt = select(PipelineRun).where(PipelineRun.workspace_id == workspace_id)
        if repo_id is not None:
            run_stmt = run_stmt.where(PipelineRun.pipeline_id.in_(pipeline_ids))
        runs = list(
            (
                await session.execute(
                    run_stmt.order_by(
                        desc(PipelineRun.started_at),
                        desc(PipelineRun.created_at),
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
    pipeline_by_id = {p.id: p for p in pipelines}
    lane_by_uuid = {lane.id: lane for lane in lanes}
    lane_keys = _state_lane_ids(lanes, pipelines)
    specialists = _specialists()
    state_runtime = _runtime_by_state(lane_keys, runs, pipeline_by_id, lane_by_uuid)
    blocked_by_state = _blocked_counts(lane_keys, inbox_items)

    states: list[ProcessStateOut] = []
    routines: list[ProcessRoutineOut] = []
    for lane_key in lane_keys:
        lane = next((row for row in lanes if row.lane_id == lane_key), None)
        pipeline = next((row for row in pipelines if row.lane_id == lane_key), None)
        specialist_id = _specialist_for_lane(lane_key)
        runtime = state_runtime[lane_key]
        runtime.blocked_count = blocked_by_state[lane_key]
        if runtime.blocked_count > 0 and runtime.health == "ok":
            runtime.health = "degraded"
        if lane_key not in _PROCESS_STATE_ORDER or lane_key in _ROUTINE_IDS:
            routines.append(
                ProcessRoutineOut(
                    id=lane_key,
                    name=_titleize(lane_key),
                    specialist_id=specialist_id,
                    specialist_name=specialists[specialist_id].name,
                    schedule=lane.cron if lane else _cron_from_pipeline(pipeline),
                    instructions=_routine_instructions(lane_key),
                    last_run=runtime.last_execution_time,
                    status=(lane.last_run_status if lane else pipeline.last_run_status if pipeline else None),
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
                triggers=_triggers_for(lane, pipeline),
                exit_conditions=[ProcessConditionOut(expression="state_complete == true")],
                block_conditions=[ProcessConditionOut(expression="requires_human_input == true")],
                runtime=runtime,
            )
        )

    if not states:
        states = _default_states(specialists)

    transitions = [
        ProcessTransitionOut(
            id=f"{left.id}_to_{right.id}",
            from_state_id=left.id,
            to_state_id=right.id,
            conditions=[ProcessConditionOut(expression="exit_conditions_met == true")],
        )
        for left, right in zip(states, states[1:])
    ]
    tasks = _tasks_for(states, runs, inbox_items, pipeline_by_id, repos)
    state_count = len(states)
    task_count = len(tasks)
    blocked_count = sum(1 for task in tasks if task.status == "blocked")
    health = _process_health(states, blocked_count)
    adapter_diagnostics = await _adapter_diagnostics(session, workspace_id)

    return ProcessOut(
        id=PRIMARY_PROCESS_ID,
        name="Development Process",
        primary=True,
        state_count=state_count,
        task_count=task_count,
        blocked_count=blocked_count,
        health=health,
        specialists=list(specialists.values()),
        states=states,
        transitions=transitions,
        tasks=tasks,
        routines=routines,
        process_graph=ProcessGraphOut(),
        adapter_diagnostics=adapter_diagnostics,
    )


def _state_lane_ids(lanes: list[Lane], pipelines: list[Pipeline]) -> list[str]:
    seen = {row.lane_id for row in lanes} | {row.lane_id for row in pipelines}
    ordered = list(_PROCESS_STATE_ORDER)
    extras = sorted(seen - set(ordered))
    return ordered + extras


def _specialists() -> dict[str, ProcessSpecialistOut]:
    rows = [
        ProcessSpecialistOut(
            id="intake",
            name="Intake specialist",
            role="Clarifies incoming work and validates minimum context.",
            capabilities=["triage", "clarification", "routing"],
        ),
        ProcessSpecialistOut(
            id="business_analyst",
            name="Business analyst",
            role="Turns requests into requirements and acceptance criteria.",
            capabilities=["requirements", "acceptance_criteria", "stakeholder_questions"],
        ),
        ProcessSpecialistOut(
            id="technical_architect",
            name="Technical architect",
            role="Plans architecture, migration strategy, and risk boundaries.",
            capabilities=["architecture", "planning", "risk_review"],
        ),
        ProcessSpecialistOut(
            id="developer",
            name="Developer",
            role="Implements code changes, tests, and PR updates.",
            capabilities=["implementation", "tests", "pull_requests"],
        ),
        ProcessSpecialistOut(
            id="qa_engineer",
            name="QA engineer",
            role="Validates acceptance criteria and quality gates.",
            capabilities=["qa", "test_plans", "regression"],
        ),
        ProcessSpecialistOut(
            id="review_owner",
            name="Review owner",
            role="Reviews completed work for correctness, scope, and maintainability.",
            capabilities=["review", "ci_triage", "risk_review"],
        ),
        ProcessSpecialistOut(
            id="devops_platform",
            name="DevOps/platform",
            role="Handles runtime, CI, deployment, and operational health.",
            capabilities=["ci", "deployment", "infrastructure"],
        ),
    ]
    return {row.id: row for row in rows}


def _specialist_for_lane(lane_id: str) -> str:
    if "intake" in lane_id:
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
        return "review_owner"
    return "devops_platform"


def _runtime_by_state(
    lane_keys: list[str],
    runs: list[PipelineRun],
    pipeline_by_id: dict[uuid.UUID, Pipeline],
    lane_by_uuid: dict[uuid.UUID, Lane],
) -> dict[str, ProcessStateRuntimeOut]:
    out = defaultdict(ProcessStateRuntimeOut)
    for key in lane_keys:
        out[key] = ProcessStateRuntimeOut()
    for run in runs:
        lane_key = _lane_key_for_run(run, pipeline_by_id, lane_by_uuid)
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
    run: PipelineRun,
    pipeline_by_id: dict[uuid.UUID, Pipeline],
    lane_by_uuid: dict[uuid.UUID, Lane],
) -> str | None:
    if run.lane_id and run.lane_id in lane_by_uuid:
        return lane_by_uuid[run.lane_id].lane_id
    pipeline = pipeline_by_id.get(run.pipeline_id)
    return pipeline.lane_id if pipeline else None


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
    runs: list[PipelineRun],
    inbox_items: list[InboxItem],
    pipeline_by_id: dict[uuid.UUID, Pipeline],
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
        pipeline = pipeline_by_id.get(run.pipeline_id)
        state_id = pipeline.lane_id if pipeline else fallback
        if state_id not in state_ids:
            state_id = fallback
        status_value: TaskStatus = (
            "done"
            if run.status in {"succeeded", "success", "ok"}
            else "blocked"
            if run.status in {"failed", "error", "cancelled"}
            else "active"
        )
        repo_name = repos.get(pipeline.repo_id) if pipeline and pipeline.repo_id else None
        tasks.append(
            ProcessTaskOut(
                id=str(run.id),
                title=_run_title(run, pipeline),
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


def _run_title(run: PipelineRun, pipeline: Pipeline | None) -> str:
    base = _titleize(pipeline.lane_id) if pipeline else "Execution window"
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
        ("pr_review", "review_owner"),
    ]
    return [
        ProcessStateOut(
            id=state_id,
            name=_titleize(state_id),
            specialist_id=specialist_id,
            specialist_name=specialists[specialist_id].name,
            instructions=_state_instructions(state_id),
            triggers=[ProcessTriggerOut(type="manual")],
            exit_conditions=[ProcessConditionOut(expression="state_complete == true")],
            block_conditions=[ProcessConditionOut(expression="requires_human_input == true")],
        )
        for state_id, specialist_id in rows
    ]


def _triggers_for(
    lane: Lane | None, pipeline: Pipeline | None
) -> list[ProcessTriggerOut]:
    raw = lane.config_blob if lane else pipeline.config if pipeline else {}
    if lane and lane.kind == "schedule":
        return [ProcessTriggerOut(type="schedule", interval=lane.cron)]
    if lane and lane.kind == "event":
        event = raw.get("trigger") or raw.get("event") or raw.get("on")
        return [ProcessTriggerOut(type="event", event=str(event or "event"))]
    if lane and lane.kind == "once":
        return [ProcessTriggerOut(type="manual")]
    return [ProcessTriggerOut(type="manual")]


def _cron_from_pipeline(pipeline: Pipeline | None) -> str | None:
    if not pipeline:
        return None
    raw = pipeline.config or {}
    cron = raw.get("cron") or raw.get("schedule")
    return str(cron) if cron else None


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
