"""Constants the seed bundle + wizard preview share.

Pre-Phase 2.4 this module derived its lane recipes from the
``artifacts/patterns/`` catalog (RFC-0008 §C3.3). Step D of Phase 2.4
retired the catalog and inlined the few constants that are still
load-bearing:

* :data:`DEFAULT_BUNDLE` / :data:`DEFAULT_BUNDLE_REASONS` — the Plays
  preview the wizard's "Confirm bootstrap" step renders.
* :data:`DEFAULT_SEED_LANES` / :func:`default_seed_lanes` — the
  canonical seven routines the seed bundle writes into a fresh
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
    # Routines (cron-driven sweeps; seven canonical lanes below).
    "daily-retro",
    "learning-capture",
    "workflow-self-heal",
    "tech-reviewer",
    "qa-reviewer",
    "security-officer",
    "process-reviewer",
    # Pipeline specialists (one ticket at a time, picked by capacity /
    # queue depth). Order mirrors the SDLC flow:
    # intake → BA → tech-arch → qa-arch → dev → QA → autoQA → reviewer.
    "intake",
    "bug-triage",
    "ba",
    "tech-architect",
    "qa-architect",
    "developer",
    "qa-engineer",
    "qa-automation",
    "reviewer",
)

DEFAULT_BUNDLE_REASONS: dict[str, str] = {
    "daily-retro": "Posts a morning summary of what shipped in the last 24 hours.",
    "learning-capture": "Closes the day with a retro that turns recent runs into improvement notes.",
    "workflow-self-heal": "Watches Ship workflows; opens a fix ticket or pings a human when something breaks.",
    "tech-reviewer": "Sweeps the repo daily for tech-debt and architectural risk; files dedup tickets.",
    "qa-reviewer": "Sweeps the repo daily for test-coverage gaps; files dedup tickets.",
    "security-officer": "Runs the daily security review and routes actionable findings.",
    "process-reviewer": "Suggests SDLC improvements (PR previews, CI health, branch hygiene) to the inbox.",
    "intake": "Shapes new work into a structured ticket before BA picks it up.",
    "bug-triage": "Structures bug reports into reproducible tickets before BA writes the fix spec.",
    "ba": "Writes the implementation-grade specification on top of intake.",
    "tech-architect": "Plans the architecture for one ticket; design only, no code.",
    "qa-architect": "Plans the test coverage for one ticket; design only, no test code.",
    "developer": "Implements tickets that cleared requirements and architecture.",
    "qa-engineer": "Walks the manual test plan against a feature-complete PR.",
    "qa-automation": "Adds automated tests for the change so the regression sticks.",
    "reviewer": "Final agent-side code review; comments only, never pushes commits.",
}


# ---------------------------------------------------------------------------
# Canonical seven routines — the only set the editor surfaces and the only
# set the seed bundle writes into a fresh ``.ship/config.yml``.
# ---------------------------------------------------------------------------

DEFAULT_SEED_LANES: Final[dict[str, dict[str, object]]] = {
    # ``specialist`` is the Phase-2.4 vocabulary: shipctl run resolves
    # the slug through ``GET /v1/.../agent-roles/{slug}/resolve`` (workspace
    # override → Ship default file). Slugs match
    # ``backend/app/resources/agent_roles/<slug>.md`` exactly.
    #
    # ----- Supporting / context-free routines -------------------
    # These don't carry an ``fsm_stage`` — they run free-form (no
    # ticket pickup), produce digests / sweeps / inbox items.
    "daily": {
        "kind": "schedule",
        "cron": "0 9 * * *",   # 09:00 UTC — morning summary
        "specialist": "daily-retro",
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
    "tech_review": {
        "kind": "schedule",
        "cron": "0 12 * * *",  # 12:00 UTC
        "specialist": "tech-reviewer",
    },
    "qa_review": {
        "kind": "schedule",
        "cron": "0 15 * * *",  # 15:00 UTC
        "specialist": "qa-reviewer",
    },
    "security_review": {
        "kind": "schedule",
        "cron": "0 6 * * *",   # 06:00 UTC
        "specialist": "security-officer",
    },
    "process_review": {
        "kind": "schedule",
        "cron": "0 16 * * *",  # 16:00 UTC — after the QA sweep, before retro
        "specialist": "process-reviewer",
    },
    # ----- SDLC routines (the missing piece) --------------------
    # One routine per ticket-driven SDLC stage. Each carries an
    # explicit ``fsm_stage`` so ``shipctl run --routine X`` polls
    # ``GET /tracker/next?state=<stage>`` and feeds the matching
    # specialist a real ticket. Without these, agents had no
    # autonomous way to pick up SDLC work — the cron's pipeline-
    # pick fallback would dispatch a context-free specialist that
    # finished in 3 minutes without doing anything useful.
    #
    # 30-min cadence is the safe free-tier default (matches the
    # trigger workflow's own */30 cron). The trigger workflow
    # picks at most ONE due routine per tick, so adding 9 stages
    # doesn't multiply the budget — picker scans them in order
    # and dispatches the first eligible one.
    "intake": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "intake",
        "fsm_stage": "task_intake",
    },
    "bug_triage": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "bug-triage",
        "fsm_stage": "bug_triage",
    },
    "ba_requirements": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "ba",
        "fsm_stage": "ba_requirements",
    },
    "tech_arch_plan": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "tech-architect",
        "fsm_stage": "tech_arch_plan",
    },
    "qa_arch_plan": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "qa-architect",
        "fsm_stage": "qa_arch_plan",
    },
    "dev_implementation": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "developer",
        "fsm_stage": "dev_implementation",
    },
    "qa_manual": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "qa-engineer",
        "fsm_stage": "qa_manual",
    },
    "qa_automation": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "qa-automation",
        "fsm_stage": "qa_automation",
    },
    "code_review": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "reviewer",
        "fsm_stage": "code_review",
    },
}

# Display labels keyed by the canonical routine id.
ROUTINE_DISPLAY_LABELS: Final[dict[str, str]] = {
    # Supporting routines.
    "daily": "Daily",
    "retro": "Retro",
    "healthcheck": "Self-heal",
    "tech_review": "Tech review",
    "qa_review": "QA review",
    "security_review": "Security review",
    "process_review": "Process review",
    # SDLC routines (per-stage ticket pickup).
    "intake": "Intake",
    "bug_triage": "Bug triage",
    "ba_requirements": "Requirements",
    "tech_arch_plan": "Tech architecture",
    "qa_arch_plan": "Test architecture",
    "dev_implementation": "Implementation",
    "qa_manual": "Manual QA",
    "qa_automation": "Test automation",
    "code_review": "Code review",
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
