"""Tracker finite-state-machine seed (Wizard v2 iter 5 & 7).

The Wizard's seed PR drops a human-readable ``.ship/tracker-fsm.md``
documenting how Ship's agents move tickets across statuses in the
connected tracker. Intent is twofold:

* Team-facing transparency — every repo knows which state a ticket
  ends up in when Ship's agents finish, flag blockers, ask for
  clarification, or receive rework requests. No more guessing which
  Linear state "Needs attention" lines up with.
* Agent-facing contract — the run-agent workflow reads the same
  file when resolving "where should I move this ticket?" so the
  runtime behaviour stays pinned to whatever the team reviewed.

Kept as plain markdown (rather than a richer YAML manifest) so the
customer repo's PR review naturally doubles as an FSM spec review.
Iter 7 will add a console page to render the same states alongside
a reconcile-with-tracker button; the file on disk is the source of
truth regardless of UI surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class FsmState:
    """One node in the tracker's workflow graph."""

    id: str
    label: str
    description: str
    # IDs of states this state can transition *to*. "Success" edges
    # only — failure / rework edges are rendered under a separate
    # "return from" section for readability.
    transitions: tuple[str, ...] = ()


# The canonical FSM Ship drives every tracker against. Keep tight —
# changing these is a policy decision that should land with an RFC
# and a migration for existing customer docs.
SHIP_DEFAULT_STATES: Final[tuple[FsmState, ...]] = (
    FsmState(
        id="triage",
        label="Triage",
        description=(
            "Fresh ticket. No agent has touched it; no one has committed "
            "to scope. Ship auto-moves tickets here on creation regardless "
            "of source."
        ),
        transitions=("ready", "blocked", "cancelled"),
    ),
    FsmState(
        id="ready",
        label="Ready",
        description=(
            "Scoped and estimated. Ship picks tickets from here on the "
            "scheduled planning lane; humans can also hand-move a ticket "
            "into Ready to request it next."
        ),
        transitions=("in_progress",),
    ),
    FsmState(
        id="in_progress",
        label="In progress",
        description=(
            "An agent (or a human) has picked up the ticket and is "
            "producing the first PR or draft. The runtime enforces at "
            "most one `in_progress` ticket per agent at a time."
        ),
        transitions=("in_review", "needs_info", "blocked"),
    ),
    FsmState(
        id="in_review",
        label="In review",
        description=(
            "Code + docs posted, awaiting human review. Ship's PR-review "
            "lane auto-comments here; status stays pinned until a "
            "reviewer approves or requests changes."
        ),
        transitions=("rework", "merged", "cancelled"),
    ),
    FsmState(
        id="rework",
        label="Rework",
        description=(
            "The human reviewer requested changes. Ship's rework lane "
            "picks the ticket back up, applies requested edits against "
            "the same PR, and pushes state back to `in_review`. This "
            "loop has no hard cap — the exit condition is explicit "
            "approval."
        ),
        transitions=("in_progress", "in_review"),
    ),
    FsmState(
        id="needs_info",
        label="Needs info",
        description=(
            "The agent or reviewer flagged a missing decision / spec / "
            "access. Ship stops iterating until someone edits the "
            "ticket body (or replies in the tracker thread) and moves "
            "the status back into the loop."
        ),
        transitions=("in_progress",),
    ),
    FsmState(
        id="blocked",
        label="Blocked",
        description=(
            "External dependency (infra change, third-party outage, "
            "legal sign-off). Ship won't retry until an operator flips "
            "it back to `ready` or `in_progress`."
        ),
        transitions=("ready", "cancelled"),
    ),
    FsmState(
        id="merged",
        label="Merged",
        description=(
            "Code shipped to the default branch. Ship logs the merge "
            "commit on the ticket and archives the agent's scratchpad."
        ),
        transitions=("done",),
    ),
    FsmState(
        id="done",
        label="Done",
        description=(
            "Change verified live (deploy check or ops acknowledgement "
            "from the CI lane). Terminal state — Ship won't reopen."
        ),
    ),
    FsmState(
        id="cancelled",
        label="Cancelled",
        description=(
            "Scrapped before merge. No implementation artifacts kept. "
            "Terminal state — re-opening requires a fresh ticket."
        ),
    ),
)


# Per-tracker display hints. None of these are authoritative: they're
# suggestions the wizard writes into the markdown so operators know
# which native status slot Ship will target when it reconciles. Left
# intentionally narrow — if a team runs with non-default Linear
# workflows they edit the file; Ship reads whatever the file says.
_TRACKER_MAPPING_HINTS: Final[dict[str, dict[str, str]]] = {
    "linear": {
        "triage": "Triage (default)",
        "ready": "Backlog → Todo (Ready)",
        "in_progress": "In Progress",
        "in_review": "In Review",
        "rework": "Changes Requested (create if missing)",
        "needs_info": "Blocked / Awaiting Info",
        "blocked": "Blocked",
        "merged": "Done (auto by Linear + GitHub)",
        "done": "Done",
        "cancelled": "Canceled",
    },
    "github": {
        "triage": "Issue open, no labels",
        "ready": "`ready` label",
        "in_progress": "`in-progress` label + assignee set",
        "in_review": "Linked PR in `review_requested` state",
        "rework": "PR `changes_requested` review event",
        "needs_info": "`needs-info` label",
        "blocked": "`blocked` label",
        "merged": "PR merged",
        "done": "Issue closed as `completed`",
        "cancelled": "Issue closed as `not planned`",
    },
    "jira": {
        "triage": "To Do (default)",
        "ready": "Selected for Development",
        "in_progress": "In Progress",
        "in_review": "In Review / Code Review",
        "rework": "Changes Requested (create if missing)",
        "needs_info": "Waiting for Info",
        "blocked": "Blocked",
        "merged": "Done (auto via Smart Commits)",
        "done": "Closed",
        "cancelled": "Won't Do",
    },
}


