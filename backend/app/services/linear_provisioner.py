"""One-shot provisioning of Linear team workflow + labels at OAuth time.

Ship's FSM (``task_intake → ba_requirements → tech_arch_plan →
dev_implementation → qa_manual → pr_review``) doesn't map to Linear's
default 6 states. Two pieces are needed when the operator OAuths
Linear:

1. Ensure a **Review** workflow state exists on the team — Ship's
   ``pr_review`` stage maps to it, and Review is the **human-only**
   gate before Done. Default Linear teams don't have it.
2. Ensure stage labels exist on the team — agents differentiate
   Ship FSM stages within Linear's Todo/In Progress states by the
   ``stage:<fsm>`` label.

The maps produced here (state-id by name, label-id by Ship FSM
stage) get persisted in ``Integration.config`` so the per-call
adapter doesn't have to discover them at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from backend.app.integrations.linear.tracker_adapter import LinearTracker


logger = logging.getLogger(__name__)


# Ship FSM stages that need a label on Linear. Add new stages here
# when patterns declare them in ``spec.fsm_stage``. Order matters —
# the adapter uses adjacency to compute "previous stage done" filters.
SHIP_FSM_STAGES: tuple[str, ...] = (
    "task_intake",
    "ba_requirements",
    "tech_arch_plan",
    "dev_implementation",
    "qa_manual",
    "pr_review",
    "self_heal",
)


# Linear order of the SDLC stages. Self-heal is parallel to the main
# pipeline (any open ticket) so it stays out of the chain.
FSM_STAGE_ORDER: tuple[str, ...] = (
    "task_intake",
    "ba_requirements",
    "tech_arch_plan",
    "dev_implementation",
    "qa_manual",
    "pr_review",
)


def previous_stage(stage: str) -> str | None:
    """Return the SDLC stage immediately before ``stage``, or ``None``
    when ``stage`` is the entry / runs out-of-band.
    """
    if stage not in FSM_STAGE_ORDER:
        return None
    idx = FSM_STAGE_ORDER.index(stage)
    if idx == 0:
        return None
    return FSM_STAGE_ORDER[idx - 1]


# Ship FSM stage → Linear workflow state name. Keys here drive label
# placement: stages mapped to ``In Progress`` get tickets in that
# Linear state; stages mapped to ``Todo`` keep tickets in Todo with
# the label carrying the SDLC stage.
FSM_TO_LINEAR_STATE: dict[str, str] = {
    "task_intake": "Todo",
    "ba_requirements": "Todo",
    "tech_arch_plan": "Todo",
    "dev_implementation": "In Progress",
    "qa_manual": "In Progress",
    # Review is human-only — agents transition INTO it but never pick
    # FROM it.
    "pr_review": "Review",
    # Self-heal runs out-of-band against any open ticket; no specific
    # Linear state.
    "self_heal": "Todo",
}


# Linear workflow state types we expect to drive the SDLC. Used to
# detect whether a team has a Review-ish state under a different name
# (some teams use ``In Review`` / ``QA``); if missing we create it.
_REVIEW_STATE_NAME = "Review"
_REVIEW_STATE_TYPE = "started"  # Linear's "started" bucket; Review is post-In-Progress
_REVIEW_STATE_COLOR = "#F2994A"  # warm orange — matches Linear's review motif


@dataclass(frozen=True)
class ProvisionResult:
    team_id: str
    team_key: str
    state_id_by_name: dict[str, str]
    label_id_by_stage: dict[str, str]


def stage_label_name(stage: str) -> str:
    """Canonical Linear label name for a Ship FSM stage."""
    return f"stage:{stage}"


async def provision_team(
    *,
    tracker: LinearTracker,
    team_key: str,
    stages: tuple[str, ...] = SHIP_FSM_STAGES,
) -> ProvisionResult:
    """Provision states + labels on the chosen Linear team.

    Idempotent: re-running on an already-provisioned team is a no-op
    (skips creates that would conflict by name). Returns the maps the
    OAuth callback persists in ``Integration.config``.
    """
    team = await _resolve_team(tracker, team_key)
    team_id = team["id"]

    state_id_by_name = await _ensure_review_state(tracker, team_id)

    label_id_by_stage: dict[str, str] = {}
    existing_labels = await _list_team_labels(tracker, team_id)
    by_name = {l["name"]: l["id"] for l in existing_labels}

    for stage in stages:
        name = stage_label_name(stage)
        if name in by_name:
            label_id_by_stage[stage] = by_name[name]
            continue
        new_id = await _create_label(tracker, team_id=team_id, name=name)
        label_id_by_stage[stage] = new_id
        logger.info("Linear: created label %r on team %s", name, team_key)

    return ProvisionResult(
        team_id=team_id,
        team_key=team["key"],
        state_id_by_name=state_id_by_name,
        label_id_by_stage=label_id_by_stage,
    )


async def list_teams(tracker: LinearTracker) -> list[dict[str, Any]]:
    """Read every team visible to the OAuth token, with id+key+name."""
    data = await tracker._gql(
        "query ShipListTeams { teams(first: 50) { nodes { id key name } } }"
    )
    return list((data.get("teams") or {}).get("nodes") or [])


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _resolve_team(tracker: LinearTracker, team_key: str) -> dict[str, Any]:
    data = await tracker._gql(
        """query ShipResolveTeam($key: String!) {
          teams(filter: {key: {eq: $key}}) { nodes { id key name } }
        }""",
        {"key": team_key},
    )
    nodes = (data.get("teams") or {}).get("nodes") or []
    if not nodes:
        raise ValueError(f"Linear team {team_key!r} not found.")
    return nodes[0]


async def _ensure_review_state(tracker: LinearTracker, team_id: str) -> dict[str, str]:
    data = await tracker._gql(
        """query ShipTeamStates($teamId: String!) {
          team(id: $teamId) {
            states(first: 50) { nodes { id name type } }
          }
        }""",
        {"teamId": team_id},
    )
    nodes = (
        (((data.get("team") or {}).get("states")) or {}).get("nodes") or []
    )
    by_name = {s["name"]: s["id"] for s in nodes}
    if _REVIEW_STATE_NAME in by_name:
        return by_name

    # Linear requires a position; use a sane "between In Progress and Done"
    # default by leaving position unset — Linear assigns the next slot.
    created = await tracker._gql(
        """mutation ShipCreateState($input: WorkflowStateCreateInput!) {
          workflowStateCreate(input: $input) {
            workflowState { id name }
          }
        }""",
        {
            "input": {
                "teamId": team_id,
                "name": _REVIEW_STATE_NAME,
                "type": _REVIEW_STATE_TYPE,
                "color": _REVIEW_STATE_COLOR,
            }
        },
    )
    new_state = (
        (created.get("workflowStateCreate") or {}).get("workflowState") or {}
    )
    new_id = new_state.get("id")
    if not new_id:
        raise RuntimeError("Linear refused workflowStateCreate for Review state")
    by_name[_REVIEW_STATE_NAME] = new_id
    logger.info("Linear: created %r workflow state on team %s", _REVIEW_STATE_NAME, team_id)
    return by_name


async def _list_team_labels(tracker: LinearTracker, team_id: str) -> list[dict[str, Any]]:
    data = await tracker._gql(
        """query ShipTeamLabels($teamId: String!) {
          team(id: $teamId) {
            labels(first: 250) { nodes { id name } }
          }
        }""",
        {"teamId": team_id},
    )
    return list(
        (((data.get("team") or {}).get("labels")) or {}).get("nodes") or []
    )


async def _create_label(tracker: LinearTracker, *, team_id: str, name: str) -> str:
    created = await tracker._gql(
        """mutation ShipCreateLabel($input: IssueLabelCreateInput!) {
          issueLabelCreate(input: $input) {
            issueLabel { id name }
          }
        }""",
        {"input": {"teamId": team_id, "name": name}},
    )
    label = (
        (created.get("issueLabelCreate") or {}).get("issueLabel") or {}
    )
    new_id = label.get("id")
    if not new_id:
        raise RuntimeError(f"Linear refused issueLabelCreate for {name!r}")
    return str(new_id)
