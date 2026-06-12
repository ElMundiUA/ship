"""Drafting-intent classifier (E20-2).

Detects "the user wants to shape a new project" / "the user wants
to stop drafting and chat about something else" signals on each
user turn. Drives the inline CTAs the Console renders mid-thread:

- Thread is **not** in drafting mode + user message reads like
  "let's shape X" → emit ``ENTER`` verdict.
- Thread **is** in drafting mode + user message reads off-topic
  for the in-flight draft → emit ``EXIT`` verdict.
- Everything else → ``NEUTRAL`` (no CTA, no surprise mode flip).

Cost model: regex fast-path on canonical phrases short-circuits
the LLM. Falls through to a small JSON-mode classifier only when
the patterns are ambiguous AND the thread has enough recent
context to be worth a token spend.

We never *auto-flip* the intent — that's E20-3's Console CTA.
This service only produces the suggestion; the user clicks.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal, Sequence

from backend.app.core.config import Settings
from backend.app.services.agent.client import AgentClient, ChatMessage
from backend.app.services.agent.topic import _last_turns_text


logger = logging.getLogger(__name__)


Verdict = Literal["ENTER", "EXIT", "NEUTRAL", "ESCALATE"]


@dataclass(slots=True)
class DraftingIntentDecision:
    """Structured output from
    :meth:`DraftingIntentService.classify`.

    ``verdict`` is the actionable signal; ``reason`` is a short
    string the Console renders under the inline CTA so the user
    can see *why* the agent thinks they want to switch.
    """

    verdict: Verdict
    reason: str = ""
    suggested_title: str | None = None


# ---------------------------------------------------------------------------
# Cheap pattern matchers
# ---------------------------------------------------------------------------

# Canonical "I want to shape a new project" cues. Each pattern is
# anchored at a word boundary so we don't false-trigger on the
# substring "project" or "shape" appearing in any context (e.g.
# "shape the UI" shouldn't fire — we're looking for shaping work,
# not shaping nouns).
_ENTER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # English
        r"\b(let'?s|let us|i (?:want to|would like to))\s+(?:shape|scope|kick off|spin up|start|launch|create)(?:\s+(?:a|an|the))?\s+(?:new\s+)?project\b",
        r"\b(?:shape|scope) (?:a|an|the)?\s*(?:new\s+)?project\b",
        r"\bdraft (?:a|an|the)?\s*(?:new\s+)?project (?:brief|charter|outline)\b",
        # Russian
        r"\b(давай|давайте)\s+(?:создадим|зашейпим|оформим|запланируем|обсудим)\s+(?:новый\s+)?проект\b",
        r"\b(нужен|нужно сделать|надо сделать)\s+(?:новый\s+)?проект\b",
        r"\bхочу\s+(?:создать|оформить|спланировать)\s+(?:новый\s+)?проект\b",
    )
)

# Canonical "stop drafting, talk about something else" cues. Reuses
# the topic-shift phrase regex from topic.py philosophically — if
# the user explicitly says "forget the project, let's talk about X"
# that's an EXIT verdict even when we're sitting in drafting mode.
_EXIT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # English — "forget the project" / "never mind the project"
        r"\b(forget|never mind|drop)\s+(?:about\s+)?(?:the\s+)?project\b",
        r"\b(?:stop|cancel)\s+(?:drafting|the draft|the project)\b",
        r"\bexit\s+drafting\b",
        # Russian
        r"\b(забудь|забей)\s+(?:про|о)?\s*проект\b",
        r"\b(прекрати|отмени)\s+(?:черновик|драфт|проект)\b",
        r"\bвыйти\s+из\s+драфта\b",
    )
)


# Canonical "this local turn is really a big async feature" cues
# (ELS-247, trigger-(a) → (b) escalation). Mirrors _ENTER_PATTERNS'
# bilingual structure. Fired by ``classify_local_escalation`` only —
# the chat ENTER/EXIT path never sees these.
_ESCALATE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        # English — needs a PR / review / a tracked ticket
        r"\b(?:open|raise|create|needs?|want)\s+(?:a\s+)?(?:pull request|pr)\b",
        r"\bneeds?\s+(?:a\s+)?(?:code\s+)?review\b",
        r"\b(?:create|file|open)\s+(?:a\s+)?ticket\b",
        r"\b(?:big|large|major)\s+(?:feature|refactor|change|rework)\b",
        r"\b(?:multi[- ]file|across\s+(?:the\s+)?(?:codebase|repo|many files))\b",
        r"\bend[- ]to[- ]end\s+feature\b",
        # Russian — нужен ПР / ревью / тикет / большая фича
        r"\b(?:нужен|нужно|надо|сдела(?:й|ть))\s+(?:пулл?[- ]?реквест|пр|pr)\b",
        r"\bнужно?\s+ревью\b",
        r"\b(?:заведи|создай|оформи)\s+тикет\b",
        r"\b(?:больш(?:ая|ой|ую)|крупн(?:ая|ый|ую))\s+(?:фич[ауи]|задач[ауи]|рефакторинг)\b",
        r"\bпо\s+всему\s+(?:коду|репо|репозиторию)\b",
    )
)


def _matches_any(message: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    if not message:
        return False
    return any(p.search(message) for p in patterns)


def explicit_enter(message: str) -> bool:
    """Cheap "I want to shape a new project" detector."""
    return _matches_any(message, _ENTER_PATTERNS)


def explicit_exit(message: str) -> bool:
    """Cheap "stop drafting" detector."""
    return _matches_any(message, _EXIT_PATTERNS)


def explicit_escalate(message: str) -> bool:
    """Cheap "this belongs in a tracked ticket / PR" detector."""
    return _matches_any(message, _ESCALATE_PATTERNS)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


# Minimum recent message chars before the LLM path is worth a call.
# Short threads ("hi", "what?") don't carry enough signal — fall to
# NEUTRAL and let the user click the dashboard CTA the old way.
_LLM_MIN_CONTEXT_CHARS = 80


class DraftingIntentService:
    """Per-turn classifier; instantiated by the chat handler."""

    def __init__(
        self,
        *,
        settings: Settings,
        client: AgentClient,
    ) -> None:
        self._settings = settings
        self._client = client

    async def classify(
        self,
        *,
        new_user_message: str,
        currently_drafting: bool,
        recent_messages: Sequence[object],
        running_summary: str | None = None,
    ) -> DraftingIntentDecision:
        """Decide whether the assistant should suggest entering or
        exiting drafting mode.

        ``recent_messages`` carries the same ``ChatMessageRow``-shaped
        items the topic-shift classifier reads; ``running_summary``
        is the chat's rolling LLM summary if one exists.

        Returns ``NEUTRAL`` on any failure path — the classifier is
        a *suggestion engine*, never a mode flipper. Safe-fallback
        means no CTA renders.
        """
        message = (new_user_message or "").strip()
        if not message:
            return DraftingIntentDecision(verdict="NEUTRAL")

        # Explicit-phrase fast paths win immediately. Asymmetric:
        # the ENTER patterns only fire when we're NOT already in
        # drafting (no point suggesting "enter drafting" when we're
        # there); the EXIT patterns only fire when we ARE.
        if not currently_drafting and explicit_enter(message):
            return DraftingIntentDecision(
                verdict="ENTER",
                reason=(
                    "You said you'd like to shape a new project — "
                    "switch the thread into drafting mode?"
                ),
            )
        if currently_drafting and explicit_exit(message):
            return DraftingIntentDecision(
                verdict="EXIT",
                reason=(
                    "Sounds like you want to step out of drafting "
                    "and chat about something else."
                ),
            )

        # LLM path — only when the thread has enough recent context
        # for the classifier to ground its verdict. Cheap LLM, but
        # still costs tokens, so bail when there's nothing to read.
        trimmed = _last_turns_text(recent_messages, n_turns=4)
        if len(trimmed) + len(running_summary or "") < _LLM_MIN_CONTEXT_CHARS:
            return DraftingIntentDecision(verdict="NEUTRAL")

        mode_label = (
            "currently in drafting (shape_project) mode"
            if currently_drafting
            else "in normal chat (no drafting intent)"
        )
        prompt = (
            "You are a drafting-intent classifier for a single-window "
            "operator chat. The thread is "
            f"{mode_label}. Decide whether the user's latest message "
            "is asking to (a) start shaping a new project — "
            "verdict ENTER, only valid when NOT already drafting; "
            "(b) stop drafting and chat about something else — "
            "verdict EXIT, only valid when already drafting; "
            "(c) neither.\n\n"
            f"Running summary (may be empty): {running_summary or '—'}\n"
            f"Recent turns:\n{trimmed}\n\n"
            f"Latest user message:\n{message}\n\n"
            "Respond with a single JSON object: "
            '{"verdict": "ENTER"|"EXIT"|"NEUTRAL", '
            '"reason": "short explanation", '
            '"suggested_title": "one-line project title or null"}. '
            "No prose outside the JSON."
        )

        try:
            raw = await self._client.acomplete(
                [ChatMessage(role="user", content=prompt)],
                model=self._settings.agent_model_fast,
                max_tokens=160,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("drafting-intent classifier failed: %s", exc)
            return DraftingIntentDecision(verdict="NEUTRAL")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return DraftingIntentDecision(verdict="NEUTRAL")

        verdict_raw = str(payload.get("verdict") or "").upper().strip()
        if verdict_raw not in {"ENTER", "EXIT", "NEUTRAL"}:
            return DraftingIntentDecision(verdict="NEUTRAL")
        # Asymmetric safety: never echo ENTER when already drafting
        # (would render a "switch to drafting" CTA on a drafting
        # thread) or EXIT when not drafting (inverse case). Cheap
        # belt-and-braces on top of the LLM's verdict.
        if verdict_raw == "ENTER" and currently_drafting:
            return DraftingIntentDecision(verdict="NEUTRAL")
        if verdict_raw == "EXIT" and not currently_drafting:
            return DraftingIntentDecision(verdict="NEUTRAL")

        title = payload.get("suggested_title")
        if not isinstance(title, str) or not title.strip():
            title = None
        return DraftingIntentDecision(
            verdict=verdict_raw,  # type: ignore[arg-type]
            reason=str(payload.get("reason") or "")[:512],
            suggested_title=title,
        )


    async def classify_local_escalation(
        self,
        *,
        operator_ask: str,
        scratch_summary: str | None = None,
    ) -> DraftingIntentDecision:
        """Decide whether a trigger-(a) local scratch ask is really a
        big async feature that belongs in a tracked (b) ticket
        (ELS-247).

        Separate entry point from :meth:`classify` on purpose: the
        chat ENTER/EXIT path and the local-executor ESCALATE path
        share the cost model (regex fast-path → small JSON LLM) but
        never each other's verdicts.

        Returns ``ESCALATE`` or ``NEUTRAL``; ``NEUTRAL`` on every
        failure path. FOUNDER DECISION: this is a *suggestion* engine
        — the local run proceeds either way; the caller renders an
        offer, never a block.
        """
        ask = (operator_ask or "").strip()
        if not ask:
            return DraftingIntentDecision(verdict="NEUTRAL")

        if explicit_escalate(ask):
            return DraftingIntentDecision(
                verdict="ESCALATE",
                reason=(
                    "This sounds like a tracked feature (PR/review/"
                    "multi-file scope) — consider escalating to a "
                    "ticket so it runs the full pipeline."
                ),
            )

        # LLM fallback only when the ask is meaty enough to carry
        # signal; one-liners ("fix typo on the landing") stay local
        # without spending tokens.
        context = f"{ask}\n{scratch_summary or ''}"
        if len(context.strip()) < _LLM_MIN_CONTEXT_CHARS:
            return DraftingIntentDecision(verdict="NEUTRAL")

        prompt = (
            "You are an escalation classifier for a local scratch "
            "coding session (no ticket, no PR, throwaway checkout). "
            "Decide whether the operator's ask is really a BIG async "
            "feature that deserves a tracked ticket with review and a "
            "pull request — verdict ESCALATE — or a small local tweak "
            "— verdict NEUTRAL. Signals for ESCALATE: multi-file "
            "feature work, schema/API changes, anything the operator "
            "says needs review or a PR.\n\n"
            f"Operator ask:\n{ask}\n\n"
            f"Scratch context (may be empty): {scratch_summary or '—'}\n\n"
            "Respond with a single JSON object: "
            '{"verdict": "ESCALATE"|"NEUTRAL", '
            '"reason": "short explanation", '
            '"suggested_title": "one-line ticket title or null"}. '
            "No prose outside the JSON."
        )

        try:
            raw = await self._client.acomplete(
                [ChatMessage(role="user", content=prompt)],
                model=self._settings.agent_model_fast,
                max_tokens=160,
                temperature=0.0,
                response_format={"type": "json_object"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("local-escalation classifier failed: %s", exc)
            return DraftingIntentDecision(verdict="NEUTRAL")

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return DraftingIntentDecision(verdict="NEUTRAL")

        verdict_raw = str(payload.get("verdict") or "").upper().strip()
        if verdict_raw not in {"ESCALATE", "NEUTRAL"}:
            return DraftingIntentDecision(verdict="NEUTRAL")

        title = payload.get("suggested_title")
        if not isinstance(title, str) or not title.strip():
            title = None
        return DraftingIntentDecision(
            verdict=verdict_raw,  # type: ignore[arg-type]
            reason=str(payload.get("reason") or "")[:512],
            suggested_title=title,
        )


__all__ = [
    "DraftingIntentDecision",
    "DraftingIntentService",
    "Verdict",
    "explicit_enter",
    "explicit_escalate",
    "explicit_exit",
]
