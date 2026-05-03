"""Lane recipes — the catalog-derived source of truth for seeded lanes.

Replaces the pre-RFC-0008 ``default_pipelines`` module which kept
every seeded lane as a hand-maintained :class:`DefaultPipelineSpec`
in Python.  Post-C3.3 the five baked-in lanes are assembled from:

1. **Pattern catalog** — any pattern with ``modes`` including
   ``lane`` and a ``spec.lane_id`` slug contributes one entry.
   Multiple patterns sharing the same ``lane_id`` merge into a
   multi-pattern lane (RFC-0008 C3.2 ``fanout`` semantics apply).
2. **Non-pattern specials** — :data:`_EXTRA_RECIPES` covers the
   two stable lanes that don't map to a single pattern today
   (``code_map`` — resolver-only, ``tech_debt`` — multi-pattern
   recipe that will pick up its patterns once RFC-0008 C5 lands
   the ``scan-*`` family).

Preset gating (previously ``PRESET_ENABLED_KINDS``) now lives on
each pattern's ``spec.enabled_on_install.presets`` mapping — the
recipe builder folds them into the per-lane
:attr:`LaneRecipe.preset_enabled` dict. The specials keep their
gating inline in this module.

``seed_default_pipelines`` / ``resolve_enabled_lane_ids`` preserve
the pre-RFC call signatures so existing routes and tests don't
need an import sweep. The DB column holding the lane slug was
renamed from ``Pipeline.kind`` to ``Pipeline.lane_id`` by C3.4 so
the seeded-lane surface and the config-derived :class:`Lane` model
now share one vocabulary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Final, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.pipelines import Pipeline


# ---------------------------------------------------------------------------
# Default bundle — the canonical Plays installed in every new repo's
# ``.ship/config.yml`` once the Wave-8 wizard collapses the 14-preset menu
# (RFC-0010 / inbox redesign). Sibling A's preset-collapse work imports
# this tuple to rewrite ``lane_recipes`` against a single bundle instead
# of the per-preset matrix below. Order is the recommended display order
# in the "Confirm bootstrap" UI: PR-attached first (immediate value),
# then scheduled scanners (proves the loop), then release-time + the
# one-shot knowledge seed.
# ---------------------------------------------------------------------------

DEFAULT_BUNDLE: tuple[str, ...] = (
    "role-qa-architect",
    "role-tech-architect",
    "role-security-officer",
    "flow-daily-retro",
    "flow-learning-capture",
    "role-intake",
    "role-ba",
    "role-developer",
    "flow-qa-acceptance",
    "op-workflow-self-heal",
)
"""Canonical set of Plays installed in every new repo's ``.ship/config.yml``.

Replaces the 14-preset menu introduced in P1; chosen to give every new
repo immediate signal on first PR, prove the scheduled-scan loop works
without manual configuration, and seed the per-repo knowledge bucket.

See ``documentation/internal/inbox-redesign-planning.md`` §2 for the
selection criteria.
"""


DEFAULT_BUNDLE_REASONS: dict[str, str] = {
    "role-qa-architect": "Reviews test architecture daily and plans QA coverage for active work.",
    "role-tech-architect": "Reviews architecture daily and plans implementation for ready work.",
    "role-security-officer": "Runs daily security review and routes actionable findings.",
    "flow-daily-retro": "Reports the last 24 hours of repository and delivery activity to Ship.",
    "flow-learning-capture": "Reviews recent runs and suggests workflow or prompt improvements.",
    "role-intake": "Checks new work for enough information before BA / architecture planning.",
    "role-ba": "Writes requirements for tasks that are ready for BA.",
    "role-developer": "Implements tasks that passed requirements and architecture planning.",
    "flow-qa-acceptance": "Covers manual QA and acceptance gaps before automation picks them up.",
    "op-workflow-self-heal": "Checks hourly whether Ship workflows are healthy and reports fixes.",
}
"""Short, human-readable reason for including each :data:`DEFAULT_BUNDLE` entry.

