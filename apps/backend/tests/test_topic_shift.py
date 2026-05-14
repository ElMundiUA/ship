"""Unit tests for TopicService.classify_shift (Navigator topic shift).

Pins the three pre-LLM fast paths + the LLM-classifier round-trip
against the behaviour the chat handler relies on:

- Explicit-phrase regex short-circuits the LLM ("let's switch
  topics" never burns a token)
- Cosine pre-filter: high distance → shifted=True without LLM,
  low distance → shifted=False without LLM, borderline → falls
  through to LLM
- LLM verdict + JSON parsing → TopicShiftDecision
- LLM failure → graceful no-shift (never blocks the turn)
- Too-few-prior-turns → no-shift early-return

The classifier is on the chat hot-path. A regression here is a
real-money regression: cosine pre-filter caps LLM cost; explicit-
phrase regex caps latency. Both are easy to break with a single-
line change in the topic.py heuristics.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.app.core.config import Settings
from backend.app.services.agent import topic as topic_module
from backend.app.services.agent.topic import (
    TopicService,
    TopicShiftDecision,
    detect_explicit_shift,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeMsg:
    """Lightweight stand-in for ``ChatMessageRow`` — the classifier
    only reads ``role`` + ``content`` + ``created_at``, so the rest
    of the SQLAlchemy model is dead weight in unit-tests."""

    __slots__ = ("role", "body", "created_at")

    def __init__(self, role: str, body: str) -> None:
        self.role = role
        self.body = body
        self.created_at = datetime.now(timezone.utc)


def _build_service(
    *,
    monkeypatch: pytest.MonkeyPatch,
    llm_response: str | None = None,
    llm_raises: BaseException | None = None,
    embed_vectors: list[list[float]] | None = None,
    embed_raises: BaseException | None = None,
) -> TopicService:
    """Construct a TopicService with the embedder + LLM mocked.

    The real ``embed_texts`` is at module-level in ``topic.py`` so we
    patch it there directly; the LLM client gets a plain AsyncMock.
    """
    if embed_raises is not None:
        async def _raise_embed(*_args, **_kwargs):
            raise embed_raises
        monkeypatch.setattr(topic_module, "embed_texts", _raise_embed)
    elif embed_vectors is not None:
        async def _fake_embed(*_args, **_kwargs):
            return embed_vectors
        monkeypatch.setattr(topic_module, "embed_texts", _fake_embed)
    else:
        # Default: classifier hasn't been given any embeddings — make
        # sure tests that don't expect a call get a loud error if
        # something accidentally invokes the embedder.
        async def _surprise_embed(*_args, **_kwargs):  # pragma: no cover
            raise AssertionError("embed_texts called unexpectedly")
        monkeypatch.setattr(topic_module, "embed_texts", _surprise_embed)

    client = AsyncMock()
    if llm_raises is not None:
        client.acomplete.side_effect = llm_raises
    elif llm_response is not None:
        client.acomplete.return_value = llm_response
    else:
        # Same as the embedder — surface accidental calls.
        client.acomplete.side_effect = AssertionError(
            "client.acomplete called unexpectedly"
        )

    settings = Settings(
        OPENAI_API_KEY="test",  # type: ignore[call-arg]
    )
    return TopicService(
        session=None,  # type: ignore[arg-type]
        settings=settings,
        client=client,
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


# ---------------------------------------------------------------------------
# 1 — explicit-phrase short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase",
    [
        "ok let's switch topics",
        "Let us change topics",
        "Different question — how do I deploy?",
        "Actually, new topic.",
        "Switching gears for a sec",
        "Forget it, ignore that",
        "никогда не путаю, давай переключимся к другому",
        "Теперь другая тема — про деплой",
        "Новая тема: CI",
        "Забудь это, я не туда смотрел",
        "Другой вопрос — про базу",
    ],
)
def test_detect_explicit_shift_canonical_phrases(phrase: str) -> None:
    assert detect_explicit_shift(phrase) is True


@pytest.mark.parametrize(
    "phrase",
    [
        "",
        "switch to dark mode",  # "switch" alone shouldn't trigger
        "could you change my display name",
        "что у нас с тестами",
    ],
)
def test_detect_explicit_shift_negative_cases(phrase: str) -> None:
    assert detect_explicit_shift(phrase) is False


@pytest.mark.asyncio
async def test_explicit_phrase_short_circuits_classifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit "let's switch topics" never hits the LLM or embedder."""
    svc = _build_service(monkeypatch=monkeypatch)
    decision = await svc.classify_shift(
        running_summary="we were debugging postgres connection pooling",
        recent_messages=[
            _FakeMsg("user", "hey can you check the pool size"),
            _FakeMsg("assistant", "the default is 20, tunable via PG_POOL"),
        ],
        new_user_message="ok let's switch topics — how do I deploy?",
    )
    assert decision.shifted is True
    assert decision.explicit_phrase is True
    assert "switching" in decision.reason.lower()


