"""Workflow runtime executor (W8.3, ELS-258).

Takes a loaded :class:`WorkflowSpec` + a workspace and drives the DAG
to completion: topological scheduling, parallel fan-out + barrier
joins, pipeline output threading, bounded loops, schema-validated
synthesize/judge/verify outputs. BOUNDED by construction — it returns
when the DAG drains or a budget trips, which is exactly what makes it
NOT the FSM (reactive, never completes) and NOT /process (a state-
machine definition).

CRITICAL: this module never spawns anything directly — none of the
spawn primitives (the CLI runtime adapter, the GH workflow-dispatch
helper, the in-process subagent loop) are referenced here. Every leaf
goes through :func:`workflow.gate.gate_step_dispatch` first and is
then executed by the injected :class:`LeafExecutors` (defaults in
:mod:`workflow.leaves`). A grep test pins the chokepoint.

Lean event-driven coding leaves: the executor fires CI dispatch and
returns ``None``; the step stays ``dispatched`` and the run stays
``running`` until the ``/agent-runs/finish`` webhook correlates back
via ``run_id`` (``leaves.complete_coding_step``) and a reconcile tick
calls :func:`advance_workflow` again. All scheduling state lives in
``agent_workflow_step_runs`` — cross-replica correct.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.workflow import AgentWorkflowRun, AgentWorkflowStepRun
from backend.app.services.workflow.gate import (
    GateReason,
    gate_step_dispatch,
    release_step_lock,
)
from backend.app.services.workflow.spec import (
    StepSpec,
    WorkflowSpec,
    WorkflowSpecError,
    validate_output,
)

logger = logging.getLogger(__name__)

# Safety net on scheduling passes per advance call — the DAG is
# statically bounded, so this should never bind; it exists so a
# scheduling bug degrades into "run blocked" instead of a busy loop.
_MAX_PASSES = 50

_TEMPLATE_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.\-]+)\s*\}\}")


def _flatten(spec: WorkflowSpec) -> dict[str, StepSpec]:
    """Index every step (parallel children included) by id. Children
    inherit the parallel container's ``needs`` implicitly."""
    flat: dict[str, StepSpec] = {}

    def walk(steps: list[StepSpec]) -> None:
        for s in steps:
            flat[s.id] = s
            walk(s.steps)

    walk(spec.steps)
    return flat


def _effective_needs(
    spec: WorkflowSpec,
) -> dict[str, list[str]]:
    """Resolve each step's effective dependency list.

    - explicit ``needs`` win;
    - parallel children inherit the container's needs;
    - a parallel CONTAINER completes when all its children do (the
      container itself never executes);
    - top-level steps without ``needs`` depend on nothing (the spec
      author sequences explicitly via needs / barriers).
    """
    needs: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}

    def walk(steps: list[StepSpec], inherited: list[str]) -> None:
        for s in steps:
            own = list(s.needs) if s.needs else list(inherited)
            needs[s.id] = own
            if s.kind == "parallel":
                children[s.id] = [c.id for c in s.steps]
                walk(s.steps, own)

    walk(spec.steps, [])
    # Anything that needs a parallel container actually needs all of
    # its children.
    for sid, dep_list in needs.items():
        expanded: list[str] = []
        for dep in dep_list:
            expanded.extend(children.get(dep, [dep]))
        needs[sid] = expanded
    return needs


