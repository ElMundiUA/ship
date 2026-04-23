"""Email transport (Twilio SendGrid) — unit-level coverage.

Three concerns live here, each isolated from the wider Settings cache
to keep the tests deterministic:

- Provider selection. ``EMAIL_PROVIDER=log`` (default) returns the
  log-only sender; ``=none`` returns the kill-switch; ``=sendgrid``
  builds the real SDK-wrapping sender. Misconfigured ``=sendgrid``
  must fail Settings validation, not at first send time.
- :class:`SendGridEmailSender` thread-offloading + error path.
  We monkeypatch the SDK so the test runs offline; we still want
  to confirm the success path returns ``sent=True`` with the
  message id, and the non-2xx + thrown-exception paths return a
  structured failure rather than raising into the caller.
- Templates render to non-empty HTML + plain text, and the inline
  Markdown subset escapes raw HTML so a hostile chat reply can't
  inject markup into the email body.
"""

from __future__ import annotations

import asyncio
import sys
import types
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------


def _reset_caches() -> None:
    from backend.app.core import config as cfg
    from backend.app.services.email import reset_email_sender_cache

    cfg.get_settings.cache_clear()
    reset_email_sender_cache()


def test_default_provider_is_log_only(monkeypatch):
    _reset_caches()
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)

    from backend.app.services.email import LogOnlyEmailSender, get_email_sender

    sender = get_email_sender()
    assert isinstance(sender, LogOnlyEmailSender)
    assert sender.provider == "log"


def test_none_provider_returns_null_sender(monkeypatch):
    _reset_caches()
    monkeypatch.setenv("EMAIL_PROVIDER", "none")

    from backend.app.services.email import get_email_sender

    sender = get_email_sender()
    assert sender.provider == "none"


def test_sendgrid_provider_requires_api_key(monkeypatch):
    """Misconfigured ``EMAIL_PROVIDER=sendgrid`` must fail at Settings validation."""
    _reset_caches()
    monkeypatch.setenv("EMAIL_PROVIDER", "sendgrid")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("SENDGRID_FROM_EMAIL", raising=False)

    from backend.app.core import config as cfg

    with pytest.raises(Exception) as excinfo:
        cfg.get_settings()
    msg = str(excinfo.value)
    assert "SENDGRID_API_KEY" in msg
    assert "SENDGRID_FROM_EMAIL" in msg


def test_sendgrid_provider_builds_with_credentials(monkeypatch):
    _reset_caches()
    monkeypatch.setenv("EMAIL_PROVIDER", "sendgrid")
    monkeypatch.setenv("SENDGRID_API_KEY", "SG.test-key")
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "ops@example.com")

    from backend.app.services.email import SendGridEmailSender, get_email_sender

    sender = get_email_sender()
    assert isinstance(sender, SendGridEmailSender)
    assert sender.provider == "sendgrid"


# ---------------------------------------------------------------------------
# SendGrid implementation — happy path + failure path (offline)
# ---------------------------------------------------------------------------


class _FakeSendGridResponse:
    def __init__(self, status_code: int, body: bytes = b"", message_id: str | None = None):
        self.status_code = status_code
        self.body = body
        self.headers = {"X-Message-Id": message_id} if message_id else {}


def _install_fake_sendgrid_module(monkeypatch, send_impl):
    """Inject a stub ``sendgrid`` package the SDK-wrapping sender can import.

    We can't rely on the real ``sendgrid`` SDK being installed in the
    sandbox; replicating just the import surface keeps the unit test
    hermetic while still exercising the SDK call path, the response
    handling, and the dataclass mapping.
    """
    fake_sendgrid = types.ModuleType("sendgrid")
    fake_helpers = types.ModuleType("sendgrid.helpers")
    fake_mail = types.ModuleType("sendgrid.helpers.mail")

    class _Mail:
        def __init__(self, from_email, to_emails, subject):
            self.from_email = from_email
            self.to_emails = to_emails
            self.subject = subject
            self.contents = []
            self.reply_to = None

        def add_content(self, content):
            self.contents.append(content)

    class _Email:
        def __init__(self, email, name=None):
            self.email = email
            self.name = name

    class _To(_Email):
        pass

    class _ReplyTo(_Email):
        pass

    class _Content:
        def __init__(self, value):
            self.value = value

    class _PlainText(_Content):
        type = "text/plain"

    class _Html(_Content):
        type = "text/html"

    class _Client:
        def __init__(self, api_key):
            self.api_key = api_key

        def send(self, mail):
            return send_impl(mail)

    fake_sendgrid.SendGridAPIClient = _Client
    fake_mail.Mail = _Mail
    fake_mail.Email = _Email
    fake_mail.To = _To
    fake_mail.ReplyTo = _ReplyTo
    fake_mail.PlainTextContent = _PlainText
    fake_mail.HtmlContent = _Html

    monkeypatch.setitem(sys.modules, "sendgrid", fake_sendgrid)
    monkeypatch.setitem(sys.modules, "sendgrid.helpers", fake_helpers)
    monkeypatch.setitem(sys.modules, "sendgrid.helpers.mail", fake_mail)