# ---------------------------------------------------------------------------
# 2 — cosine pre-filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cosine_pre_filter_far_distance_returns_shifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cosine distance ≥ 0.60 short-circuits to shifted=True."""
    # Two near-orthogonal unit vectors → distance ≈ 1.0
    vec_a = [1.0] + [0.0] * 1535
    vec_b = [0.0, 1.0] + [0.0] * 1534
    svc = _build_service(
        monkeypatch=monkeypatch, embed_vectors=[vec_a, vec_b]
    )
    decision = await svc.classify_shift(
        running_summary="long enough running summary " * 5,
        recent_messages=[
            _FakeMsg("user", "let's talk about postgres pooling settings"),
            _FakeMsg("assistant", "ok"),
        ],
        new_user_message="how do I deploy the docker image?",
    )
    assert decision.shifted is True
    assert decision.explicit_phrase is False


@pytest.mark.asyncio
async def test_cosine_pre_filter_near_distance_returns_continue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cosine distance ≤ 0.30 short-circuits to shifted=False."""
    # Two nearly-identical vectors → distance ≈ 0
    vec_a = [1.0] + [0.0] * 1535
    vec_b = [0.99] + [0.0] * 1535
    svc = _build_service(
        monkeypatch=monkeypatch, embed_vectors=[vec_a, vec_b]
    )
    decision = await svc.classify_shift(
        running_summary="long enough running summary " * 5,
        recent_messages=[
            _FakeMsg("user", "what does the pool size default to"),
            _FakeMsg("assistant", "ok"),
        ],
        new_user_message="and what about the max overflow setting?",
    )
    assert decision.shifted is False
    assert decision.reason == ""


@pytest.mark.asyncio
async def test_cosine_pre_filter_fails_falls_through_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Embedder error (rate limit / no key) falls through to LLM, not raise."""
    svc = _build_service(
        monkeypatch=monkeypatch,
        embed_raises=RuntimeError("OPENAI_API_KEY not set"),
        llm_response='{"shifted": false, "reason": "still same topic", "new_title": null}',
    )
    decision = await svc.classify_shift(
        running_summary="long enough running summary " * 5,
        recent_messages=[
            _FakeMsg("user", "checking pool size again"),
            _FakeMsg("assistant", "ok"),
        ],
        new_user_message="what's the SQLAlchemy default?",
    )
    # The LLM verdict wins after the cosine fail.
    assert decision.shifted is False


# ---------------------------------------------------------------------------
# 3 — LLM classifier round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_returns_shifted_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Borderline cosine → LLM says shifted → decision propagates."""
    # Mid-distance — the cosine pre-filter returns "borderline" so
    # the LLM gets called.
    # cos = 0.5 between vec_a and vec_b → distance = 0.5, sits in
    # the borderline band (0.30 < d < 0.60) so the LLM gets called.
    vec_a = [1.0, 0.0] + [0.0] * 1534
    vec_b = [0.5, 0.866] + [0.0] * 1534
    svc = _build_service(
        monkeypatch=monkeypatch,
        embed_vectors=[vec_a, vec_b],
        llm_response='{"shifted": true, "reason": "different domain", "new_title": "Deployment"}',
    )
    decision = await svc.classify_shift(
        running_summary="we were talking about postgres pooling " * 3,
        recent_messages=[
            _FakeMsg("user", "what's the pool size"),
            _FakeMsg("assistant", "20"),
        ],
        new_user_message="and how do I roll the container image?",
    )
    assert decision.shifted is True
    assert decision.reason == "different domain"
    assert decision.new_title == "Deployment"


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_no_shift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM exception → graceful no-shift, never raises."""
    vec_a = [0.5] * 1536
    vec_b = [0.4] * 1536  # forces the LLM path
    svc = _build_service(
        monkeypatch=monkeypatch,
        embed_vectors=[vec_a, vec_b],
        llm_raises=RuntimeError("rate-limit"),
    )
    decision = await svc.classify_shift(
        running_summary="long enough running summary " * 5,
        recent_messages=[
            _FakeMsg("user", "what's the pool size"),
            _FakeMsg("assistant", "20"),
        ],
        new_user_message="and the max overflow?",
    )
    assert decision.shifted is False
    assert decision.reason == ""
    assert decision.new_title is None


# ---------------------------------------------------------------------------
# 4 — edge: too few prior turns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_prior_user_turns_returns_no_shift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Brand-new thread (zero user turns) → no-shift, no LLM call.

    The classifier has nothing to compare against; falsely flagging
    the first message as a shift would render the banner on every
    fresh chat.
    """
    svc = _build_service(monkeypatch=monkeypatch)
    decision = await svc.classify_shift(
        running_summary=None,
        recent_messages=[],  # no prior turns
        new_user_message="hello there, how do I deploy?",
    )
    assert decision == TopicShiftDecision(
        shifted=False, reason="", new_title=None
    )
