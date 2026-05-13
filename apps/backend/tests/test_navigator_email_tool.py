"""Navigator ``send_email_to_self`` tool — abuse + transport guards.

The whole point of this tool is that an LLM can fire it on the
user's behalf, so the unit suite has to exercise the rails that
keep that safe:

1. The recipient is hard-pinned to the calling user's account
   email — the LLM can't override it via args.
2. The per-user hourly cap stops a runaway model from spamming
   inbox; once the cap trips the call raises with a
   wait-time-aware message.
3. ``EMAIL_PROVIDER=none`` (operator kill switch) is honoured:
   the tool refuses up-front instead of silently no-op'ing.
4. Both success and provider-side failures land in the
   :class:`AuditLog` so the operator can trace abuse later.
5. Subject + body are clipped to fixed caps so a model
   hallucinating War & Peace doesn't ship 1MB to inbox.

Email transport is faked with the in-memory
:class:`RecordingEmailSender` from
:mod:`backend.app.services.email`, so the tests never touch the
network.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select


def _make_toolbox(
    session,
    monkeypatch,
    *,
    workspace_id,
    user_id,
    email_provider: str = "log",
):
    """Build a :class:`ToolBox` with a real :class:`Settings` instance.

    The email tool reads ``self._settings.email_provider`` directly,
    so we can't reuse the ``settings=None`` shortcut other navigator
    tests use. Instead we monkeypatch the env var (Settings is a
    pydantic-settings ``BaseSettings`` and reads from environment
    by alias) and clear its ``lru_cache`` so the new value lands.
    """
    from backend.app.core import config as cfg
    from backend.app.services.agent.tools import ToolBox

    monkeypatch.setenv("EMAIL_PROVIDER", email_provider)
    cfg.get_settings.cache_clear()
    settings = cfg.get_settings()
    return ToolBox(
        session,
        settings=settings,
        workspace_id=workspace_id,
        user_id=user_id,
    )


def _patch_recording_sender(monkeypatch):
    """Swap the cached email sender for an in-memory recorder.

    Returns the recorder so the test can assert on the captured
    messages. We patch the symbol the tool re-exports so the
    ``get_email_sender`` import inside the tool body resolves to
    our stub.
    """
    from backend.app.services.email import RecordingEmailSender

    recorder = RecordingEmailSender()

    def _fake_get_sender(_settings):
        return recorder

    monkeypatch.setattr(
        "backend.app.services.email.get_email_sender",
        _fake_get_sender,
    )
    monkeypatch.setattr(
        "backend.app.services.agent.tools.get_email_sender",
        _fake_get_sender,
        raising=False,
    )
    return recorder


def _reset_rate_limit():
    """Drop the in-process rate-limit history between tests.

    The cap is module-global so a test that hits the ceiling would
    leak its 5 timestamps into the next test if we didn't clear
    here. Cheaper than parametrising the cap.
    """
    from backend.app.services.agent import tools as tools_mod

    tools_mod._navigator_email_history.clear()


@pytest.fixture(autouse=True)
def _clear_navigator_rate_limit():
    _reset_rate_limit()
    yield
    _reset_rate_limit()


@pytest.mark.asyncio
async def test_send_email_to_self_uses_account_email(
    db_session, seed_workspace, monkeypatch
) -> None:
    """The recipient is fixed to ``user.email`` regardless of args."""
    from backend.app.db.models.tenancy import AuditLog

    recorder = _patch_recording_sender(monkeypatch)
    user, _raw, workspace = seed_workspace

    box = _make_toolbox(
        db_session, monkeypatch, workspace_id=workspace.id, user_id=user.id
    )
    raw = await box.invoke(
        "send_email_to_self",
        {
            "subject": "Recap of our chat",
            "body_markdown": "# Hi\n\nNotes:\n- one\n- two",
        },
    )
    out = json.loads(raw)
    assert out["sent"] is True
    assert out["to"] == user.email
    assert out["subject"] == "Recap of our chat"

    assert len(recorder.messages) == 1
    msg = recorder.messages[0]
    assert msg.to.email == user.email
    assert msg.subject == "Recap of our chat"
    assert "Hi" in msg.html and "Hi" in msg.text
    # Tags are how the operator slices SendGrid traffic later;
    # losing the ``kind`` would make the inbox-summary share
    # invisible in the dashboard.
    assert msg.tags.get("kind") == "navigator_summary"
    assert msg.tags.get("user_id") == str(user.id)

    # Audit row written into the same session so we can read it back
    # without committing.
    await db_session.flush()
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "navigator.email.sent",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload.get("subject") == "Recap of our chat"


@pytest.mark.asyncio
async def test_send_email_to_self_provider_none_refuses(
    db_session, seed_workspace, monkeypatch
) -> None:
    """``EMAIL_PROVIDER=none`` is the kill switch — tool must hard-stop.

    A silent success here would be the worst failure mode: the LLM
    would tell the user "I sent it" and nothing would arrive. The
    tool raises ``ToolInvocationError`` so the agent surface bubbles
    a real explanation back into chat.
    """
    from backend.app.services.agent.tools import ToolInvocationError

    recorder = _patch_recording_sender(monkeypatch)
    user, _raw, workspace = seed_workspace

    box = _make_toolbox(
        db_session,
        monkeypatch,
        workspace_id=workspace.id,
        user_id=user.id,
        email_provider="none",
    )
    with pytest.raises(ToolInvocationError) as excinfo:
        await box.invoke(
            "send_email_to_self",
            {"subject": "Nope", "body_markdown": "won't ship"},
        )
    assert "EMAIL_PROVIDER=none" in str(excinfo.value)
    assert recorder.messages == []


@pytest.mark.asyncio
async def test_send_email_to_self_hourly_cap(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Per-user hourly cap stops at exactly ``_NAVIGATOR_EMAIL_HOURLY_CAP``.

    The first ``cap`` calls succeed; the next one is rejected with
    a wait-time-aware error message. Trips the rolling-window
    cleanup path implicitly: after the cap is hit the history list
    is at full capacity.
    """
    from backend.app.services.agent import tools as tools_mod
    from backend.app.services.agent.tools import ToolInvocationError

    recorder = _patch_recording_sender(monkeypatch)
    user, _raw, workspace = seed_workspace

    box = _make_toolbox(
        db_session, monkeypatch, workspace_id=workspace.id, user_id=user.id
    )
    cap = tools_mod._NAVIGATOR_EMAIL_HOURLY_CAP
    for i in range(cap):
        out = json.loads(
            await box.invoke(
                "send_email_to_self",
                {
                    "subject": f"Recap #{i}",
                    "body_markdown": "body",
                },
            )
        )
        assert out["sent"] is True

    assert len(recorder.messages) == cap

    with pytest.raises(ToolInvocationError) as excinfo:
        await box.invoke(
            "send_email_to_self",
            {"subject": "one too many", "body_markdown": "body"},
        )
    assert "Hourly email cap" in str(excinfo.value)
    # Sender wasn't called — rate limit precedes transport.
    assert len(recorder.messages) == cap


