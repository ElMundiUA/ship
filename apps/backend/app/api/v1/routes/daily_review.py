"""Workspace daily review read API."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import ROLES_READ, _require_membership
from backend.app.db.session import get_session
from backend.app.services.daily_review import (
    DailyReview,
    build_daily_review,
    format_daily_review_markdown,
)

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["daily-review"])


class DailyReviewMovementOut(BaseModel):
    ticket_ref: str
    current_stage: str | None
    current_status: str | None
    movement_signal: str
    verified_at: datetime


class DailyReviewStuckItemOut(BaseModel):
    ticket_ref: str | None
    reason: str
    last_verified_at: datetime | None
    detail: str | None = None


class DailyReviewPrItemOut(BaseModel):
    ticket_ref: str | None
    title: str
    url: str
    repo_full_name: str
    awaiting_review: bool
    ci_status_verified: bool
    red_ci: bool
    ci_conclusion: str | None
    ci_url: str | None
    updated_at: datetime


class DailyReviewOut(BaseModel):
    generated_at: datetime
    window_started_at: datetime
    movement: list[DailyReviewMovementOut]
    stuck: list[DailyReviewStuckItemOut]
    pull_requests: list[DailyReviewPrItemOut]
    duplicate_pr_ticket_refs: list[str]
    recommendations: list[str] = Field(max_length=3)
    unverified_sections: list[str]
    markdown: str


@router.get("/daily-review", response_model=DailyReviewOut)
async def get_daily_review(
    workspace_id: uuid.UUID,
    window_hours: int = Query(default=24, ge=1, le=168),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> DailyReviewOut:
    """Return a short, read-only daily review from Ship-owned state."""
    await _require_membership(session, workspace_id, auth.user.id, ROLES_READ)
    review = await build_daily_review(
        session,
        workspace_id=workspace_id,
        window_hours=window_hours,
    )
    return _review_to_out(review)


def _review_to_out(review: DailyReview) -> DailyReviewOut:
    return DailyReviewOut(
        generated_at=review.generated_at,
        window_started_at=review.window_started_at,
        movement=[
            DailyReviewMovementOut(
                ticket_ref=item.ticket_ref,
                current_stage=item.current_stage,
                current_status=item.current_status,
                movement_signal=item.movement_signal,
                verified_at=item.verified_at,
            )
            for item in review.movement
        ],
        stuck=[
            DailyReviewStuckItemOut(
                ticket_ref=item.ticket_ref,
                reason=item.reason,
                last_verified_at=item.last_verified_at,
                detail=item.detail,
            )
            for item in review.stuck
        ],
        pull_requests=[
            DailyReviewPrItemOut(
                ticket_ref=item.ticket_ref,
                title=item.title,
                url=item.url,
                repo_full_name=item.repo_full_name,
                awaiting_review=item.awaiting_review,
                ci_status_verified=item.ci_status_verified,
                red_ci=item.red_ci,
                ci_conclusion=item.ci_conclusion,
                ci_url=item.ci_url,
                updated_at=item.updated_at,
            )
            for item in review.pull_requests
        ],
        duplicate_pr_ticket_refs=review.duplicate_pr_ticket_refs,
        recommendations=review.recommendations,
        unverified_sections=review.unverified_sections,
        markdown=format_daily_review_markdown(review),
    )
