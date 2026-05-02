"""ELS-59 — pin the cosine-distance soft-switch in
:meth:`TopicService.classify_shift`.

Sequence inside ``classify_shift`` after the priors-count gate:

1. :func:`detect_explicit_shift` regex (covered in
   ``test_topic_explicit_shift.py``).
2. **This file:** cosine pre-filter on (prior context, new message).
   Distance >= ``_COSINE_SHIFT_THRESHOLD`` short-circuits to
   ``shifted=True`` (LLM not called); distance <=
   ``_COSINE_CONTINUE_THRESHOLD`` short-circuits to ``shifted=False``;
   anything in between falls through to the LLM classifier.
3. LLM classifier — invoked only on borderline cases.

We stub :func:`embed_texts` so tests don't need an OPENAI_API_KEY,
and stub the LLM client so we can assert "fell through" by counting
``acomplete`` calls.
"""

from __future__ import annotations

import json
import uuid
from typing import Sequence

import pytest

from backend.app.db.models.agent_surface import ChatMessage as ChatMessageRow
from backend.app.services.agent import topic as topic_module
from backend.app.services.agent.topic import TopicService


class _RecordingClient:
    """LLM stub identical to the one in test_topic_explicit_shift —
    counts ``acomplete`` calls so we can prove the cosine path
    skipped the LLM (or didn't).
    """

    vendor = "stub"

    def __init__(self, response: str = "") -> None:
        self._response = response
        self.acomplete_calls: list[dict] = []

    async def acomplete(self, messages, **kwargs):
        self.acomplete_calls.append({"messages": messages, **kwargs})
        return self._response

    async def astream(self, messages, tools=(), **kwargs):  # pragma: no cover
        raise NotImplementedError


def _patch_embed(
    monkeypatch: pytest.MonkeyPatch, vectors: Sequence[Sequence[float]]
) -> None:
    """Replace ``embed_texts`` with a stub that returns the given
    vectors verbatim. Order matches the call site:
    ``[prior_context, new_user_message]``.
    """

    async def _stub(texts, *, settings=None):  # type: ignore[no-untyped-def]
        return [list(v) for v in vectors]

    monkeypatch.setattr(topic_module, "embed_texts", _stub)


def _prior_turn(body: str) -> ChatMessageRow:
    return ChatMessageRow(
        thread_id=uuid.uuid4(),
        role="user",
        body=body,
    )


def _settings_stub():
    return type("S", (), {"agent_model_fast": "stub-fast"})()


