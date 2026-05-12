"""Default-bundle constants the seed flow + process preview share.

Pre-Phase 2.4 this module derived its lane recipes from the
``artifacts/patterns/`` catalog (RFC-0008 §C3.3). Step D of Phase 2.4
retired the catalog and inlined the two surviving constants:

* :data:`DEFAULT_BUNDLE` / :data:`DEFAULT_BUNDLE_REASONS` — the
  canonical agent-role list the operator sees in the wizard's
  "Confirm bootstrap" step.
* :data:`DEFAULT_SEED_LANES` / :func:`default_seed_lanes` — the
  routines the seed flow writes into a fresh ``.ship/config.yml``.
  Each entry spells the agent role with ``specialist:`` (Phase 2.4
  vocabulary), resolved by ``shipctl run`` through
  ``GET /v1/.../agent-roles/{slug}/resolve``.

Everything else from the pre-2.4 era — the ``LaneRecipe`` dataclass,
``_pattern_recipes``, ``list_lane_recipes``, ``_EXTRA_RECIPES``,
``resolve_enabled_lane_ids``, ``seed_default_pipelines``,
``_flatten_default_trigger``, ``LEGACY_ROUTINE_IDS``,
``ROUTINE_DISPLAY_LABELS`` — walked with the pattern catalog. The
preset-collapse helpers (``KNOWN_PRESETS``, ``LEGACY_PRESETS``,
``normalize_preset``) followed in the next sweep: presets are
``"default"`` for every workspace, nothing else exists, no legacy
payloads to migrate.
"""

from __future__ import annotations

from typing import Final


# ---------------------------------------------------------------------------
# Default bundle — the canonical agent roles installed in every new
# repo's ``.ship/config.yml``. Wave-8 wizard preview reads this tuple via
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
    # Intake handles both feature requests and bug reports; the
    # ``bug-triage`` specialist was retired after the parallel-entry
    # design produced infinite loops on feature tickets (the bug
    # agent would correctly refuse to fabricate bug-report fields
    # and the routine kept re-picking the same ticket every tick).
    "intake",
    "ba",
    "tech-architect",
    "qa-architect",
    "developer",
    "qa-engineer",
    "qa-automation",
    "reviewer",
)

DEFAULT_BUNDLE_REASONS: dict[str, str] = {
    "daily-retro": "Files a morning digest letter in the operator's inbox — read it, hit Acknowledge.",
    "learning-capture": "End-of-day digest of patterns worth remembering, filed as an inbox letter.",
    "workflow-self-heal": "Watches Ship workflows; opens a fix ticket or pings a human when something breaks.",
    "tech-reviewer": "Sweeps the repo daily for tech-debt findings; files dedup tickets in the Tech Debt project.",
    "qa-reviewer": "Sweeps the repo daily for test-coverage gaps; files dedup tickets in the QA Debt project.",
    "security-officer": "Runs the daily security review; files dedup tickets in the Security project.",
    "process-reviewer": "SDLC improvement recommendations land as inbox letters — operator decisions, not work items.",
    "intake": "Shapes new work into a structured ticket before BA picks it up. Handles both feature requests and bug reports.",
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
    # picks at most ONE due routine per tick.
    #
    # ``pipeline_priority`` drives drain-first scheduling: when
    # multiple SDLC routines are cron-due in the same tick (the
    # common case, since they all share ``*/30 * * * *``), the
    # picker sorts by priority DESC and runs the LATER pipeline
    # stage first. Reasoning: minimize WIP, prefer flushing tickets
    # toward Done over feeding new ones into the head of the queue.
    # A code-review-eligible ticket waits less than an intake ticket
    # because it's closer to delivering value. Supporting routines
    # above carry no ``pipeline_priority`` and fall back to ``0``;
    # daily / retro / sweeps cron once per day, so they almost never
    # contend with the SDLC chain anyway.
    "intake": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "intake",
        "fsm_stage": "task_intake",
        "pipeline_priority": 10,
    },
    # ``ba_requirements`` routine retired — intake now produces the full
    # impl-grade spec, see ``agent_roles/intake.md``. Legacy in-flight
    # tickets that already carry ``stage:ba_requirements`` breadcrumb
    # advance through ``tech_arch_plan`` (which requires the
    # ``stage:task_intake`` breadcrumb they also have).
    "tech_arch_plan": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "tech-architect",
        "fsm_stage": "tech_arch_plan",
        "pipeline_priority": 20,
    },
    "qa_arch_plan": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "qa-architect",
        "fsm_stage": "qa_arch_plan",
        "pipeline_priority": 30,
    },
    "dev_implementation": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "developer",
        "fsm_stage": "dev_implementation",
        "pipeline_priority": 40,
    },
    "qa_manual": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "qa-engineer",
        "fsm_stage": "qa_manual",
        "pipeline_priority": 50,
    },
    "qa_automation": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "qa-automation",
        "fsm_stage": "qa_automation",
        "pipeline_priority": 60,
    },
    "code_review": {
        "kind": "schedule",
        "cron": "*/30 * * * *",
        "specialist": "reviewer",
        "fsm_stage": "code_review",
        "pipeline_priority": 70,
    },
}

def default_seed_lanes() -> dict[str, dict[str, object]]:
    return {lane_id: dict(body) for lane_id, body in DEFAULT_SEED_LANES.items()}


__all__ = [
    "DEFAULT_BUNDLE",
    "DEFAULT_BUNDLE_REASONS",
    "DEFAULT_SEED_LANES",
    "default_seed_lanes",
]