def test_sendgrid_sender_success(monkeypatch):
    captured: dict = {}

    def fake_send(mail):
        captured["mail"] = mail
        return _FakeSendGridResponse(202, message_id="msg-abc")

    _install_fake_sendgrid_module(monkeypatch, fake_send)

    from backend.app.services.email.sender import (
        EmailAddress,
        EmailMessage,
        SendGridEmailSender,
    )

    sender = SendGridEmailSender(
        api_key="SG.test",
        from_email="ops@example.com",
        from_name="Ship",
        reply_to=None,
        timeout_seconds=5.0,
    )
    msg = EmailMessage(
        to=EmailAddress(email="alice@example.com"),
        subject="Hello",
        html="<p>Hi</p>",
        text="Hi",
    )

    result = asyncio.run(sender.send(msg))
    assert result.sent is True
    assert result.provider == "sendgrid"
    assert result.message_id == "msg-abc"
    assert captured["mail"].subject == "Hello"
    # Plain-text content must be added before HTML (SendGrid quirk).
    assert [c.type for c in captured["mail"].contents] == [
        "text/plain",
        "text/html",
    ]


def test_sendgrid_sender_non_2xx_returns_failure(monkeypatch):
    def fake_send(mail):
        del mail
        return _FakeSendGridResponse(403, body=b"unauthorized sender")

    _install_fake_sendgrid_module(monkeypatch, fake_send)

    from backend.app.services.email.sender import (
        EmailAddress,
        EmailMessage,
        SendGridEmailSender,
    )

    sender = SendGridEmailSender(
        api_key="SG.test",
        from_email="ops@example.com",
        from_name="Ship",
        reply_to=None,
        timeout_seconds=5.0,
    )
    msg = EmailMessage(
        to=EmailAddress(email="alice@example.com"),
        subject="Hello",
        html="<p>Hi</p>",
    )
    result = asyncio.run(sender.send(msg))
    assert result.sent is False
    assert "403" in (result.detail or "")
    assert "unauthorized sender" in (result.detail or "")


def test_sendgrid_sender_swallows_exceptions(monkeypatch):
    def fake_send(mail):
        del mail
        raise RuntimeError("network exploded")

    _install_fake_sendgrid_module(monkeypatch, fake_send)

    from backend.app.services.email.sender import (
        EmailAddress,
        EmailMessage,
        SendGridEmailSender,
    )

    sender = SendGridEmailSender(
        api_key="SG.test",
        from_email="ops@example.com",
        from_name="Ship",
        reply_to=None,
        timeout_seconds=5.0,
    )
    msg = EmailMessage(
        to=EmailAddress(email="alice@example.com"),
        subject="Hello",
        html="<p>Hi</p>",
    )
    result = asyncio.run(sender.send(msg))
    assert result.sent is False
    assert "network exploded" in (result.detail or "")


# ---------------------------------------------------------------------------
# Recording sender + log-only sender behaviour
# ---------------------------------------------------------------------------


def test_recording_sender_captures_messages():
    from backend.app.services.email import (
        EmailAddress,
        EmailMessage,
        RecordingEmailSender,
    )

    sender = RecordingEmailSender()
    msg = EmailMessage(
        to=EmailAddress(email="alice@example.com"),
        subject="Hello",
        html="<p>Hi</p>",
    )
    result = asyncio.run(sender.send(msg))
    assert result.sent is True
    assert sender.messages == [msg]
    sender.clear()
    assert sender.messages == []


# ---------------------------------------------------------------------------
# Templates — render produces dual-body messages
# ---------------------------------------------------------------------------


def test_invite_template_renders_html_and_text():
    from backend.app.services.email import render_invite_email

    rendered = render_invite_email(
        workspace_name="Acme",
        role="member",
        recipient_email="alice@acme.dev",
        accept_url="https://ship.example.com/invites/abc",
        expires_at=datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc),
        inviter_email="dk@acme.dev",
    )
    assert "Acme" in rendered.subject
    assert "https://ship.example.com/invites/abc" in rendered.html
    assert "https://ship.example.com/invites/abc" in rendered.text
    assert "alice@acme.dev" in rendered.html
    # No leftover Jinja syntax — would mean a missing context var.
    assert "{{" not in rendered.html
    assert "{{" not in rendered.text


def test_navigator_template_escapes_raw_html():
    from backend.app.services.email import render_navigator_summary_email

    body = "Plan:\n\n- step 1 <script>alert(1)</script>\n- **bold** and `code`"
    rendered = render_navigator_summary_email(
        subject="Recap",
        body_markdown=body,
        conversation_url="https://ship.example.com/c/1",
    )
    # Raw script tag must be escaped, not embedded.
    assert "<script>" not in rendered.html
    assert "&lt;script&gt;" in rendered.html
    # Markdown bold + code rendered.
    assert "<strong>bold</strong>" in rendered.html
    assert "<code" in rendered.html
    # Plain-text alternative carries the original body verbatim.
    assert "step 1" in rendered.text
    # Continue-conversation link rendered.
    assert "https://ship.example.com/c/1" in rendered.html


def test_navigator_template_renders_links_safely():
    from backend.app.services.email import render_navigator_summary_email

    body = "See [docs](https://example.com/docs?x=1&y=2) for more."
    rendered = render_navigator_summary_email(
        subject="Recap", body_markdown=body, conversation_url=None
    )
    # The href ends up inside the rendered anchor; ``&`` becomes
    # ``&amp;`` because we HTML-escape before regex substitution.
    assert 'href="https://example.com/docs?x=1&amp;y=2"' in rendered.html
    assert ">docs</a>" in rendered.html