Surfaced in the wizard's "Confirm bootstrap" step (Wave 8c) so the
user understands *why* each Play landed in their config. Each reason
is ≤120 characters and avoids internal vocabulary (``profile``,
``lane``, ``pattern_id``) so it reads as plain English to a first-time
operator.
"""


DEFAULT_SEED_LANES: Final[dict[str, dict[str, object]]] = {
    # Canonical six routines — the only set the editor surfaces and the
    # only set the seed bundle writes into a fresh ``.ship/config.yml``.
    # IDs are byte-stable contracts: shipctl reads them out of the repo
    # config and renames here would break already-seeded repos, so they
    # match the short labels exactly. Times spread across UTC so the
    # operator doesn't see four agents stacked on one hour.
    #
    # ``specialist`` is the Phase-2.4 vocabulary: shipctl run resolves
    # the slug through ``GET /v1/.../agent-roles/{slug}/resolve`` (workspace
    # override → Ship default file). Slugs match
    # ``backend/app/resources/agent_roles/<slug>.md`` exactly.
    #
    # Specialist roles like intake / BA / tech-architect / QA / developer
    # also live as ``process.specialists`` for the Capacity calendar
    # (FSM-driven ticket pickup), but that's a separate surface from the
    # cron-driven routines below.
    "security_review": {
        "kind": "schedule",
        "cron": "0 6 * * *",   # 06:00 UTC
        "specialist": "security-officer",
    },
    "daily": {
        "kind": "schedule",
        "cron": "0 9 * * *",   # 09:00 UTC — morning digest
        # ``flow-daily-retro`` is a routine prompt (not a role); Step
        # C-2 renames it to a top-level ``daily-retro`` agent role
        # alongside the role-* family.
        "specialist": "daily-retro",
    },
    "tech_review": {
        "kind": "schedule",
        "cron": "0 12 * * *",  # 12:00 UTC
        "specialist": "tech-architect",
    },
    "qa_review": {
        "kind": "schedule",
        "cron": "0 15 * * *",  # 15:00 UTC
        "specialist": "qa-architect",
    },
    "retro": {
        "kind": "schedule",
        "cron": "0 18 * * *",  # 18:00 UTC — end-of-day retro
        "specialist": "learning-capture",
    },
    "healthcheck": {
        "kind": "schedule",
        "cron": "0 */2 * * *",  # every 2h
        "specialist": "workflow-self-heal",
    },
}
"""Canonical routines written into a fresh ``.ship/config.yml``.

