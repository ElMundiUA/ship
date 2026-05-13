"""Default canonical → native tracker state projections.

The seven Ship canonical lifecycle states (``backlog``, ``planning``,
``executing``, ``reviewing``, ``awaiting_input``, ``blocked``,
``closed``) are the runtime contract between the agent / CLI and the
tracker adapter. Every adapter knows how to translate "move this ticket
to canonical state X" into the right native action — moving the ticket
between workflow columns, swapping a label, etc.

Each tracker has a default projection baked in here so a freshly-OAuthed
team lights up with zero operator clicks. Teams whose workflow drifts
from the default (renamed columns, extra states) get a per-team
override stored in ``Integration.config.canonical_to_native``, populated
once at OAuth time by ``linear_provisioner`` (and the equivalent on
other adapters once they land).

The mapping intentionally does NOT live in ``.ship/config.yml``. That
was tried earlier and walked back: a YAML field that only the adapter
reads becomes maintenance burden for operators with no payoff. Keeping
it adapter-internal lets us migrate trackers (Linear → Jira) without
touching customer process configs and keeps process modifications
decoupled from tracker mechanics.

Overlay sentinel — ``__overlay__`` — marks slots the adapter resolves
via a label rather than a column move (``awaiting_input`` and
``blocked`` are typical because most trackers don't have dedicated
columns for them).
"""

from __future__ import annotations

from typing import Final, Literal


CanonicalState = Literal[
    "backlog",
    "planning",
    "executing",
    "reviewing",
    "awaiting_input",
    "blocked",
    "closed",
]


CANONICAL_STATES: Final[tuple[CanonicalState, ...]] = (
    "backlog",
    "planning",
    "executing",
    "reviewing",
    "awaiting_input",
    "blocked",
    "closed",
)


# Magic value for slots the adapter resolves via labels rather than
# native column moves. The runtime treats overlay slots as "set the
# label, leave the column alone".
TRACKER_OVERLAY: Final[str] = "__overlay__"


# ---------------------------------------------------------------------------
# Per-tracker baked defaults
# ---------------------------------------------------------------------------


CANONICAL_TO_LINEAR: Final[dict[CanonicalState, str]] = {
    "backlog": "Backlog",
    "planning": "Todo",
    "executing": "In Progress",
    "reviewing": "In Review",
    "awaiting_input": TRACKER_OVERLAY,
    "blocked": TRACKER_OVERLAY,
    "closed": "Done",
}


CANONICAL_TO_JIRA: Final[dict[CanonicalState, str]] = {
    "backlog": "To Do",
    "planning": "To Do",
    "executing": "In Progress",
    "reviewing": "In Review",
    "awaiting_input": TRACKER_OVERLAY,
    "blocked": TRACKER_OVERLAY,
    "closed": "Done",
}


CANONICAL_TO_GITHUB: Final[dict[CanonicalState, str]] = {
    # GitHub Issues only has open/closed; the per-canonical bucket lives
    # in a ``state:<canonical>`` label provisioned at attach time.
    "backlog": "open",
    "planning": "open",
    "executing": "open",
    "reviewing": "open",
    "awaiting_input": TRACKER_OVERLAY,
    "blocked": TRACKER_OVERLAY,
    "closed": "closed",
}


CANONICAL_TO_NOTION: Final[dict[CanonicalState, str]] = {
    "backlog": "Not started",
    "planning": "Not started",
    "executing": "In progress",
    "reviewing": "In progress",
    "awaiting_input": TRACKER_OVERLAY,
    "blocked": TRACKER_OVERLAY,
    "closed": "Done",
}


_DEFAULTS_BY_KIND: Final[dict[str, dict[CanonicalState, str]]] = {
    "linear": CANONICAL_TO_LINEAR,
    "jira": CANONICAL_TO_JIRA,
    "github": CANONICAL_TO_GITHUB,
    "notion": CANONICAL_TO_NOTION,
}


def default_canonical_to_native(kind: str) -> dict[CanonicalState, str]:
    """Return a fresh copy of the baked default for ``kind``.

    Returns an empty dict for unknown trackers; callers should treat
    that as "no defaults — adapter must derive everything at runtime".
    """
    base = _DEFAULTS_BY_KIND.get(kind.lower())
    return dict(base) if base else {}


__all__ = [
    "CANONICAL_STATES",
    "CANONICAL_TO_GITHUB",
    "CANONICAL_TO_JIRA",
    "CANONICAL_TO_LINEAR",
    "CANONICAL_TO_NOTION",
    "CanonicalState",
    "TRACKER_OVERLAY",
    "default_canonical_to_native",
]
