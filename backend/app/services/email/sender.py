"""Vendor-agnostic email transport for the Ship backend.

Three implementations live in this module:

- :class:`SendGridEmailSender` — Twilio SendGrid, the production
  transport. The official ``sendgrid`` SDK is sync-only, so the
  blocking REST call is offloaded to a worker thread via
  :func:`asyncio.to_thread` with a configurable timeout.
- :class:`LogOnlyEmailSender` — renders nothing extra; logs the
  message at INFO and returns success. Default in dev / tests so
  the full code path runs without credentials.
- :class:`RecordingEmailSender` — same as ``LogOnlyEmailSender`` but
  also keeps an in-memory list of sent messages. Tests substitute
  this in via :func:`get_email_sender` (see ``conftest`` patterns).

All implementations return an :class:`EmailDeliveryResult` instead of
raising so that callers — most of which run inside FastAPI
``BackgroundTasks`` — can audit failures without crashing the
parent request.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol

from backend.app.core.config import Settings, get_settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmailAddress:
    """One ``RFC 5322`` recipient or sender."""

    email: str
    name: str | None = None


@dataclass(slots=True)
class EmailMessage:
    """One outbound message.

    ``html`` is required; ``text`` is optional but strongly
    recommended (SendGrid recommends a plain-text alternative for
    every transactional message; spam filters look for it).
    """

    to: EmailAddress
    subject: str
    html: str
    text: str | None = None
    from_addr: EmailAddress | None = None
    reply_to: EmailAddress | None = None
    # Free-form tags persisted on the audit log alongside the result.
    # Use them for filtering/searching ("kind=invite",
    # "workspace=<uuid>", …); not sent to SendGrid.
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EmailDeliveryResult:
    """Outcome of one send attempt — never raises to the caller."""

    sent: bool
    provider: str
    detail: str | None = None
    # Provider-specific id when one is returned (SendGrid hands us
    # ``X-Message-Id`` on a 202). Useful for tracing later.
    message_id: str | None = None


class EmailSender(Protocol):
    """Minimal contract every transport implements.

    Async to keep the call site trivial inside FastAPI handlers; the
    sync SendGrid SDK is wrapped in :func:`asyncio.to_thread` inside
    its implementation.
    """

    provider: str

    async def send(self, message: EmailMessage) -> EmailDeliveryResult: ...


# ---------------------------------------------------------------------------
# log-only implementation — default in dev/tests
# ---------------------------------------------------------------------------


class LogOnlyEmailSender:
    """Pretends to send; writes a one-liner to the application log.

    Used when ``EMAIL_PROVIDER`` is ``"log"`` (the default) so the
    full call path (template render → ``send`` → audit log) exercises
    in the test suite without any vendor key.
    """

    provider = "log"

    async def send(self, message: EmailMessage) -> EmailDeliveryResult:
        logger.info(
            "email.log_only to=%s subject=%r tags=%s html_bytes=%d",
            message.to.email,
            message.subject,
            message.tags,
            len(message.html.encode("utf-8")),
        )
        return EmailDeliveryResult(
            sent=True, provider=self.provider, detail="logged"
        )


# ---------------------------------------------------------------------------
# no-op kill switch
# ---------------------------------------------------------------------------


class _NullEmailSender:
    """``EMAIL_PROVIDER=none`` — drop everything, return success.

    Distinct from ``log`` for tests that should not even render the
    template (e.g. when asserting that an integration is gated off).
    """

    provider = "none"

    async def send(self, message: EmailMessage) -> EmailDeliveryResult:
        del message
        return EmailDeliveryResult(
            sent=True, provider=self.provider, detail="provider=none"
        )


# ---------------------------------------------------------------------------
# recording implementation — for unit tests
# ---------------------------------------------------------------------------


class RecordingEmailSender:
    """Captures every send into ``self.messages`` for assertions.

    Tests construct one directly and inject it via
    :func:`reset_email_sender_cache` + monkeypatching
    :func:`get_email_sender`. The class also keeps the log-line
    behaviour so a test failure has the message details in the log.
    """

    provider = "recording"

    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> EmailDeliveryResult:
        self.messages.append(message)
        logger.debug(
            "email.recording to=%s subject=%r", message.to.email, message.subject
        )
        return EmailDeliveryResult(
            sent=True,
            provider=self.provider,
            detail="recorded",
            message_id=f"rec-{len(self.messages)}",
        )

    def clear(self) -> None:
        self.messages.clear()


# ---------------------------------------------------------------------------
# SendGrid implementation — production transport
# ---------------------------------------------------------------------------


class SendGridEmailSender:
    """Twilio SendGrid transport.

    The official ``sendgrid`` SDK is sync-only and uses ``urllib3``
    under the hood. Calling it from inside an async handler would
    block the event loop, so :meth:`send` runs the SDK call in a
    worker thread and bounds it with ``email_send_timeout_seconds``.

    On any error (timeout, network, non-2xx) we capture the message
    and return ``sent=False``; we never raise to the caller because
    the route already responded to the user via background task.
    The audit log row carries the failure reason for the operator.
    """

    provider = "sendgrid"

    def __init__(
        self,
        *,
        api_key: str,
        from_email: str,
        from_name: str,
        reply_to: str | None,
        timeout_seconds: float,
    ) -> None:
        if not api_key:
            raise ValueError("SendGridEmailSender requires a non-empty api_key")
        if not from_email:
            raise ValueError("SendGridEmailSender requires a non-empty from_email")
        self._api_key = api_key
        self._from = EmailAddress(email=from_email, name=from_name)
        self._reply_to = (
            EmailAddress(email=reply_to) if reply_to else None
        )
        self._timeout = timeout_seconds

    async def send(self, message: EmailMessage) -> EmailDeliveryResult:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._send_sync, message),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "email.sendgrid timed out to=%s subject=%r after %.1fs",
                message.to.email,
                message.subject,
                self._timeout,
            )
            return EmailDeliveryResult(
                sent=False,
                provider=self.provider,
                detail=f"timeout after {self._timeout:.1f}s",
            )
        except Exception as exc:  # noqa: BLE001 — vendor SDK error surface
            logger.exception(
                "email.sendgrid raised to=%s subject=%r",
                message.to.email,
                message.subject,
            )
            return EmailDeliveryResult(
                sent=False, provider=self.provider, detail=str(exc)
            )

    def _send_sync(self, message: EmailMessage) -> EmailDeliveryResult:
        # Imported lazily so installs that never enable SendGrid
        # don't pay the import cost (and so the module test-imports
        # cleanly when the dependency is missing in some sandbox).
        from sendgrid import SendGridAPIClient  # type: ignore[import-untyped]
        from sendgrid.helpers.mail import (  # type: ignore[import-untyped]
            Email,
            HtmlContent,
            Mail,
            PlainTextContent,
            ReplyTo,
            To,
        )

        from_addr = message.from_addr or self._from
        mail = Mail(
            from_email=Email(from_addr.email, from_addr.name),
            to_emails=To(message.to.email, message.to.name),
            subject=message.subject,
        )
        # Order matters for SendGrid: text/plain MUST be added before
        # text/html or the API drops the plain part with no warning.
        if message.text:
            mail.add_content(PlainTextContent(message.text))
        mail.add_content(HtmlContent(message.html))

        reply_to = message.reply_to or self._reply_to
        if reply_to is not None:
            mail.reply_to = ReplyTo(reply_to.email, reply_to.name)

        client = SendGridAPIClient(self._api_key)
        response = client.send(mail)
        status_code = getattr(response, "status_code", None)
        # SendGrid returns 202 Accepted on success; anything else is
        # a delivery problem and we surface it as a non-sent result.
        if status_code is None or not (200 <= int(status_code) < 300):
            body = getattr(response, "body", b"") or b""
            try:
                body_text = body.decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover — defensive
                body_text = repr(body)
            return EmailDeliveryResult(
                sent=False,
                provider=self.provider,
                detail=f"sendgrid http {status_code}: {body_text[:300]}",
            )
        message_id: str | None = None
        headers = getattr(response, "headers", None)
        if headers is not None:
            try:
                message_id = headers.get("X-Message-Id")  # type: ignore[union-attr]
            except Exception:  # pragma: no cover — header bag shape varies
                message_id = None
        return EmailDeliveryResult(
            sent=True,
            provider=self.provider,
            detail=f"sendgrid http {status_code}",
            message_id=message_id,
        )


# ---------------------------------------------------------------------------
# Factory + cache
# ---------------------------------------------------------------------------


def _build_sender_from_settings(settings: Settings) -> EmailSender:
    """Pick the sender implementation matching ``settings.email_provider``."""
    provider = (settings.email_provider or "log").lower().strip()
    if provider == "sendgrid":
        return SendGridEmailSender(
            api_key=settings.sendgrid_api_key or "",
            from_email=settings.sendgrid_from_email or "",
            from_name=settings.sendgrid_from_name or "Ship",
            reply_to=settings.sendgrid_reply_to,
            timeout_seconds=settings.email_send_timeout_seconds,
        )
    if provider == "none":
        return _NullEmailSender()
    return LogOnlyEmailSender()


# Manual cache keyed by ``id(settings)`` because ``Settings`` is a
# mutable ``BaseSettings`` subclass and therefore unhashable; we
# can't lean on ``functools.lru_cache``. ``Settings`` is itself
# cached in ``core.config.get_settings`` so process-wide we end up
# with at most one sender per provider config.
_SENDER_CACHE: dict[int, EmailSender] = {}


def get_email_sender(settings: Settings | None = None) -> EmailSender:
    """Return the configured email sender (cached per ``Settings`` instance)."""
    settings = settings or get_settings()
    cached = _SENDER_CACHE.get(id(settings))
    if cached is None:
        cached = _build_sender_from_settings(settings)
        _SENDER_CACHE[id(settings)] = cached
    return cached


def reset_email_sender_cache() -> None:
    """Drop the cached sender — call after monkeypatching ``Settings``."""
    _SENDER_CACHE.clear()
