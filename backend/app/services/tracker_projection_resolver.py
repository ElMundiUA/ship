"""Resolve canonical → native tracker projection at OAuth time.

Lives next to :mod:`linear_provisioner` (and the equivalent for other
trackers, once they land). The provisioner calls
:func:`resolve_projection` after it has authenticated to the team's
tracker; we probe the live workflow states + a few sample issue titles
per state, run a deterministic + LLM hybrid resolver, and return a
seven-entry ``canonical → native`` map ready for storage on
``Integration.config.canonical_to_native``.

Pipeline:

1. **Deterministic pass** — closes ~50% of slots without burning model
   tokens: type=backlog → backlog, completed/canceled → closed, name
   regex on "block|hold" → blocked, awaiting_input always overlay
   (label-driven on every supported tracker).
2. **LLM pass** — single ``acomplete`` JSON call for whatever
   deterministic didn't close. Sample issue titles per state are
   included so the model can disambiguate states that share a type
   bucket (multiple ``started`` states like "In Progress" / "Code
   Review" / "QA").
3. **Validation + per-slot retry** — every entry must reference an
   actual tracker state name (or the overlay sentinel for slots that
   allow it). On validation failure the loop carries forward the
   valid slots from the previous attempt and only re-prompts the
   failed ones; capped at two retries to bound cost.
4. **Heuristic fallback** for whatever the LLM still hasn't settled
   after retries — name-aware (review/qa/verify keywords pin
   reviewing; everything else under started → executing). Always
   produces a non-empty map.

Output is stored in ``Integration.config.canonical_to_native`` once
at OAuth callback time. Operators don't see / edit it; if their
workflow drifts later (new column added on Linear, rename), they
re-trigger via a "Re-probe workflow" admin action that calls back
into :func:`resolve_projection` with the same shape.

The mapping deliberately does NOT live in ``.ship/config.yml`` —
runtime never read that field, and putting it under operator-edited
config was a misplacement. See commit 46f799e for the walk-back.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from backend.app.services.agent.client import AgentClient, ChatMessage
from backend.app.services.canonical_projection import (
    CANONICAL_STATES,
    TRACKER_OVERLAY,
    CanonicalState,
    default_canonical_to_native,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class TrackerStateInfo:
    """One entry from the tracker's workflow probe.

    ``samples`` is a few recent issue titles from that state — used as
    LLM disambiguation evidence. ``type`` follows Linear's enum
    (``backlog``/``unstarted``/``started``/``completed``/``canceled``);
    other adapters normalise to the same enum when populating this.
    """

    id: str
    name: str
    type: str
    samples: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResolveResult:
    """Outcome of :func:`resolve_projection`.

    ``mapping`` is the canonical→native map ready to be persisted on
    ``Integration.config.canonical_to_native``. ``llm_used`` records
    whether the model was actually called (lets the OAuth callback log
    show "synced — deterministic" vs "synced — LLM-resolved").
    ``warnings`` is empty on the happy path; populated when the LLM
    failed validation and we fell back to the heuristic for some slots.
    """

    mapping: dict[CanonicalState, str]
    llm_used: bool
    retries: int
    warnings: list[str]
    deterministic_slots: list[CanonicalState]


async def resolve_projection(
    *,
    tracker_kind: str,
    actual_states: Sequence[TrackerStateInfo],
    client: AgentClient | None,
    model: str | None = None,
    max_llm_retries: int = 2,
) -> ResolveResult:
    """Run the full deterministic + LLM + validation pipeline.

    Always returns a complete 7-entry mapping. ``client=None`` skips
    the LLM call and uses the heuristic fallback for unresolved slots
    — useful in tests, and as a safety net for OAuth callbacks that
    land before AGENT_MODEL env is wired in a fresh deployment.
    """
    deterministic = _resolve_deterministic(actual_states)
    unresolved: list[CanonicalState] = [
        s for s in CANONICAL_STATES if s not in deterministic
    ]

    if not unresolved:
        return ResolveResult(
            mapping=deterministic,
            llm_used=False,
            retries=0,
            warnings=[],
            deterministic_slots=list(deterministic.keys()),
        )

    if client is None:
        fallback = _resolve_fallback_no_llm(unresolved, actual_states)
        return ResolveResult(
            mapping={**deterministic, **fallback},
            llm_used=False,
            retries=0,
            warnings=["LLM client unavailable — used heuristic fallback for unresolved slots."],
            deterministic_slots=list(deterministic.keys()),
        )

    actual_names = {s.name for s in actual_states}
    prior_errors: list[str] = []
    accepted_so_far: dict[CanonicalState, str] = {}
    retries_used = 0
    for attempt in range(max_llm_retries + 1):
        slots_to_ask = [s for s in unresolved if s not in accepted_so_far]
        if not slots_to_ask:
            break
        try:
            llm_pick = await _resolve_with_llm(
                client=client,
                model=model,
                unresolved=slots_to_ask,
                actual=actual_states,
                prior_errors=prior_errors,
                tracker_kind=tracker_kind,
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "tracker projection LLM call failed (attempt %d)", attempt, exc_info=True
            )
            break

        retries_used = attempt
        candidate: dict[CanonicalState, str] = {
            **deterministic,
            **accepted_so_far,
            **llm_pick,
        }
        errors = validate_mapping(candidate, actual_names)
        if not errors:
            return ResolveResult(
                mapping=candidate,
                llm_used=True,
                retries=attempt,
                warnings=[],
                deterministic_slots=list(deterministic.keys()),
            )

        # Carry forward valid slots so the next prompt only re-asks
        # what failed. Per-slot retry: previous full-map retry let the
        # model regress on slots it had already gotten right.
        for slot, value in llm_pick.items():
            if slot in accepted_so_far:
                continue
            if value == TRACKER_OVERLAY and slot in _OVERLAY_ALLOWED_SLOTS:
                accepted_so_far[slot] = value
                continue
            if isinstance(value, str) and value in actual_names:
                accepted_so_far[slot] = value
        prior_errors = errors
        logger.info(
            "tracker projection LLM proposal failed validation, retrying %d slot(s): %s",
            len([s for s in unresolved if s not in accepted_so_far]),
            "; ".join(errors),
        )

    leftover = [s for s in unresolved if s not in accepted_so_far]
    fallback = _resolve_fallback_no_llm(leftover, actual_states)
    merged_fallback: dict[CanonicalState, str] = {
        **deterministic,
        **accepted_so_far,
        **fallback,
    }
    fallback_warnings: list[str] = []
    if leftover:
        fallback_warnings.append(
            "LLM didn't settle on a valid mapping for "
            + ", ".join(leftover)
            + " after retries — used heuristic fallback for those slots."
        )
    return ResolveResult(
        mapping=merged_fallback,
        llm_used=True,
        retries=retries_used,
        warnings=fallback_warnings,
        deterministic_slots=list(deterministic.keys()),
    )


# ---------------------------------------------------------------------------
# Deterministic pass
# ---------------------------------------------------------------------------


_BACKLOG_TYPES = {"backlog"}
_TODO_TYPES = {"unstarted", "todo"}
_STARTED_TYPES = {"started", "in_progress", "in-progress"}
_COMPLETED_TYPES = {"completed", "canceled", "cancelled", "done"}

_BLOCK_RE = re.compile(r"\b(block|blocker|blocked|hold)\b", re.IGNORECASE)
# Words a team typically uses for the "implementation done, awaiting
# review" column. Used by the heuristic fallback to disambiguate
# multiple ``started`` states without burning a model call.
_REVIEW_RE = re.compile(
    r"\b(review|qa|verif|accept|approv|test(ing)?|sign[\-\s]?off)\b",
    re.IGNORECASE,
)


def _resolve_deterministic(
    actual: Sequence[TrackerStateInfo],
) -> dict[CanonicalState, str]:
    """Close unambiguous canonical slots from state types alone.

    Always returns ``awaiting_input`` as overlay (label-driven on
    every supported tracker). Backlog / closed / blocked land here
    when the team has the obvious type or name signal; otherwise
    the LLM (or the heuristic fallback) picks them up.
    """
    by_type: dict[str, list[TrackerStateInfo]] = {}
    for state in actual:
        by_type.setdefault(state.type.lower(), []).append(state)

    out: dict[CanonicalState, str] = {}

    backlog_states = [s for kind in _BACKLOG_TYPES for s in by_type.get(kind, [])]
    if backlog_states:
        out["backlog"] = backlog_states[0].name

    completed_states = [s for kind in _COMPLETED_TYPES for s in by_type.get(kind, [])]
    if completed_states:
        done_like = next(
            (s for s in completed_states if "done" in s.name.lower()),
            None,
        )
        out["closed"] = (done_like or completed_states[0]).name

    blocked_state = next(
        (s for s in actual if _BLOCK_RE.search(s.name)),
        None,
    )
    if blocked_state:
        out["blocked"] = blocked_state.name

    out["awaiting_input"] = TRACKER_OVERLAY

    return out


def _resolve_fallback_no_llm(
    unresolved: Sequence[CanonicalState],
    actual: Sequence[TrackerStateInfo],
) -> dict[CanonicalState, str]:
    """Name-aware heuristic for slots the LLM didn't settle.

    Scans ``started`` states for review-shaped names (review/qa/verify/
    accept/sign-off) and pins them to ``reviewing``; everything else
    under started becomes the executing slot. Earlier positional
    fallback (``started[0]``, ``started[1]``) was a real bug — Linear
    returns states in display position, not workflow position, so
    teams whose Review column sits above In Progress in the UI got
    the two slots swapped.
    """
    started_states = [s for s in actual if s.type.lower() in _STARTED_TYPES]
    todo_states = [s for s in actual if s.type.lower() in _TODO_TYPES]

    review_state = next(
        (s for s in started_states if _REVIEW_RE.search(s.name)),
        None,
    )
    exec_state = next(
        (s for s in started_states if s is not review_state),
        review_state if review_state else (started_states[0] if started_states else None),
    )

    out: dict[CanonicalState, str] = {}
    if "planning" in unresolved:
        out["planning"] = (
            todo_states[0].name if todo_states
            else exec_state.name if exec_state
            else TRACKER_OVERLAY
        )
    if "executing" in unresolved:
        out["executing"] = (
            exec_state.name if exec_state else TRACKER_OVERLAY
        )
    if "reviewing" in unresolved:
        out["reviewing"] = (
            review_state.name if review_state
            else started_states[1].name if len(started_states) > 1
            else exec_state.name if exec_state
            else TRACKER_OVERLAY
        )

    for slot in unresolved:
        out.setdefault(slot, TRACKER_OVERLAY)
    return out


# ---------------------------------------------------------------------------
# LLM pass
# ---------------------------------------------------------------------------


_OVERLAY_ALLOWED_SLOTS: frozenset[CanonicalState] = frozenset(
    {"awaiting_input", "blocked"}
)


_SYSTEM_PROMPT = (
    "You map a tracker's workflow states to Ship's seven canonical "
    "lifecycle states. You must return strict JSON. Only pick from the "
    "tracker state names provided — never invent new ones. The overlay "
    "sentinel \"__overlay__\" is valid for two slots: \"awaiting_input\" "
    "and \"blocked\". Use overlay when the tracker has no dedicated "
    "column for that lifecycle state and the team marks it via a label "
    "or tag instead — most Linear/Jira teams ship without explicit "
    "Awaiting/Blocked columns. Prefer states whose names or sample "
    "titles match the canonical state's intent. Reviewing means "
    "implementation done + waiting for a human to approve, distinct "
    "from Executing where the agent or human is still actively working."
)


_CANONICAL_GUIDE = """\
Canonical states (intent + signals):
- backlog: not yet planned. Tickets recently filed, no commitment.
- planning: scoped, accepted, ready to be picked up. \"Todo\" /
  \"Selected for dev\" / \"Refining\".
