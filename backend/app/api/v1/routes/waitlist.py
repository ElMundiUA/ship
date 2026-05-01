"""Public waitlist submission endpoint (E08 T05).

Captures interested users from the landing site's "Request closed-beta access"
form. No authentication required; submissions are idempotent (duplicate emails
always succeed, updating the existing row with new data).

After every successful submission the endpoint also fires a fire-and-forget
notification email to the operators listed in :data:`_NOTIFY_RECIPIENTS` so
the team learns about new applicants without polling the DB. The email is
sent via FastAPI ``BackgroundTasks`` and uses the workspace ``EmailSender``
abstraction (SendGrid in prod, log-only in dev). Recipients are hard-coded
for now; move them to settings when the list grows.

Endpoint:
- ``POST /v1/public/waitlist`` — Submit a waitlist form (public, no auth)
"""

from __future__ import annotations

import html as html_lib
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.tenancy import WaitlistSubmission
from backend.app.db.session import get_session
from backend.app.services.email import (
    EmailAddress,
    EmailMessage,
    get_email_sender,
)


logger = logging.getLogger(__name__)


# Operators that receive a notification email on every waitlist submission.
# Hard-coded for now — small list, moves to settings when it grows beyond
# the founding team. Order does not matter; each recipient gets its own send
# (separate audit row + clean opt-out per address later).
_NOTIFY_RECIPIENTS: tuple[str, ...] = (
    "denys@bodyman.io",
    "abondar@bodyman.io",
)


# NB: no rate limiter on this public endpoint yet — sized for the closed-beta
# trickle. ``backend/app/security/rate_limit.py`` exposes LOGIN/SIGNUP/
# TOKEN_MINT limiters; add a WAITLIST_LIMITER and ``Depends(rate_limit(...))``
# on the route below if traffic warrants it post-beta.


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
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
) -> WaitlistSubmissionOut:
    """Submit a waitlist form from the landing site.

    Accepts email (required) and optional fields: role, tracker, agent, note.
    Always returns ``{ ok: true }`` even on duplicate emails (idempotent UX).

    After the row is persisted, a notification email is scheduled via
    :class:`BackgroundTasks` to every recipient in :data:`_NOTIFY_RECIPIENTS`
    so the operator team sees new submissions without polling the DB.

    Args:
        form: Waitlist submission data
        background_tasks: FastAPI background-task scheduler for the notify hook
        session: Database session

    Returns:
        Success response with ok=true
    """
    # Normalize email to lowercase for consistency
    email = form.email.lower()

    # Check if submission exists
    stmt = select(WaitlistSubmission).where(
        WaitlistSubmission.email == email
    )
    existing = await session.scalar(stmt)
    is_new = existing is None

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

    # Fire-and-forget operator notification. Failures inside the background
    # task land in uvicorn logs but cannot block the response to the
    # applicant. Settings are read inside the task so the right transport is
    # chosen at fire time (matters in tests).
    background_tasks.add_task(
        _notify_operators,
        email=email,
        role=form.role,
        tracker=form.tracker,
        agent=form.agent,
        note=form.note,
        is_new=is_new,
    )

    return WaitlistSubmissionOut(ok=True)


async def _notify_operators(
    *,
    email: str,
    role: str | None,
    tracker: str | None,
    agent: str | None,
    note: str | None,
    is_new: bool,
) -> None:
    """Send a notification email to each operator in :data:`_NOTIFY_RECIPIENTS`.

    Runs inside :class:`BackgroundTasks`. Builds one message per recipient
    (rather than putting them all on the same ``to`` line) so each delivery
    has its own audit record and the BCC-style opt-out path stays clean.
    Errors are logged but never raised.
    """
    settings = get_settings()
    provider = (settings.email_provider or "log").lower().strip()
    if provider == "none":
        logger.debug("waitlist.notify skipped (provider=none) email=%s", email)
        return

    sender = get_email_sender(settings)
    subject = (
        f"[Ship beta] {'New' if is_new else 'Updated'} waitlist submission: {email}"
    )
    rendered_html = _render_notification_html(
        email=email,
        role=role,
        tracker=tracker,
        agent=agent,
        note=note,
        is_new=is_new,
    )
    rendered_text = _render_notification_text(
        email=email,
        role=role,
        tracker=tracker,
        agent=agent,
        note=note,
        is_new=is_new,
    )

    for recipient in _NOTIFY_RECIPIENTS:
        message = EmailMessage(
            to=EmailAddress(email=recipient),
            subject=subject,
            html=rendered_html,
            text=rendered_text,
            tags={
                "kind": "waitlist_notify",
                "applicant_email": email,
                "is_new": str(is_new).lower(),
            },
        )
        try:
            result = await sender.send(message)
        except Exception:  # noqa: BLE001 — defensive; sender normally swallows
            logger.exception(
                "waitlist.notify send raised recipient=%s applicant=%s",
                recipient,
                email,
            )
            continue
        if not result.sent:
            logger.warning(
                "waitlist.notify failed recipient=%s applicant=%s detail=%s",
                recipient,
                email,
                result.detail,
            )
        else:
            logger.info(
                "waitlist.notify sent recipient=%s applicant=%s message_id=%s",
                recipient,
                email,
                result.message_id,
            )


def _render_notification_html(
    *,
    email: str,
    role: str | None,
    tracker: str | None,
    agent: str | None,
    note: str | None,
    is_new: bool,
) -> str:
    """Tiny HTML body for the operator notification.

    Inline styles only — most ops mail clients strip ``<style>`` blocks.
    Field values are HTML-escaped because they come from a public form.
    """
    rows = [
        ("Email", email),
        ("Role", role or "—"),
        ("Tracker", tracker or "—"),
        ("Agent", agent or "—"),
        ("Note", note or "—"),
    ]
    body_rows = "".join(
        f'<tr><td style="padding:6px 12px;color:#888;font-size:12px;text-transform:uppercase;letter-spacing:0.08em;">{html_lib.escape(label)}</td>'
        f'<td style="padding:6px 12px;color:#111;font-size:14px;">{html_lib.escape(str(value))}</td></tr>'
        for label, value in rows
    )
    headline = "New waitlist submission" if is_new else "Waitlist submission updated"
    return (
        '<!DOCTYPE html><html><body style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;'
        'background:#f6f6f6;margin:0;padding:24px;">'
        '<table role="presentation" style="max-width:560px;margin:0 auto;background:#fff;'
        'border-radius:8px;border:1px solid #e0e0e0;border-collapse:collapse;width:100%;">'
        '<tr><td style="padding:20px 24px 8px 24px;">'
        f'<p style="margin:0;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.18em;color:#0aa;">Ship · waitlist</p>'
        f'<h1 style="margin:8px 0 0 0;font-size:20px;color:#111;">{headline}</h1>'
        '</td></tr>'
        '<tr><td style="padding:8px 24px 20px 24px;">'
        f'<table role="presentation" style="border-collapse:collapse;width:100%;">{body_rows}</table>'
        '</td></tr>'
        '</table></body></html>'
    )


def _render_notification_text(
    *,
    email: str,
    role: str | None,
    tracker: str | None,
    agent: str | None,
    note: str | None,
    is_new: bool,
) -> str:
    """Plain-text alternative — required by spam filters and mail clients."""
    headline = "New waitlist submission" if is_new else "Waitlist submission updated"
    lines = [
        f"Ship · {headline}",
        "",
        f"Email:   {email}",
        f"Role:    {role or '—'}",
        f"Tracker: {tracker or '—'}",
        f"Agent:   {agent or '—'}",
        f"Note:    {note or '—'}",
    ]
    return "\n".join(lines)
