"""Pin the A1 prompt hardening — hallucination rules + Session context.

After the topic transcript surfaced fabricated workspace members,
PR authors, and a 2025 calendar year (in a 2026 conversation), the
static system prompt was extended with:

- An expanded "Never fabricate" list covering people, attribution,
  versions, dates, line counts (was: only repos/tickets/URLs/ids).
- A "When you don't know" section spelling out that "I don't know"
  and "the data doesn't include X" are preferred answers.
- An "On user pushback" clause that pins the agent to a fresh tool
  call instead of improvising a corrected guess.
- A reference to a dynamic "Session context" frame.

And ``assemble_messages`` now ships that Session context as a second
system message, carrying today's date (UTC) and the active
workspace id.

Tests below pin all four pieces. We don't assert on exact wording —
operators tweak the prose — but we do assert on the load-bearing
substrings that other parts of the prompt reference.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest

from backend.app.db.models.agent_surface import ChatThread
from backend.app.services.agent.topic import (
    TopicService,
    _AGENT_SYSTEM_PROMPT,
    _render_session_context,
)


def _thread(workspace_id: uuid.UUID, user_id: uuid.UUID) -> ChatThread:
    return ChatThread(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        title="t",
    )


# ---------------------------------------------------------------------------
# Static prompt — hard rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle",
    [
        # Identifier classes the original "Never fabricate" line
        # already covered — make sure we didn't drop them in the edit.
        "repo paths",
        "tickets",
        "URLs",
        "artifact ids",
        "pipeline ids",
        "integration names",
        # New A1 additions — every one of these was hallucinated in
        # the transcript that motivated the patch.
        "user names",
        "emails",
        "logins",
        "authors",
        "PR numbers",
        "commit",
        "version strings",
        "timestamps",
        "dates",
    ],
)
def test_hard_rules_cover_hallucinated_classes(needle: str) -> None:
    assert needle in _AGENT_SYSTEM_PROMPT, (
        f"_AGENT_SYSTEM_PROMPT missing '{needle}' — A1 hardening regressed"
    )


def test_hard_rules_have_idk_section() -> None:
    """The "When you don't know" header anchors the IDK rules; downstream
    docs reference it by name. Other prose can change freely."""
    assert "## When you don't know" in _AGENT_SYSTEM_PROMPT


def test_hard_rules_pushback_clause() -> None:
    """On user pushback the agent must re-call a tool, not improvise.
    Pin the load-bearing verb so casual rewording can't drop it."""
    # Match either 're-call' / 'recall' / 'call the tool that' patterns.
    assert re.search(
        r"(re[- ]?call|call the tool|call .*ground truth)",
        _AGENT_SYSTEM_PROMPT,
        flags=re.IGNORECASE,
    )


def test_hard_rules_reference_session_context() -> None:
    """The static prompt must point at the dynamic frame so the agent
    knows where to read today's date from. Renaming the frame
    requires updating both sides — this test enforces the link."""
    assert "Session context" in _AGENT_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Session context renderer
# ---------------------------------------------------------------------------


def test_session_context_pins_iso_date_and_weekday() -> None:
    workspace_id = uuid.uuid4()
    now = datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc)
    out = _render_session_context(workspace_id=workspace_id, now=now)
    assert "2026-05-02" in out
    assert "Saturday" in out
    assert str(workspace_id) in out
    assert out.startswith("## Session context")


def test_session_context_warns_against_training_data() -> None:
    """Without an explicit "don't lean on training data" cue the model
    happily invents years. The cue is the whole point of the frame."""
    out = _render_session_context(
        workspace_id=uuid.uuid4(),
        now=datetime.now(timezone.utc),
    )
    assert "training data" in out


# ---------------------------------------------------------------------------
# Wiring through assemble_messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_assemble_emits_session_context_after_system_prompt(
    db_session, seed_workspace
) -> None:
    """The dynamic frame must sit at index 1, immediately after the
    static prompt, before any policies / topic-summary / bucket /
    KB layers. Order is load-bearing: the static prompt's "Today's
    date is in the **Session context** system message that follows"
    rule reads "follows" literally."""
    user, _, workspace = seed_workspace
    service = TopicService(
        db_session,
        settings=None,  # type: ignore[arg-type]
        client=None,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=user.id,
    )
    out = await service.assemble_messages(
        thread=_thread(workspace.id, user.id),
        recent_messages=[],
        new_user_message="hi",
    )

    assert out[0].role == "system" and "You are Ship" in out[0].content
    assert out[1].role == "system" and "## Session context" in out[1].content
    # Today's date should be present in YYYY-MM-DD form. We don't
    # pin the exact value (test runs on whatever day) but the format
    # has to be there so the model has something to anchor against.
    assert re.search(r"\d{4}-\d{2}-\d{2}", out[1].content)
    assert str(workspace.id) in out[1].content
