"""Resolve canonical FSM states → native tracker columns via probe + LLM.

Background
----------
Ship pins every process to seven canonical states (``backlog``,
``planning``, ``executing``, ``reviewing``, ``awaiting_input``,
``blocked``, ``closed``). Each tracker (Linear, Jira, GitHub Projects,
Notion) has its own workflow with arbitrary state names ("Selected for
dev", "QA", "Cooked"). The mapping between the two is the
``tracker_projection`` block in ``.ship/config.yml``.

Most defaults work out of the box because Ship bakes a "all-mapped"
seed in :mod:`backend.app.api.v1.routes.processes`. But teams who
customised their workflow (renamed columns, added a Code Review state,
run multilingual workspaces) need their projection re-aligned. That's
this module's job:

1. **Probe** the bound tracker for its actual workflow states +
   sample issue titles per state.
2. **Deterministic first pass** — match the unambiguous slots:
   ``type=backlog`` → ``backlog``, ``type=completed`` / ``canceled``
   → ``closed``, names containing "block" → ``blocked``.
3. **LLM second pass** for the rest — single ``acomplete`` JSON call
   that picks the best-fitting native state for each unresolved
   canonical slot, using the sample titles to disambiguate states
   that share a type bucket.
4. **Validate + retry** — every entry must reference an actual
   tracker state name (or the overlay sentinel for awaiting_input);
   on failure we feed the validation errors back into the prompt and
   try again, capped at two retries to bound cost / latency.

The output is a ``CANONICAL_STATE → str`` mapping ready to be written
into ``.ship/config.yml`` under ``process.tracker_mapping.<kind>``.
The write itself goes through the existing ``/repos/{id}/config/propose``
PR path — this module is purely the proposal generator.

The deterministic pass closes ~50-60% of real cases on its own; the
LLM is needed for the remaining 30-40% (custom names, multiple
``started`` states, non-English workflows). Pure Python in those 50%
is intentional — burning a model call to map ``type=backlog`` to
``backlog`` would be silly.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

from backend.app.services.agent.client import AgentClient, ChatMessage


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


CanonicalState = Literal[
    "backlog",
    "planning",
    "executing",
    "reviewing",
    "awaiting_input",
    "blocked",
    "closed",
]


CANONICAL_STATES: tuple[CanonicalState, ...] = (
    "backlog",
    "planning",
    "executing",
    "reviewing",
    "awaiting_input",
    "blocked",
    "closed",
)


# Mirror of ``_OVERLAY`` in :mod:`backend.app.api.v1.routes.processes`.
# ``awaiting_input`` is a label-overlay rather than a column move on
# every supported tracker — the resolver always projects it to this
# sentinel, the FE renderer knows to render "stays in current column +
# label" when it sees it.
TRACKER_OVERLAY = "__overlay__"


@dataclass(slots=True)
class TrackerStateInfo:
    """One entry from :meth:`LinearTracker.fetch_workflow_states`.

    ``samples`` is the corresponding bucket from
    :meth:`fetch_sample_titles_per_state` — empty list when the state
    has no issues. We keep both fields together so the LLM prompt
    doesn't have to re-zip them at format time.
    """

    id: str
    name: str
    # One of: ``backlog`` / ``unstarted`` / ``started`` / ``completed``
    # / ``canceled`` (Linear's enum). Other adapters normalise to the
    # same values when populating this field.
    type: str
    samples: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ResolveResult:
    """Outcome of :func:`resolve_projection`.

    ``mapping`` is the canonical→native projection ready to be written
    into the YAML; ``llm_used`` records whether we actually called the
    model (lets the route surface a more honest "synced — deterministic"
    vs "synced — LLM-resolved" status to the operator); ``retries``
    counts how many validation cycles we burned; ``warnings`` is a
    free-form list the FE banner can render alongside the diff.
    """

    mapping: dict[CanonicalState, str]
    llm_used: bool
    retries: int
    warnings: list[str]
    deterministic_slots: list[CanonicalState]


async def resolve_projection(
    *,
    tracker_kind: Literal["linear", "jira", "github", "notion"],
    actual_states: Sequence[TrackerStateInfo],
    client: AgentClient | None,
    model: str | None = None,
    max_llm_retries: int = 2,
) -> ResolveResult:
    """Run the full deterministic + LLM + validation pipeline."""

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
        # No model wired — fall back to "guess the closest state name"
        # heuristic so the operator still gets a non-empty PR proposal
        # to react to. The fallback is intentionally dumb (first
        # ``started`` state for executing/reviewing/awaiting_input,
        # etc.) — production deployments always have a model.
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
        # Only ask the LLM about slots we haven't accepted yet. Per-slot
        # retry — keep the valid bits from earlier rounds instead of
        # re-prompting the whole map and risking the model overwriting
        # slots it already got right with worse guesses on retry.
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

        # Carry forward valid slots from this round so the next prompt
        # only re-asks what failed. Empty / malformed entries (and slots
        # that aren't in our unresolved set) are dropped.
        for slot, value in llm_pick.items():
            if slot in accepted_so_far:
                continue
            if value == TRACKER_OVERLAY and slot in {"awaiting_input", "blocked"}:
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

    # Backfill any slots the LLM never settled with the name-aware
    # heuristic, then re-validate. The heuristic always produces a
    # best-effort guess so the merged result is always a complete map.
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
# Words that mark a "this is the review-side state" within Linear's
# ``started`` bucket. Teams put any of these on the column they treat
# as the post-implementation, awaiting-approval slot. Order matters
# only for prompt examples, not matching.
_REVIEW_RE = re.compile(
    r"\b(review|qa|verif|accept|approv|test(ing)?|sign[\-\s]?off)\b",
    re.IGNORECASE,
)


def _resolve_deterministic(
    actual: Sequence[TrackerStateInfo],
) -> dict[CanonicalState, str]:
    """Close the unambiguous canonical slots from state types alone.

    Returns a partial mapping. Slots not covered here go to the LLM.
    The caller relies on ``CANONICAL_STATES`` order for stable iteration
    so the prompt builder lists unresolved entries top-to-bottom.
    """
    by_type: dict[str, list[TrackerStateInfo]] = {}
    for state in actual:
        by_type.setdefault(state.type.lower(), []).append(state)

    out: dict[CanonicalState, str] = {}

    # backlog — ``type=backlog`` is unambiguous on every tracker we
    # support. If the team has multiple backlog states (rare), pick
    # the first; the LLM doesn't see this slot at all.
    backlog_states = [
        s for kind in _BACKLOG_TYPES for s in by_type.get(kind, [])
    ]
    if backlog_states:
        out["backlog"] = backlog_states[0].name

    # closed — ``type=completed`` / ``canceled`` collapse to a single
    # canonical "closed" slot. Prefer the "Done"-shaped name when the
    # team has both, otherwise fall back to the first.
    completed_states = [
        s for kind in _COMPLETED_TYPES for s in by_type.get(kind, [])
    ]
    if completed_states:
        done_like = next(
            (s for s in completed_states if "done" in s.name.lower()),
            None,
        )
        out["closed"] = (done_like or completed_states[0]).name

    # blocked — name-based regex over the whole state list (``type``
    # is rarely useful here; teams mark blocked as ``unstarted`` or
    # ``started`` depending on their workflow).
    blocked_state = next(
        (s for s in actual if _BLOCK_RE.search(s.name)),
        None,
    )
    if blocked_state:
        out["blocked"] = blocked_state.name

    # awaiting_input is always overlay — no column move on Linear/Jira/
    # GitHub/Notion; the adapter sets a label and the ticket stays put.
    out["awaiting_input"] = TRACKER_OVERLAY

    return out


def _resolve_fallback_no_llm(
    unresolved: Sequence[CanonicalState],
    actual: Sequence[TrackerStateInfo],
) -> dict[CanonicalState, str]:
    """Last-resort heuristic when the LLM is unavailable or unhelpful.

    Name-aware: scans started states for review-shaped names ("Review",
    "QA", "In Review", "Acceptance") and pins them to ``reviewing``;
    everything else under ``started`` becomes the ``executing`` slot.
    Falling back on positional order (``started[0]``, ``started[1]``)
    was a real bug — Linear returns states in display position, not
    workflow position, so a team whose Review column sits before
    In Progress in the UI got executing/reviewing swapped.

    Always emits a non-empty mapping for unresolved slots so the
    overall result is a complete 7-key map. ``""`` would round-trip
    as a YAML null which the FE misreads as "deleted entry".
    """
    started_states = [s for s in actual if s.type.lower() in _STARTED_TYPES]
    todo_states = [s for s in actual if s.type.lower() in _TODO_TYPES]

    review_state = next(
        (s for s in started_states if _REVIEW_RE.search(s.name)),
        None,
    )
    exec_state = next(
        (s for s in started_states if s is not review_state),
        # If there is no non-review started state (e.g. the team only
        # has a "Review" column under started), we'd rather collapse
        # executing→that-state than emit an empty value.
        review_state if review_state else (started_states[0] if started_states else None),
    )

    out: dict[CanonicalState, str] = {}
    if "planning" in unresolved:
        out["planning"] = (
            todo_states[0].name if todo_states
            else exec_state.name if exec_state else TRACKER_OVERLAY
        )
    if "executing" in unresolved:
        out["executing"] = (
            exec_state.name if exec_state else TRACKER_OVERLAY
        )
    if "reviewing" in unresolved:
        out["reviewing"] = (
            review_state.name if review_state
            # If there's no review-shaped state name, fall back to the
            # second started state (positional) — better a guess than
            # empty. Adapter overlay would be wrong here because most
            # trackers don't expose a label-based review signal.
            else started_states[1].name if len(started_states) > 1
            else exec_state.name if exec_state
            else TRACKER_OVERLAY
        )

    # Fill any leftover slot with overlay so we always emit the full
    # 7-key map. blocked + awaiting_input are legitimate overlay slots;
    # the others fall back to overlay only when there are no usable
    # tracker states at all.
    for slot in unresolved:
        out.setdefault(slot, TRACKER_OVERLAY)

    return out


# ---------------------------------------------------------------------------
# LLM pass
# ---------------------------------------------------------------------------


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
    tracker_kind: str = "linear",
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
        # Best-effort salvage — same approach as the distiller's
        # classifier when a model emits trailing prose.
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


# Canonical slots where ``__overlay__`` is a legitimate value. Both
# ``awaiting_input`` and ``blocked`` are typically label-driven on real
# trackers (Linear's signal label, Jira's "needs:info" label) — teams
# rarely add a dedicated workflow column for them. Forcing the LLM to
# pick a column when overlay is the right answer made retries spin
# until the model gave up.
_OVERLAY_ALLOWED_SLOTS: frozenset[CanonicalState] = frozenset(
    {"awaiting_input", "blocked"}
)


def validate_mapping(
    mapping: dict[CanonicalState, str],
    actual_state_names: Iterable[str],
) -> list[str]:
    """Return a list of human-readable error strings; empty == valid.

    Errors that fail the pipeline (and trigger an LLM retry):
    - Missing canonical slot.
    - Value isn't one of the actual tracker state names (and isn't the
      overlay sentinel for an overlay-allowed slot).
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


__all__ = [
    "CANONICAL_STATES",
    "CanonicalState",
    "ResolveResult",
    "TRACKER_OVERLAY",
    "TrackerStateInfo",
    "resolve_projection",
    "validate_mapping",
]
