"""Unit tests for DraftingIntentService (E20-2).

Pins the classifier's two cheap fast paths + the LLM round-trip
against the behaviour the in-thread CTA relies on:

- Explicit-phrase ENTER: "let's shape a new project around X" →
  ENTER when not already drafting; NEUTRAL when already drafting
  (no surprise "switch to drafting" CTA on a thread that IS).
- Explicit-phrase EXIT: "forget the project, what about Y" →
  EXIT when drafting; NEUTRAL when not.
- LLM borderline: ambiguous message + drafting context → LLM
  decides; verdict propagates.
- LLM-side safety: if the LLM returns ENTER but we're already
  drafting, the service downgrades to NEUTRAL — belt-and-braces
  so a hallucinated verdict can't flip mode against the user's
  intent.
- LLM failure → NEUTRAL (graceful degrade).
- Too-thin context (short thread) → NEUTRAL without LLM call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.app.core.config import Settings
from backend.app.services.agent.drafting_intent import (
    DraftingIntentDecision,
    DraftingIntentService,
    explicit_enter,
    explicit_exit,
)


class _FakeMsg:
    __slots__ = ("role", "body", "created_at")

    def __init__(self, role: str, body: str) -> None:
        self.role = role
        self.body = body
        self.created_at = datetime.now(timezone.utc)


def _service(llm_response: str | None = None, llm_raises: BaseException | None = None) -> DraftingIntentService:
    client = AsyncMock()
    if llm_raises is not None:
        client.acomplete.side_effect = llm_raises
    elif llm_response is not None:
        client.acomplete.return_value = llm_response
    else:
        client.acomplete.side_effect = AssertionError(
            "client.acomplete called unexpectedly"
        )
    return DraftingIntentService(
        settings=Settings(OPENAI_API_KEY="test"),  # type: ignore[call-arg]
        client=client,
    )


# ---------------------------------------------------------------------------
# Explicit-phrase fast paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "let's shape a new project for the dashboard",
        "I want to start a new project around CSV exports",
        "let's spin up a project for the onboarding flow",
        "shape a project around our memory ranker",
        "давай создадим новый проект про дашборд",
        "хочу оформить новый проект",
        "нужен новый проект про релизы",
    ],
)
def test_explicit_enter_canonical_phrases(phrase: str) -> None:
    assert explicit_enter(phrase) is True


@pytest.mark.parametrize(
    "phrase",
    [
        "shape the UI of the table",
        "the project we just shipped",
        "what's the project's status",
        "новости по проекту",
        "",
    ],
)
def test_explicit_enter_negative_cases(phrase: str) -> None:
    assert explicit_enter(phrase) is False


@pytest.mark.parametrize(
    "phrase",
    [
        "forget the project, tell me about CSV export instead",
        "drop the project, what was that thing about deploys?",
        "never mind the project, I have another question",
        "stop drafting, let's talk about something else",
        "exit drafting",
        "забудь про проект, расскажи про деплой",
        "прекрати черновик",
    ],
)
def test_explicit_exit_canonical_phrases(phrase: str) -> None:
    assert explicit_exit(phrase) is True


@pytest.mark.parametrize(
    "phrase",
    [
        "let's review the project",
        "продолжай работу над проектом",
        "",
    ],
)
def test_explicit_exit_negative_cases(phrase: str) -> None:
    assert explicit_exit(phrase) is False


# ---------------------------------------------------------------------------
# Asymmetry — ENTER only when NOT drafting, EXIT only when drafting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enter_phrase_fires_when_not_drafting() -> None:
    svc = _service()
    decision = await svc.classify(
        new_user_message="let's shape a new project for the dashboard",
        currently_drafting=False,
        recent_messages=[
            _FakeMsg("user", "what's the dashboard look like today"),
            _FakeMsg("assistant", "it's a simple grid of cards"),
        ],
    )
    assert decision.verdict == "ENTER"
    assert "shape" in decision.reason.lower() or "drafting" in decision.reason.lower()


@pytest.mark.asyncio
async def test_enter_phrase_neutral_when_already_drafting() -> None:
    """Suggesting "switch to drafting" while already drafting is a
    confusing CTA — the fast path collapses to NEUTRAL."""
    svc = _service()
    decision = await svc.classify(
        new_user_message="let's shape a new project for the dashboard",
        currently_drafting=True,
        recent_messages=[
            _FakeMsg("user", "started the brief"),
            _FakeMsg("assistant", "got it, what's the scope"),
        ],
    )
    assert decision.verdict == "NEUTRAL"


@pytest.mark.asyncio
async def test_exit_phrase_fires_when_drafting() -> None:
    svc = _service()
    decision = await svc.classify(
        new_user_message="forget the project, tell me about CSV export instead",
        currently_drafting=True,
        recent_messages=[
            _FakeMsg("user", "drafting a brief about retention"),
            _FakeMsg("assistant", "what's the user impact"),
        ],
    )
    assert decision.verdict == "EXIT"


@pytest.mark.asyncio
async def test_exit_phrase_neutral_when_not_drafting() -> None:
    svc = _service()
    decision = await svc.classify(
        new_user_message="forget the project, tell me about CSV export instead",
        currently_drafting=False,
        recent_messages=[
            _FakeMsg("user", "just chatting"),
            _FakeMsg("assistant", "sure"),
        ],
    )
    assert decision.verdict == "NEUTRAL"


# ---------------------------------------------------------------------------
# LLM round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_ambiguous_message_propagates_verdict() -> None:
    """Ambiguous message → LLM gets called; its JSON verdict
    propagates back to the decision."""
    svc = _service(
        llm_response=(
            '{"verdict": "ENTER", "reason": "user is describing a '
            'new initiative", "suggested_title": "Retention overhaul"}'
        )
    )
    decision = await svc.classify(
        new_user_message="hmm there's a whole thing we should build for retention",
        currently_drafting=False,
        # Long-enough context to bypass the LLM-context-too-thin
        # guard.
        recent_messages=[
            _FakeMsg("user", "I keep thinking about retention metrics"),
            _FakeMsg(
                "assistant",
                "the funnel drop-off after day 7 is the biggest bucket "
                "we have unresolved attention on right now",
            ),
        ],
    )
    assert decision.verdict == "ENTER"
    assert decision.suggested_title == "Retention overhaul"


@pytest.mark.asyncio
async def test_llm_verdict_downgrades_inconsistent_modes() -> None:
    """If the LLM hallucinates ENTER while we're already drafting,
    the service overrides to NEUTRAL."""
    svc = _service(
        llm_response='{"verdict": "ENTER", "reason": "...", "suggested_title": null}'
    )
    decision = await svc.classify(
        new_user_message="how about retention",
        currently_drafting=True,  # ENTER would be inconsistent here
        recent_messages=[
            _FakeMsg("user", "I keep thinking about retention metrics"),
            _FakeMsg(
                "assistant",
                "the funnel drop-off after day 7 is the biggest bucket "
                "we have unresolved attention on right now",
            ),
        ],
    )
    assert decision.verdict == "NEUTRAL"


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_neutral() -> None:
    """LLM raises → graceful NEUTRAL, never blocks the chat turn."""
    svc = _service(llm_raises=RuntimeError("rate-limit"))
    decision = await svc.classify(
        new_user_message="some ambiguous message that needs the model",
        currently_drafting=False,
        recent_messages=[
            _FakeMsg("user", "thinking about a few things in our stack"),
            _FakeMsg(
                "assistant",
                "what's the highest impact one to talk through right now",
            ),
        ],
    )
    assert decision.verdict == "NEUTRAL"


# ---------------------------------------------------------------------------
# Cheap edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_short_thread_returns_neutral_without_llm_call() -> None:
    """Threads without enough context don't burn LLM tokens."""
    svc = _service()  # no LLM expected
    decision = await svc.classify(
        new_user_message="hi",
        currently_drafting=False,
        recent_messages=[],
    )
    assert decision.verdict == "NEUTRAL"


@pytest.mark.asyncio
async def test_empty_message_returns_neutral() -> None:
    svc = _service()
    decision = await svc.classify(
        new_user_message="",
        currently_drafting=False,
        recent_messages=[
            _FakeMsg("user", "...lots of context..."),
            _FakeMsg("assistant", "...lots of context..."),
        ],
    )
    assert decision.verdict == "NEUTRAL"
