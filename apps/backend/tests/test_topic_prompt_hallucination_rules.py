"""Pin the A1 prompt hardening — hallucination rules + Session context.

After the topic transcript surfaced fabricated workspace members,
PR authors, and a 2025 calendar year (in a 2026 conversation), the
Navigator prompt was extended with:

- An expanded "Never fabricate" list covering people, attribution,
  versions, dates, line counts (was: only repos/tickets/URLs/ids).
- An "I don't know" preference for ambiguous answers.
- An "On user pushback" clause pinning the agent to a fresh tool
  call instead of improvising a corrected guess.
- A reference to a dynamic "Session context" frame.

These rules now live in the workspace-policy seed
(``backend.app.services.policies_seed``) under role
``navigator``. The Navigator artifact body is the playbook
("what to do"); the seed is the invariants ("what is forbidden").
Tests below pin both — the seed entries cover the hallucination
classes; ``assemble_messages`` covers the session-context system
message that the seed entry references.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest

from backend.app.db.models.agent_surface import ChatThread
from backend.app.services.agent.topic import (
    TopicService,
    _load_navigator_prompt,
    _render_session_context,
)
from backend.app.services.policies_seed import default_policies


def _navigator_prompt() -> str:
    """Cache-friendly helper: catalog read is mtime-cached internally."""
    return _load_navigator_prompt()


def _navigator_policy_text() -> str:
    """Concatenated body of all navigator-scoped seed policies."""
    return "\n\n".join(
        p.body for p in default_policies()
        if p.applies_to_roles and "navigator" in p.applies_to_roles
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
        "URLs",
        "artifact ids",
        "pipeline ids",
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
def test_navigator_no_fabrication_policy_covers_classes(needle: str) -> None:
    assert needle in _navigator_policy_text(), (
        f"navigator policy text missing '{needle}' — A1 hardening regressed"
    )


def test_navigator_policy_prefers_idk_over_guessing() -> None:
    """The IDK guidance ("I don't know" is a preferred answer) lives
    in the navigator-no-fabricated-identifiers policy body."""
    assert "I don't know" in _navigator_policy_text()


def test_navigator_policy_pushback_clause() -> None:
    """On user pushback the agent must call a tool, not improvise.
    Pin the load-bearing verb so casual rewording of the policy
    body can't drop it."""
    assert re.search(
        r"(call the tool|call .*ground truth|re[- ]?call)",
        _navigator_policy_text(),
        flags=re.IGNORECASE,
    )


def test_navigator_policy_references_session_context() -> None:
    """The session-time policy must point at the dynamic frame so
    the agent knows where to read today's date from. Renaming the
    frame requires updating both sides — this test enforces the
    link."""
    assert "Session context" in _navigator_policy_text()


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
