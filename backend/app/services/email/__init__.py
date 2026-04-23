"""Transactional email — invites, navigator summaries, future system mail.

The package is intentionally small: a vendor-agnostic
:class:`EmailSender` protocol, three implementations
(``sendgrid`` / ``log`` / ``recording``), and the templates the rest of
the backend needs. Callers never instantiate a sender themselves —
they go through :func:`get_email_sender`, which returns the cached
implementation chosen by :class:`Settings.email_provider`.

Why a thin protocol instead of "just call SendGrid":

- Tests (and the laptop dev loop) must work without credentials. The
  ``log`` provider renders the message exactly the same way the
  SendGrid provider does, then writes the rendered HTML to the app
  log instead of POSTing it. The ``recording`` provider keeps a
  per-process list of sent messages so unit tests can assert on
  them.
- Future transports (Postmark, SES, an in-house SMTP relay) plug in
  by adding one class. The invite and agent code does not change.
- The "send" call is fire-and-forget from the caller's point of view
  (returns an :class:`EmailDeliveryResult` rather than raising);
  routes always queue them through FastAPI ``BackgroundTasks`` so
  the user-facing request returns immediately.
"""

from backend.app.services.email.sender import (
    EmailAddress,
    EmailDeliveryResult,
    EmailMessage,
    EmailSender,
    LogOnlyEmailSender,
    RecordingEmailSender,
    SendGridEmailSender,
    get_email_sender,
    reset_email_sender_cache,
)
from backend.app.services.email.templates import (
    render_invite_email,
    render_navigator_summary_email,
)

__all__ = [
    "EmailAddress",
    "EmailDeliveryResult",
    "EmailMessage",
    "EmailSender",
    "LogOnlyEmailSender",
    "RecordingEmailSender",
    "SendGridEmailSender",
    "get_email_sender",
    "render_invite_email",
    "render_navigator_summary_email",
    "reset_email_sender_cache",
]
