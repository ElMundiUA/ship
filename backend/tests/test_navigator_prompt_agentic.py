"""Navigator prompt carries the agentic operating rules (PR2 of the
overhaul).

These tests are deliberately structural — the prompt is human-edited
markdown and asserting on copy would lock in editorial choices that
should be free to evolve. We assert only the load-bearing rules: the
section exists, and the verbs that drive runtime behaviour
(``Plan first``, ``never guess``, ``Verify before mutate``, etc.)
each appear at least once. If a future edit drops one of these, the
agent regresses to chat-style responses; the test catches that
before it ships.

The agent doesn't read these tests — it reads the markdown file. So
the assertions here mirror what the LLM should still see no matter
how the prose around them gets reshuffled.
"""

from __future__ import annotations

import pytest


def _prompt() -> str:
    from backend.app.services import agent_roles as svc

    role = svc.get_default("navigator")
    assert role is not None, "navigator role must be in the default registry"
    return role.prompt


def test_how_you_operate_block_exists() -> None:
    text = _prompt()
    assert "## How you operate" in text


@pytest.mark.parametrize(
    "needle, why",
    [
        # Rule 1 — plan first.
        ("Plan first", "rule 1: plan-then-execute is the load-bearing agentic shift"),
        ("## Plan", "rule 1: the plan block is what the agent renders"),
        # Rule 2 — gather context, don't guess.
        ("never guess", "rule 2: don't fall back to training data"),
        # Rule 3 — read Session context.
        ("Session context", "rule 3: read identity / tracker / repos from there, never re-ask"),
        # Rule 4 — cite tool evidence.
        ("tool evidence", "rule 4: every claim cites a tool result"),
        # Rule 5 — verify before mutate.
        ("Verify before mutate", "rule 5: confirm side-effect tool calls"),
        ("ticket_create", "rule 5: name the gated mutating tools explicitly"),
        # Rule 6 — delegate to specialists.
        ("run_subagent", "rule 6: delegate UX/architecture/QA/triage to a subagent"),
        # Rule 7 — one thread, one initiative (anti-pivot).
        ("One thread", "rule 7: don't spawn parallel intent inside one thread"),
        # Rule 8 — output discipline.
        ("Output discipline", "rule 8: answer first, then plan, then evidence"),
    ],
)
def test_each_operating_rule_carries_its_load_bearing_token(
    needle: str, why: str
) -> None:
    text = _prompt()
    assert needle in text, f"missing load-bearing token {needle!r} ({why})"


def test_old_chat_style_lead_in_is_gone() -> None:
    """The pre-PR2 description framed the Navigator as "a software-
    engineering agent in a single chat window. Be concrete, accurate,
    concise." The agentic rewrite reframes it as autonomous, with
    plan-first / evidence-first / verify-first discipline. If a future
    edit reverts to the chat framing, the prompt regresses; this test
    pins the autonomous framing."""
    text = _prompt()
    assert "autonomous" in text.lower(), (
        "the lead-in should frame Navigator as autonomous, not as a chat"
    )


def test_session_context_route_replaces_list_activated_repos_default() -> None:
    """The pre-PR1/PR2 prompt told the agent to call
    ``list_activated_repos`` first to get a repo UUID. PR1 surfaces
    activated repos in the session frame, so the default flow has to
    flip — call only when the user mentions a repo NOT in the
    session frame. If a future edit reverts the default, the agent
    burns a tool call every turn."""
    text = _prompt()
    assert "Session context" in text
    # The session-context resolution rule should appear in the Code
    # lookup section, not just the rules block.
    assert "## Code lookup" in text
    code_lookup_section = text.split("## Code lookup", 1)[1]
    assert "Session context" in code_lookup_section
