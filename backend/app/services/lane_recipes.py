"""Constants the seed bundle + wizard preview share.

Pre-Phase 2.4 this module derived its lane recipes from the
``artifacts/patterns/`` catalog (RFC-0008 §C3.3). Step D of Phase 2.4
retired the catalog and inlined the few constants that are still
load-bearing:

* :data:`DEFAULT_BUNDLE` / :data:`DEFAULT_BUNDLE_REASONS` — the Plays
  preview the wizard's "Confirm bootstrap" step renders.
* :data:`DEFAULT_SEED_LANES` / :func:`default_seed_lanes` — the
  canonical six routines the seed bundle writes into a fresh
  ``.ship/config.yml``. Each entry now spells the agent role with
  ``specialist:`` (Phase 2.4 vocabulary), resolved by
  ``shipctl run`` through ``GET /v1/.../agent-roles/{slug}/resolve``.
* :data:`KNOWN_PRESETS` / :data:`LEGACY_PRESETS` /
  :func:`normalize_preset` — preset-collapse helper kept for
  back-compat with rows in ``WorkspaceRepo.preset`` and old API
  payloads.
* :data:`LEGACY_ROUTINE_IDS` / :data:`ROUTINE_DISPLAY_LABELS` —
  drift detection + UI labels for the canonical six.

Everything else (the ``LaneRecipe`` dataclass, ``_pattern_recipes``,
``list_lane_recipes``, ``_EXTRA_RECIPES``, ``resolve_enabled_lane_ids``,
``seed_default_pipelines``, ``_flatten_default_trigger``) walked the
pattern catalog and is gone with it. ``pipelines.py`` now carries an
inline ``_LANE_WORKFLOW_MAP`` for the only consumer that still needs
the lane → workflow_id projection.
"""

from __future__ import annotations

from typing import Final


# ---------------------------------------------------------------------------
# Default bundle — the canonical Plays installed in every new repo's
# ``.ship/config.yml``. Wave-8 wizard preview reads this tuple via
# ``GET /v1/catalog/default-bundle``; ``BUNDLE_REASONS`` supplies the
# one-line blurb each entry shows.
# ---------------------------------------------------------------------------

DEFAULT_BUNDLE: tuple[str, ...] = (
    "qa-architect",
    "tech-architect",
    "security-officer",
    "daily-retro",
    "learning-capture",
    "intake",
    "ba",
    "developer",
    "qa-acceptance",
    "workflow-self-heal",
)

DEFAULT_BUNDLE_REASONS: dict[str, str] = {
    "qa-architect": "Reviews test architecture daily and plans QA coverage for active work.",
    "tech-architect": "Reviews architecture daily and plans implementation for ready work.",
    "security-officer": "Runs daily security review and routes actionable findings.",
    "daily-retro": "Reports the last 24 hours of repository and delivery activity to Ship.",
    "learning-capture": "Reviews recent runs and suggests workflow or prompt improvements.",
    "intake": "Checks new work for enough information before BA / architecture planning.",
    "ba": "Writes requirements for tasks that are ready for BA.",
    "developer": "Implements tasks that passed requirements and architecture planning.",
    "qa-acceptance": "Covers manual QA and acceptance gaps before automation picks them up.",
    "workflow-self-heal": "Checks hourly whether Ship workflows are healthy and reports fixes.",
}


# ---------------------------------------------------------------------------
# Canonical six routines — the only set the editor surfaces and the only
# set the seed bundle writes into a fresh ``.ship/config.yml``.
# ---------------------------------------------------------------------------

DEFAULT_SEED_LANES: Final[dict[str, dict[str, object]]] = {
    # ``specialist`` is the Phase-2.4 vocabulary: shipctl run resolves
    # the slug through ``GET /v1/.../agent-roles/{slug}/resolve`` (workspace
    # override → Ship default file). Slugs match
    # ``backend/app/resources/agent_roles/<slug>.md`` exactly.
    "security_review": {
        "kind": "schedule",
        "cron": "0 6 * * *",   # 06:00 UTC
        "specialist": "security-officer",
    },
    "daily": {
        "kind": "schedule",
        "cron": "0 9 * * *",   # 09:00 UTC — morning digest
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

# Display labels keyed by the canonical routine id.
ROUTINE_DISPLAY_LABELS: Final[dict[str, str]] = {
    "daily": "Daily",
    "retro": "Retro",
    "healthcheck": "Healthcheck",
    "tech_review": "Tech review",
    "qa_review": "QA review",
    "security_review": "Security review",
}

# Legacy ids ever produced by older seed versions. Loading code logs a
# drift warning when it sees one of these.
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
# Preset normalization (P5-01 collapse).
# ---------------------------------------------------------------------------

KNOWN_PRESETS: Final[tuple[str, ...]] = ("default",)

# Legacy preset ids accepted at API boundaries; all normalize to
# ``"default"``.
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
    passes through unchanged.

    Pure: no I/O, no module-level mutation, idempotent
    (``normalize_preset(normalize_preset(x)) == normalize_preset(x)``).
    """
    if preset is None or preset in LEGACY_PRESETS:
        return "default"
    return preset


__all__ = [
    "DEFAULT_BUNDLE",
    "DEFAULT_BUNDLE_REASONS",
    "DEFAULT_SEED_LANES",
    "KNOWN_PRESETS",
    "LEGACY_PRESETS",
    "LEGACY_ROUTINE_IDS",
    "ROUTINE_DISPLAY_LABELS",
    "default_seed_lanes",
    "normalize_preset",
]
