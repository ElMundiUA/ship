"""Scheduled ticket-creating routines (ELS-232/233/234 — trigger c).

The second flavor of time-driven work. ``daily_scheduler`` fires
WORKSPACE-SCOPE bundles (no ticket); this module CREATES a Linear
ticket in a target FSM stage so the already-shipped
ticket → tracker_poller → dispatcher → ``shipctl run`` path executes
it — no new dispatch code, the cron is just an intake clerk.

Idempotency (ELS-232): creation is gated on the audit_log-as-ledger
pattern the cascade counter uses — a durable
``scheduled_routine.ticket_created`` row per ``(kind, period_key)``;
two ticks inside one period create at most one ticket, and the guard
survives leader failover because audit_log is Postgres.

Launch posture (founder default): per-workspace OPT-IN. A workspace
without ``settings.scheduled_routines.<kind> = true`` is skipped
fail-closed — the cron never writes tickets into a tracker nobody
asked it to.

Templates (ELS-234): each spec renders a self-contained brief — the
downstream stage agents (planning/dev) must be able to act on the
ticket body alone, with no reliance on the cron run's transient
context. Templates never instruct the agent to bypass the FSM (no
direct merges, no stage skipping); the 'use Ship's API, never reach
into Linear via MCP' guardrail mirrors daily-digest.md.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.integrations import NativeIntegrationInstallation
from backend.app.db.models.tenancy import AuditLog, Workspace
from backend.app.db.session import get_sessionmaker

log = logging.getLogger(__name__)

TICKET_CREATED_ACTION = "scheduled_routine.ticket_created"


@dataclass(frozen=True)
class ScheduledRoutineSpec:
    kind: str  # 'daily' | 'retro' | 'techdebt'
    cron_expr: str
    period: str  # 'day' | 'week' — drives period_key granularity
    target_fsm_stage: str  # stage label the created ticket carries
    title_template: str  # .format(period_key=, workspace=)
    body_template: str


SPECS: dict[str, ScheduledRoutineSpec] = {
    s.kind: s
    for s in (
        ScheduledRoutineSpec(
            kind="daily",
            cron_expr="30 6 * * 1-5",  # weekday mornings UTC
            period="day",
            target_fsm_stage="planning",
            title_template="Daily review — {period_key}",
            body_template=(
                "## Daily review ({period_key})\n\n"
                "Walk the workspace's in-flight work and produce a short, "
                "self-contained status review as this ticket's deliverable:\n\n"
                "1. List tickets that moved stages in the last 24h and any "
                "that look stuck (no movement, blocked label).\n"
                "2. Flag PRs awaiting review or with red CI.\n"
                "3. Propose at most three concrete next actions.\n\n"
                "Work through Ship's own API surface (the run context you "
                "are handed) — do NOT reach into Linear via MCP or any "
                "side channel. Do not transition other tickets, do not "
                "merge anything; your output is the review on THIS ticket. "
                "The normal FSM gates apply to this ticket itself."
            ),
        ),
        ScheduledRoutineSpec(
            kind="retro",
            cron_expr="0 16 * * 5",  # Friday afternoon UTC
            period="week",
            target_fsm_stage="planning",
            title_template="Weekly retro — {period_key}",
            body_template=(
                "## Weekly retrospective ({period_key})\n\n"
                "Look back over the week's merged PRs, blocked tickets and "
                "agent-run failures, and produce a self-contained retro:\n\n"
                "1. What shipped (merged work, by theme).\n"
                "2. What got stuck and why (blocked/rework loops, gate "
                "refusals).\n"
                "3. At most three process improvements, each phrased as a "
                "follow-up ticket draft (title + 2-3 sentence body).\n\n"
                "Use Ship's API surface only — never Linear via MCP. Do not "
                "create the follow-up tickets yourself and do not bypass "
                "any FSM stage; the human picks which drafts to file."
            ),
        ),
        ScheduledRoutineSpec(
            kind="techdebt",
            cron_expr="0 7 * * 1",  # Monday morning UTC
            period="week",
            target_fsm_stage="planning",
            title_template="Tech-debt sweep — {period_key}",
            body_template=(
                "## Tech-debt sweep ({period_key})\n\n"
                "Survey the repo for accumulating debt and produce a "
                "prioritised, self-contained report:\n\n"
                "1. TODO/FIXME/xfail/skip markers older than 30 days.\n"
                "2. Modules with rising churn + failing/ignored tests.\n"
                "3. Dependency pins overdue for a bump (security first).\n"
                "4. Top three debt items as ticket drafts (title + why now "
                "+ rough size).\n\n"
                "Report only — do not refactor on this ticket, do not merge "
                "anything, do not skip FSM stages. Use Ship's API surface, "
                "never Linear via MCP."
            ),
        ),
    )
}


def period_key_for(spec: ScheduledRoutineSpec, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if spec.period == "day":
        return now.strftime("%Y-%m-%d")
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _ledger_target(kind: str, period_key: str) -> str:
    return f"{kind}:{period_key}"


async def already_created(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    period_key: str,
) -> bool:
    """Durable idempotency guard — audit_log as the ledger."""
    count = await session.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.workspace_id == workspace_id,
            AuditLog.action == TICKET_CREATED_ACTION,
            AuditLog.target_id == _ledger_target(kind, period_key),
        )
    )
    return bool(count)


def _workspace_opted_in(workspace: Workspace | None, kind: str) -> bool:
    """Founder default: fail-closed opt-in per workspace + kind."""
    if workspace is None:
        return False
    raw = (workspace.settings or {}).get("scheduled_routines")
    if not isinstance(raw, dict):
        return False
    return raw.get(kind) is True


async def create_routine_ticket(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    spec: ScheduledRoutineSpec,
    now: datetime | None = None,
) -> str | None:
    """Create one routine ticket for one workspace (idempotent per
    period). Returns the created ticket ref, or None on skip."""
    from backend.app.core.config import get_settings
    from backend.app.services.tracker_resolver import resolve_for_workspace

    now = now or datetime.now(timezone.utc)
    period_key = period_key_for(spec, now)

    workspace = await session.get(Workspace, workspace_id)
    if not _workspace_opted_in(workspace, spec.kind):
        return None
    if await already_created(
        session, workspace_id=workspace_id, kind=spec.kind, period_key=period_key
    ):
        log.info(
            "scheduled_routine %s: ws=%s period=%s already created — skip",
            spec.kind, workspace_id, period_key,
        )
        return None

    resolved = await resolve_for_workspace(
        session=session, settings=get_settings(), workspace_id=workspace_id
    )
    if resolved is None:
        return None

    created = await resolved.gateway.create_ticket(
        title=spec.title_template.format(period_key=period_key),
        body=spec.body_template.format(period_key=period_key),
        labels=[f"stage:{spec.target_fsm_stage}"],
    )
    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=None,
            actor_token_id=None,
            action=TICKET_CREATED_ACTION,
            target_kind="ticket",
            target_id=_ledger_target(spec.kind, period_key),
            payload={
                "routine_kind": spec.kind,
                "period_key": period_key,
                "ticket_ref": created.display_id or created.ref.id,
                "target_fsm_stage": spec.target_fsm_stage,
            },
        )
    )
    await session.flush()
    return created.display_id or created.ref.id


async def run_routine_for_all_workspaces(kind: str) -> None:
    """One tick: create the period's ticket for every eligible
    (ready-Linear + opted-in) workspace. Per-workspace failures are
    isolated, matching daily_scheduler."""
    spec = SPECS[kind]
    sm = get_sessionmaker()
    async with sm() as session:
        installs = (
            await session.execute(
                select(NativeIntegrationInstallation).where(
                    NativeIntegrationInstallation.provider == "linear",
                    NativeIntegrationInstallation.status == "ready",
                )
            )
        ).scalars().all()
        created = 0
        skipped = 0
        for install in installs:
            try:
                ref = await create_routine_ticket(
                    session, workspace_id=install.workspace_id, spec=spec
                )
                await session.commit()
            except Exception:  # noqa: BLE001 — one ws must not abort the tick
                await session.rollback()
                log.exception(
                    "scheduled_routine %s: ws=%s failed",
                    kind, install.workspace_id,
                )
                continue
            if ref:
                created += 1
            else:
                skipped += 1
        log.info(
            "scheduled_routine %s tick: installs=%d created=%d skipped=%d",
            kind, len(installs), created, skipped,
        )


__all__ = [
    "SPECS",
    "ScheduledRoutineSpec",
    "TICKET_CREATED_ACTION",
    "already_created",
    "create_routine_ticket",
    "period_key_for",
    "run_routine_for_all_workspaces",
]