These six are the **only** routine ids the editor surfaces and the only
ones the seed bundle ever writes. ``shipctl`` reads them from the repo
config and treats any other id as drift (logs a warning, still runs).
"""

# Display labels keyed by the canonical routine id. Backend includes
# this in the process projection so the FE picker can show short verbs
# ("Daily" / "Retro" / "Healthcheck") instead of the snake-case ids.
ROUTINE_DISPLAY_LABELS: Final[dict[str, str]] = {
    "daily": "Daily",
    "retro": "Retro",
    "healthcheck": "Healthcheck",
    "tech_review": "Tech review",
    "qa_review": "QA review",
    "security_review": "Security review",
}

# Legacy ids ever produced by older seed versions. Loading code logs a
# drift warning when it sees one of these. Kept here as a single source
# of truth so the warning text doesn't go stale.
LEGACY_ROUTINE_IDS: Final[frozenset[str]] = frozenset(
    {
        "daily_security_review",
        "daily_digest",
        "daily_technical_architecture_review",
        "daily_architecture_tests_review",
        "daily_retro",
        "self_heal",
        "task_intake",
        "ba_requirements",
        "tech_arch_plan",
        "qa_arch_plan",
        "dev_implementation",
        "qa_manual",
        "qa_automation",
        "daily_standup",
        "tech_debt",
        "code_map",
        "flow_release_notes",
        "scan_docs_freshness",
        "scan_license_deps",
        "scan_security_deps",
    }
)


def default_seed_lanes() -> dict[str, dict[str, object]]:
    return {lane_id: dict(body) for lane_id, body in DEFAULT_SEED_LANES.items()}


# ---------------------------------------------------------------------------
# Preset catalog (P5-01 collapse).
#
# Pre-Wave 8a the catalog enumerated 14 presets ("web-app", "api-backend",
# "mobile-app", …). The Plays/Inbox redesign removes the user-facing
# preset choice entirely — every repo now lands on the single canonical
# ``"default"`` preset which seeds the canonical Plays bundle.
#
# We keep the legacy ids accepted at API boundaries (``LEGACY_PRESETS``)
# so existing rows in ``WorkspaceRepo.preset`` and any in-flight wizard
# tabs don't break; ``normalize_preset`` is the single funnel that maps
# both legacy ids and ``None`` to ``"default"``. Once Wave 8c rips the
# preset picker out of the FE *and* we're confident no DB rows still
# reference legacy strings, the ``LEGACY_PRESETS`` set can be dropped
# in a follow-up cleanup ticket.
# ---------------------------------------------------------------------------

KNOWN_PRESETS: Final[tuple[str, ...]] = ("default",)

# Legacy preset ids accepted at API boundaries; all normalize to
# ``"default"``. Do NOT remove without first confirming no
# ``WorkspaceRepo.preset`` row still references one of these strings AND
# no FE codepath sends them.
LEGACY_PRESETS: Final[frozenset[str]] = frozenset(
    {
        "web-app",
        "api-backend",
        "mobile-app",
        "mobile-app-deep",
        "ml-project",
        "platform",
        "regulated",
        "desktop-app",
        "firmware",
        "game",
        "cli",
        "monorepo",
        "marketing",
        "adoption-minimum",
    }
)


def normalize_preset(preset: str | None) -> str:
    """Canonicalize any preset string.

    ``None`` and any of the historical 14 preset ids in
    :data:`LEGACY_PRESETS` collapse to ``"default"``. Any other string
    passes through unchanged so a future custom preset can be added at
    the catalog layer without a code change here.

    Pure: no I/O, no module-level mutation, idempotent
    (``normalize_preset(normalize_preset(x)) == normalize_preset(x)``).
    """
    if preset is None or preset in LEGACY_PRESETS:
        return "default"
    return preset


# Compatibility map: preset id → ordered tuple of pattern ids that
# the preset enables. Post-P5-01 there is exactly one entry pointing
# at sibling D's canonical :data:`DEFAULT_BUNDLE` above. The wider
# Plays installer reads this map. Existing lane-seeding code paths use
# :func:`list_lane_recipes` / :func:`resolve_enabled_lane_ids`
# instead — see those for the pattern-catalog-derived enablement
# logic.
lane_recipes: Final[dict[str, tuple[str, ...]]] = {
    "default": DEFAULT_BUNDLE,
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LaneRecipe:
    """One baked-in lane as the seeder / dashboard wants to consume it.

    ``patterns`` is the ordered list of pattern ids the lane runs.
    Single-pattern lanes emit ``pattern: <id>`` in ``.ship/config.yml``;
    multi-pattern lanes emit ``patterns: [ids]`` and respect ``fanout``.
    Resolver-only lanes (``code_map``) keep ``patterns=[]`` and
    ``trigger=None`` so :func:`preset_bundle_files` skips them when
    composing ``.ship/config.yml``.
    """

    lane_id: str
    name: str
    workflow_id: str
    summary: str
    trigger: dict[str, str] | None
    patterns: tuple[str, ...] = ()
    fanout: str = "matrix"
    default_enabled: bool = True
    preset_enabled: frozenset[str] = field(default_factory=frozenset)


# Display / runtime metadata for the two lanes that don't derive from a
# single pattern. Keep in sync with ``preset_bundle_files`` — if a preset
# stops listing ``tech_debt`` here, the seeder drops the
# ``parallel-audit-lanes.yml`` starter on install.
_EXTRA_RECIPES: Final[tuple[LaneRecipe, ...]] = (
    LaneRecipe(
        lane_id="code_map",
        name="Code map refresh",
        workflow_id="code-map-refresh",
        summary=(
            "Refreshes the repo-wide code map so the agent can navigate "
            "without re-scanning every run. Resolver-only — no YAML "
            "lands in .ship/config.yml."
        ),
        trigger=None,
        patterns=(),
        fanout="matrix",
        default_enabled=True,
        # P5-01 collapse: only the canonical ``"default"`` preset
        # remains. Per-recipe gating is preserved as a hook for any
        # future custom presets a tenant adds via the catalog.
        preset_enabled=frozenset(KNOWN_PRESETS),
    ),
    LaneRecipe(
        lane_id="tech_debt",
        name="Tech-debt scan",
        workflow_id="parallel-audit-lanes",
        summary=(
            "Weekly parallel audit: security, perf, type coverage, "
            "dead code. Files findings as tracker tickets."
        ),
        trigger={"schedule": "0 6 * * 1"},
        # Multi-pattern recipe. Today ``patterns`` is empty — the scan-*
        # family lands with RFC-0008 C5 (expansion). Seeder still emits
        # the lane so the starter workflow + schedule ship; the catalog
        # just shows an empty pattern list until C5.
        patterns=(),
        fanout="matrix",
        default_enabled=True,
        # P5-01 collapse: legacy per-preset gating removed. The single
        # ``"default"`` preset enables tech_debt the same way it did
        # for every flagged legacy preset.
        preset_enabled=frozenset(KNOWN_PRESETS),
    ),
)


# ---------------------------------------------------------------------------
# Derivation from catalog
# ---------------------------------------------------------------------------


def _flatten_default_trigger(default_trigger: dict | None) -> dict[str, str] | None:
    """Turn the pattern ``spec.default_trigger`` shape into a lane trigger.

    ``default_trigger`` is expressed as ``{kind: event|schedule, …}`` in
    pattern frontmatter; the lane layer (``.ship/config.yml`` / Lane DB
    row) expects the flattened shape ``{event: …, pattern: …}`` or
    ``{schedule: …}``. Returns ``None`` for malformed shapes so the
    caller can skip the pattern cleanly instead of crashing the seeder.
    """
    if not isinstance(default_trigger, dict):
        return None
    kind = default_trigger.get("kind")
    if kind == "event":
        event = default_trigger.get("event")
        if not isinstance(event, str):
            return None
        out: dict[str, str] = {"event": event}
        for extra in ("pattern", "idempotency_key"):
            value = default_trigger.get(extra)
            if isinstance(value, str) and value:
                out[extra] = value
        return out
    if kind == "schedule":
        cron = default_trigger.get("cron")
        if not isinstance(cron, str) or not cron:
            return None
        return {"schedule": cron}
    return None


def _pattern_recipes() -> list[LaneRecipe]:
    """Walk the pattern catalog and fold patterns into lane recipes.

    Returns one recipe per ``lane_id`` a pattern declared. Multiple
    patterns with the same ``lane_id`` merge — the first contributor
    seeds trigger / workflow_id / display fields, subsequent
    contributors only add to ``patterns`` and union their
    ``enabled_on_install.presets`` into ``preset_enabled``.
    """
    # Local import to avoid a cycle: ``catalog`` imports from other
    # backend services that may import from here in the future.
    from backend.app.services import catalog as catalog_service

    grouped: dict[str, dict] = {}
    for entry in catalog_service.list_patterns():
        if "lane" not in entry.modes:
            continue
        lane_id = entry.lane_id
        if not lane_id:
            continue
        trigger = _flatten_default_trigger(entry.default_trigger)
        # A lane-mode pattern without a resolvable trigger is a config
        # error in the ARTIFACT.md — skip rather than crash the
        # seeder; ``ship_artifact_check`` catches these at CI time.
        if trigger is None:
            continue
        workflow_id = catalog_service.resolve_lane_workflow(entry)
        if not workflow_id:
            continue
        enabled_map = entry.enabled_on_install.get("presets") or {}
        presets_on = frozenset(
            preset
            for preset, flag in enabled_map.items()
            if isinstance(preset, str) and bool(flag)
        )
        default_enabled = bool(entry.enabled_on_install.get("default", False))
        fanout_value = entry.spec.get("fanout")
        fanout = fanout_value if fanout_value in ("matrix", "sequential", "concurrent") else "matrix"

        bucket = grouped.get(lane_id)
        if bucket is None:
            grouped[lane_id] = {
                "name": entry.lane_name or entry.name or lane_id,
                "summary": entry.lane_summary or entry.description or "",
                "workflow_id": workflow_id,
                "trigger": trigger,
                "patterns": [entry.id],
                "fanout": fanout,
                "default_enabled": default_enabled,
                "preset_enabled": set(presets_on),
            }
            continue
        # Subsequent patterns just join the pattern list + preset union.
        # Trigger / workflow conflicts are quietly ignored here — the
        # first contributor wins so the recipe stays deterministic;
        # ``ship_artifact_check`` should flag mismatched siblings.
        bucket["patterns"].append(entry.id)
        bucket["preset_enabled"].update(presets_on)
        bucket["default_enabled"] = bucket["default_enabled"] or default_enabled

    return [
        LaneRecipe(
            lane_id=lane_id,
            name=bucket["name"],
            workflow_id=bucket["workflow_id"],
            summary=bucket["summary"],
            trigger=dict(bucket["trigger"]) if bucket["trigger"] else None,
            patterns=tuple(bucket["patterns"]),
            fanout=bucket["fanout"],
            default_enabled=bucket["default_enabled"],
            preset_enabled=frozenset(bucket["preset_enabled"]),
        )
        for lane_id, bucket in grouped.items()
    ]


# Order mirrors the dashboard preference: PR review (demo-most) first,
# then schedule-based lanes, then self-heal last. We fix the order here
# rather than sort alphabetically so the Console + tests have a stable
# "first card on screen" contract.
_RECIPE_ORDER: Final[tuple[str, ...]] = (
    "pr_review",
    "daily_standup",
    "code_map",
    "tech_debt",
    "self_heal",
)


def list_lane_recipes() -> list[LaneRecipe]:
    """Return the full ordered list of built-in lane recipes.

    Catalog-derived patterns merge with :data:`_EXTRA_RECIPES` (the
    code_map resolver and the tech_debt placeholder) into a single
    ``lane_id → LaneRecipe`` map; the result is then sorted by
    :data:`_RECIPE_ORDER` with unknown lane_ids appended alphabetically
    so a freshly-authored pattern becomes visible without a code
    change.
    """
    by_id: dict[str, LaneRecipe] = {r.lane_id: r for r in _EXTRA_RECIPES}
    for recipe in _pattern_recipes():
        # Pattern-backed recipes win over ``_EXTRA_RECIPES`` with the
        # same id — that lets someone fold ``code_map`` /
        # ``tech_debt`` into the catalog later without a second edit
        # here (just add ``lane_id`` to the new pattern and drop the
        # special). Today no pattern owns those two ids.
        by_id[recipe.lane_id] = recipe

    known_order = {lane_id: idx for idx, lane_id in enumerate(_RECIPE_ORDER)}
    return sorted(
        by_id.values(),
        key=lambda r: (
            known_order.get(r.lane_id, len(known_order)),
            r.lane_id,
        ),
    )


def get_lane_recipe(lane_id: str) -> LaneRecipe | None:
    """Look up a single recipe by ``lane_id`` (used by the workflow/run lookup)."""
    for recipe in list_lane_recipes():
        if recipe.lane_id == lane_id:
            return recipe
    return None


def resolve_enabled_lane_ids(preset: str | None) -> frozenset[str]:
    """Return the set of lane ids a preset enables by default.

    Post-P5-01 the meaningful preset surface is a single ``"default"``
    (legacy ids and ``None`` collapse via :func:`normalize_preset`);
    that case returns every recipe whose ``default_enabled`` is
    truthy — same contract the pre-collapse "unknown preset" fallback
    exposed.

    Forward-compat: a future custom preset id (one not in the legacy
    set and not ``"default"``) still falls through to per-recipe
    ``preset_enabled`` gating, so a catalog-authored preset can opt
    into specific lanes without a code change here.
    """
    normalized = normalize_preset(preset)
    recipes = list_lane_recipes()
    if normalized == "default":
        return frozenset(r.lane_id for r in recipes if r.default_enabled)
    return frozenset(
        r.lane_id for r in recipes if normalized in r.preset_enabled
    )


# ---------------------------------------------------------------------------
# Pipeline seeding (unchanged external contract)
# ---------------------------------------------------------------------------


async def seed_default_pipelines(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    specs: Iterable[LaneRecipe] | None = None,
    default_repo_id: uuid.UUID | None = None,
    preset: str | None = None,
) -> list[Pipeline]:
    """Insert any missing Pipeline rows for ``workspace_id`` / ``preset``.

    Shape matches the pre-RFC ``seed_default_pipelines`` so its callers
    (``repos.activate_workspace_repo`` / pipeline-change preset swap)
    don't need to know about LaneRecipe. Adds a row per recipe not
    already present, respects ``preset`` for the enabled flag on new
    rows, and is **additive only**: once a row exists we never rewrite
    its ``enabled`` column, so operator toggles survive re-seeds.
    """
    recipes = list(specs) if specs is not None else list_lane_recipes()

    existing_rows = (
        await session.execute(
            select(Pipeline).where(Pipeline.workspace_id == workspace_id)
        )
    ).scalars().all()
    existing_lane_ids: set[str] = {row.lane_id for row in existing_rows}

    if default_repo_id is not None:
        for row in existing_rows:
            if row.repo_id is None:
                row.repo_id = default_repo_id

    enabled_ids = resolve_enabled_lane_ids(preset)
    new_rows: list[Pipeline] = []
    for recipe in recipes:
        if recipe.lane_id in existing_lane_ids:
            continue
        enabled = (
            (recipe.lane_id in enabled_ids)
            if preset is not None
            else recipe.default_enabled
        )
        row = Pipeline(
            workspace_id=workspace_id,
            lane_id=recipe.lane_id,
            name=recipe.name,
            workflow_id=recipe.workflow_id,
            enabled=enabled,
            config={},
            repo_id=default_repo_id,
        )
        session.add(row)
        new_rows.append(row)

    if new_rows or default_repo_id is not None:
        await session.flush()

    return [*existing_rows, *new_rows]


__all__ = [
    "DEFAULT_BUNDLE",
    "DEFAULT_BUNDLE_REASONS",
    "DEFAULT_SEED_LANES",
    "KNOWN_PRESETS",
    "LEGACY_PRESETS",
    "LEGACY_ROUTINE_IDS",
    "LaneRecipe",
    "ROUTINE_DISPLAY_LABELS",
    "default_seed_lanes",
    "get_lane_recipe",
    "lane_recipes",
    "list_lane_recipes",
    "normalize_preset",
    "resolve_enabled_lane_ids",
    "seed_default_pipelines",
]