@pytest.mark.asyncio
async def test_send_email_to_self_truncates_oversize_payload(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Subject > 120 chars and body > 16k chars get clipped, not rejected.

    The contract with the LLM is "send what you can" — clipping
    keeps a long-form summary from blowing past inbox limits while
    still delivering the user-visible recap. The tool documents
    both caps in its description; this test pins the actual
    behaviour to the documented numbers.
    """
    from backend.app.services.agent import tools as tools_mod

    recorder = _patch_recording_sender(monkeypatch)
    user, _raw, workspace = seed_workspace

    box = _make_toolbox(
        db_session, monkeypatch, workspace_id=workspace.id, user_id=user.id
    )
    long_subject = "S" * 500
    long_body = "x" * (tools_mod._NAVIGATOR_EMAIL_MAX_BODY + 5_000)

    raw = await box.invoke(
        "send_email_to_self",
        {"subject": long_subject, "body_markdown": long_body},
    )
    out = json.loads(raw)
    assert out["sent"] is True

    assert len(recorder.messages) == 1
    msg = recorder.messages[0]
    assert len(msg.subject) == tools_mod._NAVIGATOR_EMAIL_MAX_SUBJECT
    assert msg.subject.endswith("…")
    # Body cap is on the markdown; the rendered HTML/text adds
    # boilerplate, so we only check it carries the truncation marker.
    assert "(truncated)" in msg.text


@pytest.mark.asyncio
async def test_send_email_to_self_records_failure_in_audit(
    db_session, seed_workspace, monkeypatch
) -> None:
    """Provider returning ``sent=False`` flows back as a structured
    payload + ``navigator.email.failed`` audit row.

    The agent surface uses the structured ``sent`` flag to tell the
    user "I tried but the transport said no", which is much better
    than swallowing the failure as a success.
    """
    from backend.app.db.models.tenancy import AuditLog
    from backend.app.services.email import EmailDeliveryResult

    user, _raw, workspace = seed_workspace

    class _FailingSender:
        provider = "test_failing"

        def __init__(self):
            self.calls = 0

        async def send(self, message):
            self.calls += 1
            return EmailDeliveryResult(
                sent=False,
                provider=self.provider,
                detail="simulated 5xx from upstream",
            )

    failing = _FailingSender()

    monkeypatch.setattr(
        "backend.app.services.agent.tools.get_email_sender",
        lambda _settings: failing,
        raising=False,
    )
    monkeypatch.setattr(
        "backend.app.services.email.get_email_sender",
        lambda _settings: failing,
    )

    box = _make_toolbox(
        db_session, monkeypatch, workspace_id=workspace.id, user_id=user.id
    )
    raw = await box.invoke(
        "send_email_to_self",
        {"subject": "fail me", "body_markdown": "nope"},
    )
    out = json.loads(raw)
    assert out["sent"] is False
    assert out["detail"] == "simulated 5xx from upstream"
    assert failing.calls == 1

    await db_session.flush()
    rows = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == workspace.id,
                AuditLog.action == "navigator.email.failed",
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert "5xx" in (rows[0].payload.get("detail") or "")


@pytest.mark.asyncio
async def test_send_email_to_self_unknown_user(
    db_session, seed_workspace, monkeypatch
) -> None:
    """If the user row vanished mid-session the tool refuses.

    This is the defensive branch for ``user is None``. A real
    deployment shouldn't hit it (the auth dep guarantees the row),
    but the agent runtime swaps user_ids around enough that we
    pin the failure mode here.
    """
    from backend.app.services.agent.tools import ToolInvocationError

    recorder = _patch_recording_sender(monkeypatch)
    _user, _raw, workspace = seed_workspace

    box = _make_toolbox(
        db_session,
        monkeypatch,
        workspace_id=workspace.id,
        user_id=uuid.uuid4(),  # nonexistent user
    )
    with pytest.raises(ToolInvocationError):
        await box.invoke(
            "send_email_to_self",
            {"subject": "ghost", "body_markdown": "nobody home"},
        )
    assert recorder.messages == []
