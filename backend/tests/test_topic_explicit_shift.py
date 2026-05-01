"""Pin the explicit-phrase shortcut in :meth:`TopicService.classify_shift`.

The detector turns canonical RU + EN "I'm switching topics" phrases
into a high-confidence ``shifted=True`` decision **without** calling
the LLM classifier. We test:

1. The pure :func:`detect_explicit_shift` regex matrix — Russian and
   English variants, plus a control set that must NOT match
   (substrings the user clearly didn't mean as a topic switch).
2. ``classify_shift`` short-circuits when the detector fires:
   ``explicit_phrase=True`` and the LLM client is never invoked.
3. ``classify_shift`` still consults the LLM when no explicit phrase
   matched.
"""

from __future__ import annotations

import json
import uuid

import pytest

from backend.app.db.models.agent_surface import ChatMessage as ChatMessageRow
from backend.app.services.agent.topic import (
    TopicService,
    detect_explicit_shift,
)


@pytest.mark.parametrize(
    "message,expected",
    [
        # ----- English: should match -----
        ("let's switch topics", True),
        ("let us switch topics", True),
        ("new topic please", True),
        ("change subject", True),
        ("change the topic", True),
        ("switching gears", True),
        ("forget this", True),
        ("forget about that", True),
        ("never mind", True),
        ("moving on", True),
        ("a different question", True),
        # ----- Russian: should match -----
        ("давай переключимся на другое", True),
        ("давайте сменим тему", True),
        ("теперь другое", True),
        ("теперь другая тема", True),
        ("новая тема", True),
        ("забудь про это", True),
        ("забей", True),
        ("переключимся", True),
        ("другой вопрос", True),
        # ----- Must NOT match (control set) -----
        ("switch to dark mode", False),
        ("continue on this", False),
        ("какой статус по тикетам", False),
        ("давай обсудим что-то еще", False),
        ("change directory", False),
        ("topic command syntax", False),
    ],
)
def test_detect_explicit_shift(message: str, expected: bool) -> None:
    assert detect_explicit_shift(message) is expected


class _RecordingClient:
    """LLM stub that records calls; raises if invoked unexpectedly."""

    vendor = "stub"

    def __init__(self, response: str = "") -> None:
        self._response = response
        self.acomplete_calls: list[dict] = []

    async def acomplete(self, messages, **kwargs):
        self.acomplete_calls.append({"messages": messages, **kwargs})
        return self._response

    async def astream(self, messages, tools=(), **kwargs):  # pragma: no cover
        raise NotImplementedError


@pytest.mark.asyncio
async def test_classify_shift_short_circuits_on_explicit_phrase(
    db_session, seed_workspace
) -> None:
    user, _, workspace = seed_workspace
    client = _RecordingClient()
    service = TopicService(
        db_session,
        settings=None,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=user.id,
    )

    # One prior user turn so the classifier wouldn't bail on
    # "not enough signal" — that path fires before the explicit
    # check and would mask the assertion.
    prior = ChatMessageRow(
        thread_id=uuid.uuid4(),
        role="user",
        body="What's blocking ELS-100?",
    )

    decision = await service.classify_shift(
        running_summary=None,
        recent_messages=[prior],
        new_user_message="давай переключимся на другое",
    )
    assert decision.shifted is True
    assert decision.explicit_phrase is True
    # LLM must NOT have been called — the regex shortcut owns this case.
    assert client.acomplete_calls == []


@pytest.mark.asyncio
async def test_classify_shift_falls_through_to_llm_without_explicit_phrase(
    db_session, seed_workspace
) -> None:
    user, _, workspace = seed_workspace
    # Stub returns a "not shifted" payload so the short-circuit
    # path's behaviour is observable purely through the call count.
    client = _RecordingClient(
        response=json.dumps(
            {"shifted": False, "reason": "still on topic", "new_title": None}
        )
    )
    service = TopicService(
        db_session,
        settings=type(
            "S",
            (),
            {"agent_model_fast": "stub-fast"},
        )(),  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
        workspace_id=workspace.id,
        user_id=user.id,
    )

    prior = ChatMessageRow(
        thread_id=uuid.uuid4(),
        role="user",
        body="What's blocking ELS-100?",
    )

    decision = await service.classify_shift(
        running_summary=None,
        recent_messages=[prior],
        new_user_message="any update from the team?",
    )
    assert decision.shifted is False
    assert decision.explicit_phrase is False
    assert len(client.acomplete_calls) == 1
