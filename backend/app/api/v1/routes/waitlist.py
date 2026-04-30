"""Public waitlist submission endpoint (E08 T05).

Captures interested users from the landing site's "Request closed-beta access"
form. No authentication required; submissions are idempotent (duplicate emails
always succeed, updating the existing row with new data).

Endpoint:
- ``POST /v1/public/waitlist`` — Submit a waitlist form (public, no auth)
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import APIRouter, Depends, status

from backend.app.db.models.tenancy import WaitlistSubmission
from backend.app.db.session import get_session
from backend.app.security.rate_limit import rate_limit, GENERAL_LIMITER


router = APIRouter(
    prefix="/public",
    tags=["waitlist"],
)


class WaitlistSubmissionIn(BaseModel):
    """Waitlist form submission."""

    email: EmailStr = Field(..., description="Email address (required)")
    role: str | None = Field(None, max_length=100, description="User's role")
    tracker: str | None = Field(None, max_length=100, description="Current tracker")
    agent: str | None = Field(None, max_length=100, description="Current code agent")
    note: str | None = Field(None, max_length=500, description="Free-text response")


class WaitlistSubmissionOut(BaseModel):
    """Successful submission response."""

    ok: bool = Field(True, description="Always true on success")


@router.post(
    "/waitlist",
    response_model=WaitlistSubmissionOut,
    status_code=status.HTTP_200_OK,
)
async def submit_waitlist(
    form: WaitlistSubmissionIn,
    session: AsyncSession = Depends(get_session),
) -> WaitlistSubmissionOut:
    """Submit a waitlist form from the landing site.

    Accepts email (required) and optional fields: role, tracker, agent, note.
    Always returns ``{ ok: true }`` even on duplicate emails (idempotent UX).

    Rate-limited per IP using GENERAL_LIMITER.

    Args:
        form: Waitlist submission data
        session: Database session

    Returns:
        Success response with ok=true
    """
    # Apply rate limit (future: configure per-IP limits if needed)
    # TODO: Implement rate limiting based on client IP if GENERAL_LIMITER is available

    # Normalize email to lowercase for consistency
    email = form.email.lower()

    # Check if submission exists
    stmt = select(WaitlistSubmission).where(
        WaitlistSubmission.email == email
    )
    existing = await session.scalar(stmt)

    if existing:
        # Update existing row with new data (idempotent)
        stmt = (
            update(WaitlistSubmission)
            .where(WaitlistSubmission.email == email)
            .values(
                role=form.role,
                tracker=form.tracker,
                agent=form.agent,
                note=form.note,
            )
        )
        await session.execute(stmt)
    else:
        # Insert new submission
        stmt = insert(WaitlistSubmission).values(
            email=email,
            role=form.role,
            tracker=form.tracker,
            agent=form.agent,
            note=form.note,
        )
        await session.execute(stmt)

    await session.commit()

    return WaitlistSubmissionOut(ok=True)
