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

Plus: the canonical → native projection (the seven Ship lifecycle
states mapped to this team's actual workflow names) is computed here
too. It used to live in ``.ship/config.yml`` for operator editing,
which was wrong — the runtime never read the YAML, and operators
don't want to maintain native column names anyway. Computing it once
at OAuth time and storing it in ``Integration.config.canonical_to_native``
keeps the mapping adapter-internal and decouples tracker mechanics
from process editing.

The maps produced here (state-id by name, label-id by Ship FSM
stage, canonical-to-native) get persisted in ``Integration.config``
so the per-call adapter doesn't have to discover them at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.app.core.config import Settings
from backend.app.integrations.linear.tracker_adapter import LinearTracker
from backend.app.services.canonical_projection import (
    CanonicalState,
    default_canonical_to_native,
)


logger = logging.getLogger(__name__)


# Ship FSM stages that need a label on Linear. Add new stages here
# when patterns declare them in ``spec.fsm_stage``. Order matters —
# the adapter uses adjacency to compute "previous stage done" filters.
SHIP_FSM_STAGES: tuple[str, ...] = (
    # E16/ELS-123 bundle stages. The 7-stage SDLC chain and the
    # 4-stage decomposition chain both collapsed into single bundle
    # invocations:
    #   - ``planning`` absorbs task_intake + tech_arch_plan + qa_arch_plan
    #   - ``validation`` absorbs qa_manual + qa_automation
    #   - ``decomposition`` absorbs wbs + architecture + test_architecture
    #     + tasks on the planning anchor.
    "planning",
    "dev_implementation",
    "devops_implementation",
    "validation",
    "code_review",
    "auto_merge",
    "self_heal",
    "decomposition",
    "planning_done",
    # Legacy pre-E16 labels kept provisioned for in-flight tickets so
    # they still get a Linear label row that the picker can resolve.
    # ELS-124 (cutover) deletes these from the provision list.
    "task_intake",
    "ba_requirements",
    "tech_arch_plan",
    "qa_arch_plan",
    "qa_manual",
    "qa_automation",
    "pr_review",
    "wbs",
    "architecture",
    "test_architecture",
    "tasks",
)


# E16/ELS-123 — bundle stages in execution order. Self-heal is
# parallel to the main pipeline (any open ticket) and stays out of
# the chain. ``previous_stage("validation")`` returns
# ``"dev_implementation"``, ``previous_stage("code_review")`` returns
# ``"validation"``, etc.
FSM_STAGE_ORDER: tuple[str, ...] = (
    "planning",
    "dev_implementation",
    "validation",
    "code_review",
    "auto_merge",
)


# E16/ELS-123 — decomposition is now a single bundle stage that
# emits every project section + child tickets in one run.
# ``planning_done`` stays as the terminal marker the finish hook
# reads to flip the project's dashboard row from Drafts to Parked
# (the PO promotes Parked to Active manually; ELS-81).
DECOMPOSITION_STAGE_ORDER: tuple[str, ...] = (
    "decomposition",
    "planning_done",
)


# Infra / deployment path. Diverges from the SDLC chain at ``planning``
# (planning routes ``infra`` tickets here instead of ``dev_implementation``)
# and rejoins the shared tail (validation → code_review → auto_merge).
# ``previous_stage`` checks this chain LAST, so for tail stages the SDLC
# chain wins — that's only the best-effort breadcrumb-cleanup target, and
# the load-bearing infra bounce is handled by the kind-aware bounce-guard
# in ``agent_runs.finish_agent_run`` (keys off the ``stage:devops_implementation``
# breadcrumb), not by ``previous_stage``.
DEVOPS_STAGE_ORDER: tuple[str, ...] = (
    "planning",
    "devops_implementation",
    "validation",
    "code_review",
    "auto_merge",
)


def previous_stage(stage: str) -> str | None:
    """Return the stage immediately before ``stage``, or ``None`` when
    ``stage`` is the entry of its chain / runs out-of-band.

    Walks the development, decomposition, and devops chains. Each chain
    is independent — there is no transition from the last decomposition
    stage into the first SDLC stage; child tickets that decomposition
    emits enter SDLC at ``task_intake`` as their own entry, not as a
    continuation of the anchor's chain.
    """
    for chain in (FSM_STAGE_ORDER, DECOMPOSITION_STAGE_ORDER, DEVOPS_STAGE_ORDER):
        if stage in chain:
            idx = chain.index(stage)
            if idx == 0:
                return None
            return chain[idx - 1]
    return None


def previous_stages(stage: str) -> list[str]:
    """All possible immediate predecessors of ``stage`` across every
    chain, de-duplicated in first-seen order.

    The SDLC and devops chains share the tail
    (``…→validation→code_review→auto_merge``) but diverge at the
    implementation stage: ``validation`` follows BOTH
    ``dev_implementation`` (feature path) and ``devops_implementation``
    (infra path). The picker's "previous role is done" filter must accept
    EITHER breadcrumb, otherwise an infra ticket that falls back to the
    cron-poller / self-heal picker at a tail stage is silently stranded
    (it carries ``stage:devops_implementation``, never
    ``stage:dev_implementation``). ``previous_stage`` (singular) stays for
    the backwards-cascade label-cleanup callers that want one value.
    """
    seen: list[str] = []
    for chain in (FSM_STAGE_ORDER, DECOMPOSITION_STAGE_ORDER, DEVOPS_STAGE_ORDER):
        if stage in chain:
            idx = chain.index(stage)
            if idx > 0 and chain[idx - 1] not in seen:
                seen.append(chain[idx - 1])
    return seen


# Ship FSM stage → Linear workflow state name. Keys here drive label
# placement: stages mapped to ``In Progress`` get tickets in that
# Linear state; stages mapped to ``Todo`` keep tickets in Todo with
# the label carrying the SDLC stage.
FSM_TO_LINEAR_STATE: dict[str, str] = {
    # E16/ELS-123 bundle stages.
    "planning": "Todo",
    "dev_implementation": "In Progress",
    "devops_implementation": "In Progress",
    "validation": "In Progress",
    # ``code_review`` and ``auto_merge`` both run while the ticket
    # sits in Linear's ``Review`` column. Reviewer leaves feedback
    # (or rubber-stamps), auto-merger then runs its 7-signal gate
    # and squashes via the GitHub API. ``merged`` (the terminal
    # value of auto-merger's ``stage_next``) flips the ticket to
    # Linear ``Done``.
    "code_review": "Review",
    "auto_merge": "Review",
    "merged": "Done",
    # Self-heal runs out-of-band against any open ticket; no specific
    # Linear state.
    "self_heal": "Todo",
    # Decomposition bundle lives on the planning anchor. The anchor
    # sits in ``In Progress`` while the bundle runs and ``Done`` once
    # ``planning_done`` lands. The project body carries the artefacts
    # (WBS / Architecture / Test architecture / Tasks); the finish
    # hook flips the dashboard row Drafts → Parked when
    # ``planning_done`` arrives.
    "decomposition": "In Progress",
    "planning_done": "Done",
    # Legacy pre-E16 stage names — kept so an in-flight ticket carrying
    # ``stage:task_intake`` still maps to a Linear state. ELS-124
    # cutover strips them from the map.
    "task_intake": "Todo",
    "ba_requirements": "Todo",
    "tech_arch_plan": "Todo",
    "qa_arch_plan": "Todo",
    "qa_manual": "In Progress",
    "qa_automation": "In Progress",
    "pr_review": "Review",
    "wbs": "In Progress",
    "architecture": "In Progress",
    "test_architecture": "In Progress",
    "tasks": "In Progress",
}


# Linear workflow state types we expect to drive the SDLC. Used to
# detect whether a team has a Review-ish state under a different name
# (some teams use ``In Review`` / ``QA``); if missing we create it.
_REVIEW_STATE_NAME = "Review"
_REVIEW_STATE_TYPE = "started"  # Linear's "started" bucket; Review is post-In-Progress
_REVIEW_STATE_COLOR = "#F2994A"  # warm orange — matches Linear's review motif


# Out-of-band signal labels that aren't FSM stages but the agent
# pipeline reads/writes. Format: ``<key> -> <Linear label name>``. Keys
# are what the ``/agent-runs/finish`` endpoint and the FSM filter use
# to refer to these labels; the canonical Linear name is what the
# operator sees in the Linear UI.
SIGNAL_LABELS: dict[str, str] = {
    "needs_clarification": "needs:clarification",
    # ``blocked`` is the canonical stop state set by ``agent_run.finish``
    # when an agent reports ``outcome=blocked``. The label is already in
    # OVERLAY_FREEZE_LABEL_PREFIXES below — picker and dispatcher refuse
    # any ticket carrying it. Operator clears the label in Linear to
    # resume work; the webhook ingest catches the label removal and
    # cascades the next dispatch automatically.
    "blocked": "blocked",
}


# Overlay labels that freeze a ticket — the agent picker drops any
# row carrying one of these (ELS-84). Distinct from ``SIGNAL_LABELS``
# (what the agent emits): freeze-overlay labels include both Ship-
# emitted ones and operator-friendly aliases (``blocked`` /
# ``blocker``) so a hand-tagged ticket is also respected.
#
# Match is case-insensitive — for each prefix ``p``, label ``l``
# matches if ``l == p``, ``l.startswith(p + "-")``, or
# ``l.startswith(p + ":")``. Covers ``needs:clarification`` exactly,
# ``blocked``, ``blocked-on-acme``, ``blocked:foo``, etc.
OVERLAY_FREEZE_LABEL_PREFIXES: frozenset[str] = frozenset(
    {
        "needs:clarification",
        "blocked",
    }
)


@dataclass(frozen=True)
class ProvisionResult:
    team_id: str
    team_key: str
    state_id_by_name: dict[str, str]
    label_id_by_stage: dict[str, str]
    signal_label_ids: dict[str, str]
    # Canonical → native projection for this team's workflow. The OAuth
    # callback persists this on ``Integration.config.canonical_to_native``
    # so the adapter can translate "move ticket to canonical state X"
    # into the right native column without re-probing on every call.
    canonical_to_native: dict[str, str] = field(default_factory=dict)
    # Telemetry — useful in logs / audit for debugging projections.
    canonical_resolution_meta: dict[str, Any] = field(default_factory=dict)


def stage_label_name(stage: str) -> str:
    """Canonical Linear label name for a Ship FSM stage."""
    return f"stage:{stage}"


async def provision_team(
    *,
    tracker: LinearTracker,
    team_key: str,
    stages: tuple[str, ...] = SHIP_FSM_STAGES,
    settings: Settings | None = None,
) -> ProvisionResult:
    """Provision states + labels on the chosen Linear team.

    Idempotent: re-running on an already-provisioned team is a no-op
    (skips creates that would conflict by name). Returns the maps the
    OAuth callback persists in ``Integration.config`` — including the
    canonical → native projection (resolved via probe + LLM here so
    the runtime never has to re-discover it).

    ``settings`` is optional; when omitted the canonical resolver
    falls back to the deterministic + heuristic path without burning
    a model call. Tests + cold-deploy environments use that path.
    """
    team = await _resolve_team(tracker, team_key)
    team_id = team["id"]

    state_id_by_name = await _ensure_review_state(tracker, team_id)

    existing_labels = await _list_team_labels(tracker, team_id)
    by_name = {l["name"]: l["id"] for l in existing_labels}

    label_id_by_stage: dict[str, str] = {}
    for stage in stages:
        name = stage_label_name(stage)
        if name in by_name:
            label_id_by_stage[stage] = by_name[name]
            continue
        new_id = await _create_label(tracker, team_id=team_id, name=name)
        label_id_by_stage[stage] = new_id
        by_name[name] = new_id
        logger.info("Linear: created label %r on team %s", name, team_key)

    signal_label_ids: dict[str, str] = {}
    for key, label_name in SIGNAL_LABELS.items():
        if label_name in by_name:
            signal_label_ids[key] = by_name[label_name]
            continue
        new_id = await _create_label(tracker, team_id=team_id, name=label_name)
        signal_label_ids[key] = new_id
        by_name[label_name] = new_id
        logger.info("Linear: created signal label %r on team %s", label_name, team_key)

    canonical_to_native, canonical_meta = await _resolve_canonical_projection(
        tracker=tracker, team_id=team_id, team_key=team_key, settings=settings
    )

    return ProvisionResult(
        team_id=team_id,
        team_key=team["key"],
        state_id_by_name=state_id_by_name,
        label_id_by_stage=label_id_by_stage,
        signal_label_ids=signal_label_ids,
        canonical_to_native=canonical_to_native,
        canonical_resolution_meta=canonical_meta,
    )


async def _resolve_canonical_projection(
    *,
    tracker: LinearTracker,
    team_id: str,
    team_key: str,
    settings: Settings | None,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Probe the live Linear workflow + run the resolver.

    Failures here MUST NOT break OAuth: a deploy without an LLM key,
    a Linear hiccup mid-probe, or a parse error inside the resolver
    just leaves us with the baked default for the tracker. Returns
    ``(mapping, meta)`` where ``meta`` carries telemetry the OAuth
    audit log can show.
    """
    # Lazy imports — ``linear_provisioner`` is on the OAuth hot path
    # and the resolver pulls in the agent client + JSON salvage code
    # we don't need on every Linear write call.
    from backend.app.services.agent.client import pick_default_client
    from backend.app.services.tracker_projection_resolver import (
        TrackerStateInfo,
        resolve_projection,
    )

    fallback = default_canonical_to_native("linear")
    fallback_str = {str(k): v for k, v in fallback.items()}

    try:
        actual_states_raw = await tracker.fetch_workflow_states(team_id=team_id)
        sample_titles = await tracker.fetch_sample_titles_per_state(
            team_id=team_id, per_state_limit=3
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Linear workflow probe failed for team=%s; using baked default mapping",
            team_key,
            exc_info=True,
        )
        return fallback_str, {"source": "default", "reason": "probe_failed"}

    if not actual_states_raw:
        return fallback_str, {"source": "default", "reason": "no_states_returned"}

    actual_states = [
        TrackerStateInfo(
            id=row["id"],
            name=row["name"],
            type=row.get("type") or "",
            samples=sample_titles.get(row["id"], []),
        )
        for row in actual_states_raw
    ]

    client = None
    model = None
    if settings is not None:
        try:
            client = pick_default_client(settings)
            model = settings.agent_model_fast
        except Exception:  # noqa: BLE001
            logger.warning(
                "Couldn't get an LLM client for canonical projection on team=%s; "
                "falling back to deterministic + heuristic.",
                team_key,
                exc_info=True,
            )
            client = None

    try:
        result = await resolve_projection(
            tracker_kind="linear",
            actual_states=actual_states,
            client=client,
            model=model,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Canonical projection resolver crashed for team=%s; using baked default",
            team_key,
            exc_info=True,
        )
        return fallback_str, {"source": "default", "reason": "resolver_crashed"}

    mapping_str = {str(k): v for k, v in result.mapping.items()}
    meta = {
        "source": "resolver",
        "llm_used": result.llm_used,
        "retries": result.retries,
        "deterministic_slots": list(result.deterministic_slots),
        "warnings": list(result.warnings),
    }
    logger.info(
        "Linear canonical projection resolved for team=%s "
        "(deterministic=%d/7, llm_used=%s, retries=%d)",
        team_key,
        len(result.deterministic_slots),
        result.llm_used,
        result.retries,
    )
    return mapping_str, meta


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