- executing: someone is actively working on it. \"In Progress\".
- reviewing: implementation is done; awaiting human / QA review.
  \"In Review\" / \"Code Review\" / \"QA\" / \"Acceptance\".
- awaiting_input: blocked on a question / decision / external answer.
  Most trackers don't have a column for this; map it to overlay
  (\"__overlay__\") so the adapter uses a label instead.
- blocked: blocked on an external dependency (infra, third party,
  legal). Distinct from awaiting_input because the user hasn't been
  asked anything. Also map to overlay (\"__overlay__\") if the
  tracker has no dedicated Blocked column — that's the common case.
- closed: terminal. \"Done\" / \"Cancelled\" / \"Won't Do\".
"""


def _build_user_prompt(
    *,
    tracker_kind: str,
    unresolved: Sequence[CanonicalState],
    actual: Sequence[TrackerStateInfo],
    prior_errors: Sequence[str],
) -> str:
    state_lines: list[str] = []
    for s in actual:
        sample_part = ""
        if s.samples:
            joined = " | ".join(t.replace("\n", " ").strip() for t in s.samples[:3])
            sample_part = f" | recent: {joined}"
        state_lines.append(f"- {s.name!r} (type={s.type or 'unknown'}){sample_part}")

    body = [
        f"Tracker: {tracker_kind}.",
        "",
        _CANONICAL_GUIDE,
        "Tracker workflow states (pick names verbatim from this list):",
        *state_lines,
        "",
        "Unresolved canonical states (pick one tracker name per entry):",
        *[f"- {name}" for name in unresolved],
        "",
        "Rules:",
        "- The value MUST be a verbatim copy of one of the tracker state names listed above (case- and whitespace-exact).",
        '- The string "__overlay__" is the ONLY allowed non-state value, and only for "awaiting_input" or "blocked" when the tracker has no dedicated column for them.',
        "- If multiple states match a canonical, pick the one whose name or sample titles best fits the canonical's intent (Reviewing prefers names with review/qa/verify/accept/sign-off; Executing prefers names like in-progress/doing).",
        "",
        "Return JSON with this exact shape:",
        '{ "mapping": { "<canonical>": "<tracker state name | __overlay__>" } }',
    ]
    if prior_errors:
        body.extend(
            [
                "",
                "Your previous answer failed validation. Errors:",
                *[f"- {err}" for err in prior_errors],
                "Fix all of these and return a clean JSON object.",
            ]
        )
    return "\n".join(body)


async def _resolve_with_llm(
    *,
    client: AgentClient,
    model: str | None,
    unresolved: Sequence[CanonicalState],
    actual: Sequence[TrackerStateInfo],
    prior_errors: Sequence[str],
    tracker_kind: str,
) -> dict[CanonicalState, str]:
    messages = [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=_build_user_prompt(
                tracker_kind=tracker_kind,
                unresolved=unresolved,
                actual=actual,
                prior_errors=prior_errors,
            ),
        ),
    ]
    raw = await client.acomplete(
        messages,
        model=model,
        max_tokens=400,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    payload = _parse_json(raw)
    mapping_obj = payload.get("mapping") or payload
    out: dict[CanonicalState, str] = {}
    for slot in unresolved:
        value = mapping_obj.get(slot)
        if isinstance(value, str) and value.strip():
            out[slot] = value.strip()
    return out


def _parse_json(raw: str) -> dict[str, Any]:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", str(raw or ""), re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
        return {}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_mapping(
    mapping: dict[CanonicalState, str],
    actual_state_names: Iterable[str],
) -> list[str]:
    """Return human-readable error strings; empty == valid.

    Errors that fail the pipeline (and trigger an LLM retry):
    - Missing canonical slot.
    - Value isn't one of the actual tracker state names (and isn't
      the overlay sentinel for an overlay-allowed slot).
    - Overlay sentinel used for a slot other than ``awaiting_input``
      or ``blocked``.
    """
    errors: list[str] = []
    actual = set(actual_state_names)

    for slot in CANONICAL_STATES:
        if slot not in mapping:
            errors.append(f"missing entry for canonical state {slot!r}")
            continue
        value = mapping[slot]
        if value == TRACKER_OVERLAY:
            if slot not in _OVERLAY_ALLOWED_SLOTS:
                errors.append(
                    f"overlay sentinel is only valid for awaiting_input/blocked, not {slot!r}"
                )
            continue
        if not value:
            errors.append(f"empty value for {slot!r}")
            continue
        if value not in actual:
            errors.append(
                f"{slot!r} → {value!r} is not one of the tracker's workflow states"
            )

    return errors


# ---------------------------------------------------------------------------
# Convenience: backed-by-default merge
# ---------------------------------------------------------------------------


def merge_with_default(
    tracker_kind: str,
    resolved: dict[CanonicalState, str],
) -> dict[CanonicalState, str]:
    """Layer ``resolved`` on top of the baked default for ``tracker_kind``.

    The default is keyed by canonical state, so any slot that's in the
    resolved map wins — slots not in resolved fall through to the
    baked default. Useful when the resolver only re-validated a subset
    of the seven states (e.g. on a partial re-probe).
    """
    base = default_canonical_to_native(tracker_kind)
    base.update(resolved)
    return base


__all__ = [
    "ResolveResult",
    "TrackerStateInfo",
    "merge_with_default",
    "resolve_projection",
    "validate_mapping",
]
