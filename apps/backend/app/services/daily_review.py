"""Read-only daily workspace review aggregation.

The review intentionally composes Ship-owned control-plane state only:
audit rows, dispatch locks, and cached GitHub webhook rows. It does not
call tracker or GitHub write APIs and it does not mutate local state.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.pipelines import PullRequest, WorkflowRun
from backend.app.db.models.tenancy import AuditLog
from backend.app.services.engine_health import assess_engine_health

_TICKET_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")
_RED_CI_CONCLUSIONS = {"action_required", "cancelled", "failure", "timed_out"}


@dataclass(frozen=True, slots=True)
class DailyReviewMovement:
    ticket_ref: str
    current_stage: str | None
    current_status: str | None
    movement_signal: str
    verified_at: datetime


@dataclass(frozen=True, slots=True)
class DailyReviewStuckItem:
    ticket_ref: str | None
    reason: str
    last_verified_at: datetime | None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DailyReviewPrItem:
    ticket_ref: str | None
    title: str
    url: str
    repo_full_name: str
    awaiting_review: bool
    red_ci: bool
    ci_conclusion: str | None
    ci_url: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DailyReview:
    generated_at: datetime
    window_started_at: datetime
    movement: list[DailyReviewMovement]
    stuck: list[DailyReviewStuckItem]
    pull_requests: list[DailyReviewPrItem]
    duplicate_pr_ticket_refs: list[str]
    recommendations: list[str]
    unverified_sections: list[str]


async def build_daily_review(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    now: datetime | None = None,
    window_hours: int = 24,
) -> DailyReview:
    """Build one workspace review from cached/read-only Ship data."""
    generated_at = _aware(now or datetime.now(timezone.utc))
    window_started_at = generated_at - timedelta(hours=window_hours)

    audit_rows = (
        await session.execute(
            select(AuditLog)
            .where(
                AuditLog.workspace_id == workspace_id,
                AuditLog.action.in_(("agent_run.dispatch", "agent_run.finish")),
                AuditLog.created_at >= window_started_at,
            )
            .order_by(desc(AuditLog.created_at), desc(AuditLog.id))
            .limit(200)
        )
    ).scalars().all()

    movement = _movement_from_audit(audit_rows)
    stuck = _stuck_from_recent_finishes(audit_rows)

    engine = await assess_engine_health(
        session,
        workspace_id=workspace_id,
        now=generated_at,
        dispatch_window_minutes=min(window_hours * 60, 24 * 60),
    )
    for stall in engine.stalled:
        stuck.append(
            DailyReviewStuckItem(
                ticket_ref=_ticket_ref_from_lock(stall.lock_key),
                reason=stall.reason,
                last_verified_at=stall.claimed_at,
                detail=f"{stall.lock_key} held for {stall.age_minutes:.1f} minutes",
            )
        )

    pull_requests = await _collect_pull_requests(session, workspace_id=workspace_id)
    duplicate_pr_ticket_refs = _duplicate_ticket_refs(pull_requests)
    recommendations = _recommendations(stuck, pull_requests, duplicate_pr_ticket_refs)

    return DailyReview(
        generated_at=generated_at,
        window_started_at=window_started_at,
        movement=movement,
        stuck=stuck,
        pull_requests=pull_requests,
        duplicate_pr_ticket_refs=duplicate_pr_ticket_refs,
        recommendations=recommendations,
        unverified_sections=[],
    )


def format_daily_review_markdown(review: DailyReview) -> str:
    """Render a short operator-readable daily review."""
    lines = [
        f"## Daily Review ({review.generated_at.date().isoformat()})",
        "",
        "### Movement",
    ]
    if review.movement:
        for item in review.movement:
            status = item.current_status or "status unknown"
            stage = item.current_stage or "stage unknown"
            lines.append(
                f"- {item.ticket_ref}: {status} in {stage}; "
                f"{item.movement_signal} at {item.verified_at.isoformat()}"
            )
    else:
        lines.append("- No verified movement in the last 24 hours.")

    lines.extend(["", "### Stuck Or Blocked"])
    if review.stuck:
        for item in review.stuck:
            ref = item.ticket_ref or "Unknown ticket"
            when = (
                item.last_verified_at.isoformat()
                if item.last_verified_at is not None
                else "timestamp unavailable"
            )
            detail = f" ({item.detail})" if item.detail else ""
            lines.append(f"- {ref}: {item.reason}{detail}; verified {when}.")
    else:
        lines.append("- None found from Ship control-plane data.")

    lines.extend(["", "### PR And CI Attention"])
    attention = [
        item
        for item in review.pull_requests
        if item.awaiting_review or item.red_ci
    ]
    if attention:
        for item in attention:
            flags = []
            if item.awaiting_review:
                flags.append("awaiting review")
            if item.red_ci:
                flags.append(f"CI {item.ci_conclusion or 'red'}")
            ref = f"{item.ticket_ref}: " if item.ticket_ref else ""
            lines.append(f"- {ref}{item.title} ({', '.join(flags)}) - {item.url}")
    else:
        lines.append("- No cached open PRs needing review or red-CI attention.")
    if review.duplicate_pr_ticket_refs:
        refs = ", ".join(review.duplicate_pr_ticket_refs)
        lines.append(f"- Duplicate open PR risk for: {refs}.")

    lines.extend(["", "### Recommendations"])
    if review.recommendations:
        for item in review.recommendations:
            lines.append(f"- {item}")
    else:
        lines.append("- No immediate action recommended from verified Ship data.")

    if review.unverified_sections:
        lines.extend(["", "### Unverified"])
        for section in review.unverified_sections:
            lines.append(f"- {section}")

    return "\n".join(lines)


def _movement_from_audit(rows: list[AuditLog]) -> list[DailyReviewMovement]:
    latest_by_ticket: dict[str, DailyReviewMovement] = {}
    for row in rows:
        payload = row.payload or {}
        ticket_ref = _ticket_ref_from_payload(payload) or _ticket_ref(row.target_id)
        if ticket_ref is None or ticket_ref in latest_by_ticket:
            continue
        if row.action == "agent_run.finish":
            status = _clean(payload.get("outcome"))
            stage = _clean(payload.get("stage_next")) or _clean(payload.get("fsm_stage"))
            signal = "agent_run.finish"
        else:
            status = "dispatched"
            stage = _clean(payload.get("fsm_stage")) or _clean(payload.get("stage"))
            signal = "agent_run.dispatch"
        latest_by_ticket[ticket_ref] = DailyReviewMovement(
            ticket_ref=ticket_ref,
            current_stage=stage,
            current_status=status,
            movement_signal=signal,
            verified_at=_aware(row.created_at),
        )
    return list(latest_by_ticket.values())


def _stuck_from_recent_finishes(rows: list[AuditLog]) -> list[DailyReviewStuckItem]:
    stuck: list[DailyReviewStuckItem] = []
    seen: set[tuple[str | None, str]] = set()
    for row in rows:
        if row.action != "agent_run.finish":
            continue
        payload = row.payload or {}
        outcome = _clean(payload.get("outcome"))
        if outcome not in {"blocked", "needs_clarification"}:
            continue
        ticket_ref = _ticket_ref_from_payload(payload) or _ticket_ref(row.target_id)
        reason = (
            "waiting on clarification"
            if outcome == "needs_clarification"
            else "blocked finish outcome"
        )
        key = (ticket_ref, reason)
        if key in seen:
            continue
        seen.add(key)
        stuck.append(
            DailyReviewStuckItem(
                ticket_ref=ticket_ref,
                reason=reason,
                last_verified_at=_aware(row.created_at),
                detail=_clean(payload.get("description"))
                or _clean(payload.get("comment")),
            )
        )
    return stuck


async def _collect_pull_requests(
    session: AsyncSession, *, workspace_id: uuid.UUID
) -> list[DailyReviewPrItem]:
    prs = (
        await session.execute(
            select(PullRequest)
            .where(
                PullRequest.workspace_id == workspace_id,
                PullRequest.state.ilike("open"),
                PullRequest.merged.is_(False),
            )
            .order_by(desc(PullRequest.updated_at))
            .limit(100)
        )
    ).scalars().all()

    items: list[DailyReviewPrItem] = []
    for pr in prs:
        ticket_ref = _ticket_ref(pr.title)
        ci = await _latest_ci_for_pr(
            session,
            workspace_id=workspace_id,
            pr=pr,
            ticket_ref=ticket_ref,
        )
        ci_conclusion = ci.conclusion if ci is not None else None
        items.append(
            DailyReviewPrItem(
                ticket_ref=ticket_ref,
                title=pr.title,
                url=pr.html_url,
                repo_full_name=pr.repo_full_name,
                awaiting_review=not pr.draft,
                red_ci=ci_conclusion in _RED_CI_CONCLUSIONS,
                ci_conclusion=ci_conclusion,
                ci_url=ci.html_url if ci is not None else None,
                updated_at=_aware(pr.updated_at_external or pr.updated_at),
            )
        )
    return items


async def _latest_ci_for_pr(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pr: PullRequest,
    ticket_ref: str | None,
) -> WorkflowRun | None:
    if ticket_ref is None:
        return None
    stmt = (
        select(WorkflowRun)
        .where(
            WorkflowRun.workspace_id == workspace_id,
            WorkflowRun.repo_full_name == pr.repo_full_name,
        )
        .order_by(desc(WorkflowRun.updated_at))
        .limit(1)
    )
    stmt = stmt.where(WorkflowRun.head_branch.ilike(f"%{ticket_ref}%"))
    return (await session.execute(stmt)).scalars().first()


def _duplicate_ticket_refs(items: list[DailyReviewPrItem]) -> list[str]:
    counts: dict[str, int] = {}
    for item in items:
        if item.ticket_ref:
            counts[item.ticket_ref] = counts.get(item.ticket_ref, 0) + 1
    return sorted(ref for ref, count in counts.items() if count > 1)


def _recommendations(
    stuck: list[DailyReviewStuckItem],
    pull_requests: list[DailyReviewPrItem],
    duplicate_pr_ticket_refs: list[str],
) -> list[str]:
    recs: list[str] = []
    if stuck:
        ref = stuck[0].ticket_ref or "the oldest stuck item"
        recs.append(f"Unblock {ref}: {stuck[0].reason}.")
    red = next((pr for pr in pull_requests if pr.red_ci), None)
    if red is not None:
        ref = red.ticket_ref or red.title
        recs.append(f"Fix red CI on {ref}.")
    awaiting = next((pr for pr in pull_requests if pr.awaiting_review), None)
    if awaiting is not None:
        ref = awaiting.ticket_ref or awaiting.title
        recs.append(f"Review {ref}.")
    if duplicate_pr_ticket_refs and len(recs) < 3:
        recs.append(f"Resolve duplicate open PRs for {duplicate_pr_ticket_refs[0]}.")
    return recs[:3]


def _ticket_ref_from_payload(payload: dict[str, Any]) -> str | None:
    for key in ("ticket_ref", "ticket", "id"):
        value = _ticket_ref(payload.get(key))
        if value is not None:
            return value
    return None


def _ticket_ref_from_lock(lock_key: str) -> str | None:
    value = lock_key
    for prefix in ("ticket:", "project:"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    return _ticket_ref(value)


def _ticket_ref(value: Any) -> str | None:
    if value is None:
        return None
    match = _TICKET_RE.search(str(value))
    return match.group(0) if match else None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value