@pytest.mark.asyncio
async def test_cosine_clear_shift_short_circuits_llm(
    db_session, seed_workspace, monkeypatch
):
    """Distance >= 0.60 → ``shifted=True`` without consulting the LLM."""
    user, _, workspace = seed_workspace
    # Two near-orthogonal vectors → cosine distance ~1.0.
    _patch_embed(monkeypatch, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    client = _RecordingClient()
    service = TopicService(
        db_session,
        settings=_settings_stub(),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=user.id,
    )

    # Prior text needs to clear ``_COSINE_MIN_PRIOR_CHARS`` (60) so the
    # pre-filter even runs. One detailed turn does it.
    prior = _prior_turn(
        "I need a rundown of yesterday's pipeline runs and which "
        "self-heal lanes failed for the ship-canary repo."
    )
    decision = await service.classify_shift(
        running_summary=None,
        recent_messages=[prior],
        new_user_message="any updates from the team about the office party?",
    )

    assert decision.shifted is True
    assert decision.explicit_phrase is False
    assert decision.reason  # non-empty soft-shift copy
    assert client.acomplete_calls == []


@pytest.mark.asyncio
async def test_cosine_clear_continue_short_circuits_llm(
    db_session, seed_workspace, monkeypatch
):
    """Distance <= 0.30 → ``shifted=False`` without consulting the LLM."""
    user, _, workspace = seed_workspace
    # Identical vectors → distance 0.0.
    _patch_embed(monkeypatch, [[0.6, 0.8, 0.0], [0.6, 0.8, 0.0]])
    client = _RecordingClient()
    service = TopicService(
        db_session,
        settings=_settings_stub(),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=user.id,
    )

    prior = _prior_turn(
        "Show me the failing self-heal runs for the ship-canary repo "
        "from yesterday — full diagnostic, not just headlines."
    )
    decision = await service.classify_shift(
        running_summary=None,
        recent_messages=[prior],
        new_user_message="and what about the day before that?",
    )

    assert decision.shifted is False
    assert decision.reason == ""
    assert client.acomplete_calls == []


@pytest.mark.asyncio
async def test_cosine_borderline_falls_through_to_llm(
    db_session, seed_workspace, monkeypatch
):
    """Distance between the two thresholds → LLM classifier still runs."""
    user, _, workspace = seed_workspace
    # cos(theta) = 0.5 → distance 0.5 (between continue=0.30 and shift=0.60).
    import math

    a = [1.0, 0.0]
    b = [math.cos(math.radians(60)), math.sin(math.radians(60))]
    _patch_embed(monkeypatch, [a, b])
    client = _RecordingClient(
        response=json.dumps(
            {"shifted": False, "reason": "borderline-but-related", "new_title": None}
        )
    )
    service = TopicService(
        db_session,
        settings=_settings_stub(),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=user.id,
    )

    prior = _prior_turn(
        "Walk me through the topic-shift classifier and how it decides "
        "whether the user moved on to a new conversation."
    )
    decision = await service.classify_shift(
        running_summary=None,
        recent_messages=[prior],
        new_user_message="how does the cosine distance threshold work in practice?",
    )

    # LLM verdict wins in the borderline band.
    assert decision.shifted is False
    assert len(client.acomplete_calls) == 1


@pytest.mark.asyncio
async def test_cosine_skipped_when_prior_context_too_thin(
    db_session, seed_workspace, monkeypatch
):
    """Tiny prior context (< 60 chars) → skip cosine, fall to LLM."""
    user, _, workspace = seed_workspace
    embed_calls = {"n": 0}

    async def _stub(texts, *, settings=None):  # type: ignore[no-untyped-def]
        embed_calls["n"] += 1
        return [[1.0, 0.0], [0.0, 1.0]]

    monkeypatch.setattr(topic_module, "embed_texts", _stub)
    client = _RecordingClient(
        response=json.dumps(
            {"shifted": False, "reason": "thin-context", "new_title": None}
        )
    )
    service = TopicService(
        db_session,
        settings=_settings_stub(),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=user.id,
    )

    prior = _prior_turn("hi")
    decision = await service.classify_shift(
        running_summary=None,
        recent_messages=[prior],
        new_user_message="what's the architecture of this thing?",
    )

    assert decision.shifted is False
    assert embed_calls["n"] == 0  # cosine pre-filter never ran
    assert len(client.acomplete_calls) == 1


@pytest.mark.asyncio
async def test_cosine_embed_failure_falls_through(
    db_session, seed_workspace, monkeypatch
):
    """When ``embed_texts`` raises (e.g. no API key, rate limit), the
    classifier must still produce a verdict via the LLM rather than
    bubbling the error and killing the turn.
    """
    user, _, workspace = seed_workspace

    async def _failing(texts, *, settings=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    monkeypatch.setattr(topic_module, "embed_texts", _failing)
    client = _RecordingClient(
        response=json.dumps(
            {"shifted": False, "reason": "fallback-from-embed-fail", "new_title": None}
        )
    )
    service = TopicService(
        db_session,
        settings=_settings_stub(),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=user.id,
    )

    prior = _prior_turn(
        "Walk me through the topic-shift classifier and how it decides "
        "whether the user moved on to a new conversation."
    )
    decision = await service.classify_shift(
        running_summary=None,
        recent_messages=[prior],
        new_user_message="any updates from the team about the office party?",
    )

    assert decision.shifted is False
    assert len(client.acomplete_calls) == 1