# Path the seed PR writes. ``shipctl`` reads from the same location
# on every `run`, so operators who edit the file in-repo don't need
# to re-trigger anything — the next lane pick-up sees the new map.
FSM_INSTALL_PATH = ".ship/tracker-fsm.md"


def render_tracker_fsm(
    tracker_kind: str | None,
    *,
    workspace_default_kind: str | None = None,
    repo_full_name: str | None = None,
) -> str:
    """Return the ``tracker-fsm.md`` body for ``tracker_kind``.

    ``tracker_kind`` of ``None`` (or ``"none"``) is still supported —
    we drop the same FSM but annotate the mapping section with "no
    tracker bound yet" so the file still documents Ship's behaviour
    even when the wizard skipped the tracker step. When the repo is
    inheriting the workspace default, ``workspace_default_kind`` is
    surfaced in the header so operators can tell at a glance.
    """

    normalised = (tracker_kind or "").strip().lower()
    if normalised == "":
        normalised = None

    lines: list[str] = []
    lines.append("<!-- ship-cli: tracker-fsm v1 — generated by the wizard seed PR. -->")
    lines.append("<!-- Edit freely; `shipctl` always reads the current committed file. -->")
    lines.append("")
    lines.append("# Ticket lifecycle — Ship FSM")
    lines.append("")
    lines.append(
        "Every Ship-driven ticket moves through this finite-state machine. "
        "The states are intentionally short so humans and agents don't argue "
        "about wording — the transitions below are the only ones Ship "
        "triggers autonomously. Anything else (re-opening a `done` ticket, "
        "jumping from `triage` straight to `done`) requires an operator."
    )
    lines.append("")
    if repo_full_name:
        lines.append(f"**Repository**: `{repo_full_name}`")
    if normalised is None:
        lines.append(
            "**Tracker**: _not connected yet_ — Ship runs the FSM in-repo "
            "(via GitHub issues as a fallback) until a tracker is bound."
        )
    else:
        lines.append(f"**Tracker**: `{normalised}`")
        if (
            workspace_default_kind
            and workspace_default_kind != normalised
        ):
            lines.append(
                "  (overrides the workspace default of "
                f"`{workspace_default_kind}`)"
            )
    lines.append("")

    # ── State list ────────────────────────────────────────────────
    lines.append("## States")
    lines.append("")
    for state in SHIP_DEFAULT_STATES:
        lines.append(f"### `{state.id}` — {state.label}")
        lines.append("")
        lines.append(state.description)
        if state.transitions:
            pretty = ", ".join(f"`{t}`" for t in state.transitions)
            lines.append("")
            lines.append(f"**Next**: {pretty}")
        lines.append("")

    # ── Rework / return-from loop ─────────────────────────────────
    lines.append("## Returning a ticket for rework")
    lines.append("")
    lines.append(
        "When a reviewer requests changes, move the ticket from `in_review` "
        "to `rework`. Ship's rework lane picks it up on the next schedule "
        "tick, applies the requested edits, and flips the status back to "
        "`in_review` when a new revision lands. No limit on rounds — exit "
        "is explicit approval."
    )
    lines.append("")
    lines.append(
        "Direct status edit in the tracker is the *only* signal Ship "
        "listens to for rework. PR comments and reviews are surfaced as "
        "context to the agent but do not trigger a lane on their own."
    )
    lines.append("")

    # ── Mapping table ─────────────────────────────────────────────
    lines.append("## Status mapping")
    lines.append("")
    mapping = _TRACKER_MAPPING_HINTS.get(normalised or "")
    if mapping is None:
        lines.append(
            "_No tracker selected yet — once the repo's tracker binding is "
            "set, re-run the seed PR to refresh this section with the "
            "native status names._"
        )
    else:
        lines.append(
            "These are the native statuses Ship will target for each FSM "
            "state. Rename them on the tracker side if you prefer — just "
            "mirror the rename here so `shipctl` keeps finding the right "
            "slot."
        )
        lines.append("")
        lines.append("| Ship state | Native status |")
        lines.append("| --- | --- |")
        for state in SHIP_DEFAULT_STATES:
            native = mapping.get(state.id, "—")
            lines.append(f"| `{state.id}` | {native} |")
    lines.append("")

    # ── Operator cheat sheet ──────────────────────────────────────
    lines.append("## Operator cheat sheet")
    lines.append("")
    lines.append(
        "- Need Ship to pick up a ticket right now? Move it to `ready` "
        "and run the planning lane (`shipctl run --lane planning`)."
    )
    lines.append(
        "- Want Ship to try again after rework? Flip the status to "
        "`rework`; the scheduled rework lane takes it from there."
    )
    lines.append(
        "- Agent is off the rails on a ticket? Move it to `blocked` — "
        "Ship stops iterating until someone flips it back."
    )
    lines.append(
        "- Ticket is out of scope? `cancelled` is terminal. Open a "
        "fresh ticket rather than reviving a cancelled one so the "
        "audit trail stays readable."
    )
    lines.append("")
    lines.append("<!-- ship-cli:end tracker-fsm -->")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "FSM_INSTALL_PATH",
    "FsmState",
    "SHIP_DEFAULT_STATES",
    "render_tracker_fsm",
]