def _resolve_templates(value: Any, context: dict[str, Any]) -> Any:
    """Substitute ``{{ inputs.x }}`` / ``{{ steps.<id>.output.<key> }}``
    templates. A template that IS the whole string resolves to the
    raw value (object passing); embedded templates stringify."""
    if isinstance(value, dict):
        return {k: _resolve_templates(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_templates(v, context) for v in value]
    if not isinstance(value, str):
        return value

    def lookup(path: str) -> Any:
        node: Any = context
        for part in path.split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                # steps.axis.correctness.output.findings — step ids may
                # contain dots, so retry greedily against steps.*.
                return _lookup_step_path(path, context)
        return node

    full = _TEMPLATE_RE.fullmatch(value.strip())
    if full:
        return lookup(full.group(1))
    return _TEMPLATE_RE.sub(lambda m: str(lookup(m.group(1))), value)


def _lookup_step_path(path: str, context: dict[str, Any]) -> Any:
    """Handle dotted step ids: ``steps.<id-with-dots>.output.<key>``."""
    if not path.startswith("steps."):
        return None
    rest = path[len("steps."):]
    steps: dict[str, Any] = context.get("steps") or {}
    # Longest matching step id wins.
    for sid in sorted(steps, key=len, reverse=True):
        prefix = f"{sid}."
        if rest.startswith(prefix):
            node: Any = steps[sid]
            for part in rest[len(prefix):].split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    return None
            return node
    return None


def _until_satisfied(until: str | None, output: Any) -> bool:
    """Evaluate the loop predicate: ``output.<key> == <literal>``."""
    if not until:
        return False
    m = re.fullmatch(
        r"\s*output\.([a-zA-Z0-9_]+)\s*==\s*(.+?)\s*", until
    )
    if not m or not isinstance(output, dict):
        return False
    key, literal = m.group(1), m.group(2).strip()
    actual = output.get(key)
    expected: Any = literal.strip("'\"")
    if literal in ("true", "True"):
        expected = True
    elif literal in ("false", "False"):
        expected = False
    elif literal.isdigit():
        expected = int(literal)
    return actual == expected


async def run_workflow(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    spec: WorkflowSpec,
    inputs: dict[str, Any],
    trigger_kind: str,
    triggered_by: str | None = None,
    executors: Any = None,
    settings: Settings | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> AgentWorkflowRun:
    """Single entrypoint the three triggers (chat/gate/cron) call.

    Creates the durable run row and advances the DAG as far as it can
    go synchronously. Reasoning leaves complete in-process; coding
    leaves leave the run ``running`` until the finish webhook +
    reconcile tick complete them.
    """
    run = AgentWorkflowRun(
        workspace_id=workspace_id,
        spec_name=spec.name,
        spec_version=spec.version,
        inputs=inputs,
        trigger_kind=trigger_kind,
        triggered_by=triggered_by,
        status="running",
    )
    session.add(run)
    await session.flush()
    await advance_workflow(
        session,
        run=run,
        spec=spec,
        executors=executors,
        settings=settings,
        actor_user_id=actor_user_id,
    )
    return run


async def advance_workflow(
    session: AsyncSession,
    *,
    run: AgentWorkflowRun,
    spec: WorkflowSpec,
    executors: Any = None,
    settings: Settings | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> AgentWorkflowRun:
    """Advance the DAG: dispatch every READY step, thread outputs,
    finalize when all steps are terminal. Idempotent and resumable —
    the reconcile tick calls this after coding leaves finish."""
    if executors is None:
        from backend.app.services.workflow.leaves import DEFAULT_EXECUTORS

        executors = DEFAULT_EXECUTORS
    settings = settings or get_settings()

    flat = _flatten(spec)
    needs = _effective_needs(spec)
    cascade_key = f"workflow:{run.id}"

    for _pass in range(_MAX_PASSES):
        rows = (
            await session.execute(
                select(AgentWorkflowStepRun).where(
                    AgentWorkflowStepRun.workflow_run_id == run.id
                )
            )
        ).scalars().all()
        # Latest attempt per step decides its state.
        latest: dict[str, AgentWorkflowStepRun] = {}
        for r in sorted(rows, key=lambda r: r.attempt):
            latest[r.step_id] = r

        context: dict[str, Any] = {
            "inputs": dict(run.inputs or {}),
            "steps": {
                sid: {"output": r.output}
                for sid, r in latest.items()
                if r.status == "completed"
            },
        }

        def is_done(sid: str) -> bool:
            r = latest.get(sid)
            return r is not None and r.status == "completed"

        def is_terminal(sid: str) -> bool:
            r = latest.get(sid)
            return r is not None and r.status in (
                "completed", "failed", "blocked", "skipped",
            )

        progressed = False
        pending_async = False

        for sid, step in flat.items():
            if step.kind == "parallel":
                continue  # containers never execute
            row = latest.get(sid)
            if row is not None and row.status in ("dispatched", "running"):
                pending_async = True
                continue
            if row is not None:
                continue  # terminal — done/failed/blocked
            dep_states = [
                (dep, is_done(dep), is_terminal(dep)) for dep in needs[sid]
            ]
            if any(not term for _, _, term in dep_states):
                continue  # a dependency is still in flight
            # All deps terminal. A barrier tolerates partial failure
            # (sees the partial set); other steps require all deps
            # completed — otherwise they are skipped.
            if step.kind != "barrier" and any(
                not done for _, done, _ in dep_states
            ):
                session.add(
                    AgentWorkflowStepRun(
                        workflow_run_id=run.id,
                        step_id=sid,
                        attempt=1,
                        kind=step.kind,
                        status="skipped",
                        reason="dependency_failed",
                        finished_at=datetime.now(timezone.utc),
                    )
                )
                await session.flush()
                progressed = True
                continue

            if step.kind == "barrier":
                session.add(
                    AgentWorkflowStepRun(
                        workflow_run_id=run.id,
                        step_id=sid,
                        attempt=1,
                        kind="barrier",
                        status="completed",
                        output={
                            "joined": [
                                dep for dep, done, _ in dep_states if done
                            ],
                            "failed": [
                                dep for dep, done, _ in dep_states if not done
                            ],
                        },
                        finished_at=datetime.now(timezone.utc),
                    )
                )
                await session.flush()
                progressed = True
                continue

            # Executable leaf (pipeline / loop / synthesize / judge /
            # verify / parallel child).
            resolved_inputs = _resolve_templates(dict(step.inputs), context)
            ran = await _run_leaf(
                session,
                run=run,
                step=step,
                inputs=resolved_inputs,
                cascade_key=cascade_key,
                executors=executors,
                settings=settings,
                actor_user_id=actor_user_id,
            )
            progressed = progressed or ran is not None
            if ran == "async":
                pending_async = True

        if not progressed:
            break

    # Finalize when nothing is left in flight.
    rows = (
        await session.execute(
            select(AgentWorkflowStepRun).where(
                AgentWorkflowStepRun.workflow_run_id == run.id
            )
        )
    ).scalars().all()
    latest = {}
    for r in sorted(rows, key=lambda r: r.attempt):
        latest[r.step_id] = r
    executable_ids = [
        sid for sid, s in flat.items() if s.kind != "parallel"
    ]
    in_flight = any(
        latest.get(sid) is None
        or latest[sid].status in ("dispatched", "running", "pending")
        for sid in executable_ids
    )
    if not in_flight:
        statuses = {latest[sid].status for sid in executable_ids}
        if statuses <= {"completed"}:
            run.status = "completed"
        elif "completed" in statuses:
            run.status = "completed"  # partial failure recorded per-step
        elif "blocked" in statuses:
            run.status = "blocked"
        else:
            run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        # Final output = the last top-level step's output when it is
        # an object.
        last_id = spec.steps[-1].id
        last_row = latest.get(last_id)
        if last_row is not None and isinstance(last_row.output, dict):
            run.output = last_row.output
    await session.flush()
    return run


async def advance_run_by_id(
    run_id: uuid.UUID, *, actor_user_id: uuid.UUID | None = None
) -> None:
    """Background entrypoint for queued runs (chat trigger) and the
    reconcile tick: open a fresh session, load the run + its packaged
    spec, advance. Best-effort — failures land on the run row, never
    on the caller."""
    from backend.app.db.session import get_sessionmaker
    from backend.app.services.workflow.registry import resolve_spec

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            run = await session.get(AgentWorkflowRun, run_id)
            if run is None:
                return
            spec = resolve_spec(run.spec_name)
            if spec is None:
                run.status = "failed"
                run.finished_at = datetime.now(timezone.utc)
                await session.commit()
                return
            if run.status == "queued":
                run.status = "running"
            await advance_workflow(
                session, run=run, spec=spec, actor_user_id=actor_user_id
            )
            await session.commit()
    except Exception:  # noqa: BLE001 — background task must not crash the loop
        logger.exception("workflow %s background advance failed", run_id)


async def _run_leaf(
    session: AsyncSession,
    *,
    run: AgentWorkflowRun,
    step: StepSpec,
    inputs: dict[str, Any],
    cascade_key: str,
    executors: Any,
    settings: Settings,
    actor_user_id: uuid.UUID | None,
) -> str | None:
    """Gate + execute one leaf. Returns 'sync' / 'async' / 'blocked',
    or None when nothing happened."""
    is_coding = step.agent is not None and step.agent.kind == "coding"
    provider = (
        step.agent.provider if step.agent else "reasoning"
    )
    max_iters = step.max_iters if step.kind == "loop" else 1

    last_output: Any = None
    for iteration in range(1, max_iters + 1):
        decision = await gate_step_dispatch(
            session,
            workspace_id=run.workspace_id,
            workflow_run_id=run.id,
            step_id=step.id,
            attempt=iteration,
            kind=step.kind,
            agent_provider=provider,
            cascade_key=cascade_key,
            settings=settings,
        )
        if not decision.granted:
            if decision.reason == GateReason.DUPLICATE_ATTEMPT:
                # Reconcile tick re-walking an already-handled step.
                return None
            return "blocked"

        row = await session.get(AgentWorkflowStepRun, decision.step_row_id)
        runner = executors.run_coding if is_coding else executors.run_reasoning
        try:
            output = await runner(
                session,
                settings,
                run.workspace_id,
                step,
                inputs,
                decision.run_id,
                user_id=actor_user_id,
            )
        except Exception as exc:  # noqa: BLE001 — a leaf failure never aborts siblings
            logger.warning(
                "workflow %s step %s failed: %s", run.id, step.id, exc
            )
            row.status = "failed"
            row.reason = str(exc)[:64]
            row.finished_at = datetime.now(timezone.utc)
            await release_step_lock(
                session,
                workspace_id=run.workspace_id,
                workflow_run_id=run.id,
                step_id=step.id,
            )
            await session.flush()
            return "sync"

        if output is None:
            # Asynchronous coding leaf — finish webhook completes it.
            return "async"

        if step.output_schema:
            try:
                validate_output(output, step.output_schema, step_id=step.id)
            except WorkflowSpecError as exc:
                row.status = "failed"
                row.reason = "schema_invalid"
                row.output = output if isinstance(output, dict) else None
                row.finished_at = datetime.now(timezone.utc)
                await release_step_lock(
                    session,
                    workspace_id=run.workspace_id,
                    workflow_run_id=run.id,
                    step_id=step.id,
                )
                await session.flush()
                logger.warning("workflow %s step %s: %s", run.id, step.id, exc)
                return "sync"

        row.status = "completed"
        row.output = output if isinstance(output, dict) else {"value": output}
        row.finished_at = datetime.now(timezone.utc)
        await release_step_lock(
            session,
            workspace_id=run.workspace_id,
            workflow_run_id=run.id,
            step_id=step.id,
        )
        await session.flush()
        last_output = output

        if step.kind != "loop" or _until_satisfied(step.until, last_output):
            return "sync"
    return "sync"
