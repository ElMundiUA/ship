"""Seed-bundle helpers — what's left of the catalog after Phase 2.4 Step D.

Pre-Step-D this module owned a typed walker over ``artifacts/**/ARTIFACT.md``
plus dozens of helpers that derived lane recipes, knowledge articles, and
preset bundles from RFC-0008 frontmatter. Step D retired the catalog
itself; this module now keeps only the helpers the seed flow + the
Library editor still call:

* :class:`CatalogError` — raised by :func:`knowledge_starter_files` when
  a starter file is missing on disk. Kept under the ``catalog`` name so
  existing ``except catalog_service.CatalogError`` blocks keep working.
* :data:`KNOWLEDGE_STARTERS` / :func:`knowledge_starter_slugs` /
  :func:`knowledge_starter_files` — static seed list (``code-style`` /
  ``ui-runbook``) read from ``artifacts/knowledge-starters/<slug>.md``.
* :func:`emit_config_yaml` / :func:`bundle_routine_entries` /
  :func:`default_development_process_config` — render the
  ``.ship/config.yml`` body the wizard seed PR commits.
* :func:`bundle_lane_entries` — stub that returns an empty mapping;
  preserved for synthetic_lane_sync's import surface until it's
  rewritten or removed.

The catalog vocabulary (``pattern`` / ``tool`` / ``collection`` kinds,
``CatalogArtifact``, ``list_patterns`` and friends) is gone with
``artifacts/patterns/``. The only other on-disk artifact source still
loaded by Ship is ``artifacts/knowledge-starters/``; ``agent-rules``
collections are read by the CLI directly through the unauthenticated
``/collections`` endpoint that lives in :mod:`backend.app.main`.

The three "planning"s — disambiguation
======================================

There are three things called "planning" in this codebase. Don't conflate
them; if you find yourself confused by a log line, use this map:

1. ``priority_state.planning`` — the **dashboard bucket** value (DB enum
   in :mod:`backend.app.db.models.dashboard_priorities`). The UI label
   for this bucket is **"Drafts"** — what the operator sees on the
   dashboard. It means "the PO is shaping this; the agent's autonomous
   picker must NOT consume it."

2. ``state="planning"`` — a **kind-of-work tag** on per-stage entries in
   :func:`default_development_process_config` below (e.g. the
   ``task_intake`` stage carries ``state="planning"``). Classifies the
   stage's phase (planning / executing / reviewing). Internal-only;
   doesn't surface to the operator.

3. The **decomposition process** — a separate FSM (``id="decomposition"``,
   added in PR3 of the project-first delivery work) that runs BA →
   Architect → QA-Architect → QA + Developer on a project's anchor
   issue to produce coarse child tickets. Its operator-facing label is
   "Decomposition," NOT "planning," precisely to keep these three apart.
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Final

import yaml


__all__ = [
    "CatalogError",
    "ARTIFACTS_ROOT",
    "KNOWLEDGE_STARTERS",
    "knowledge_starter_slugs",
    "knowledge_starter_files",
    "emit_config_yaml",
    "bundle_routine_entries",
    "bundle_lane_entries",
    "default_development_process_config",
    "default_planning_process_config",
]


logger = logging.getLogger(__name__)


class CatalogError(RuntimeError):
    """Raised when a seedable knowledge starter is missing on disk.

    Kept under the legacy name so existing ``except CatalogError``
    handlers in the wizard / Library editor flows keep working.
    """


# ---------------------------------------------------------------------------
# Disk root resolution
# ---------------------------------------------------------------------------


def _discover_artifacts_root() -> Path:
    """Walk parents of this file until an ``artifacts/`` directory shows up.

    Same heuristic the pre-Step-D walker used; kept so the seed flow
    works in dev (``backend/app/services/catalog.py`` → repo root)
    and in prod (the file ships under ``/app`` next to a sibling
    ``artifacts/``).
    """
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        candidate = parent / "artifacts"
        if candidate.is_dir():
            return candidate
    fallback = Path.cwd() / "artifacts"
    logger.warning(
        "catalog: artifacts/ not found by parent walk; falling back to %s "
        "(check working directory if knowledge starters fail to load)",
        fallback,
    )
    return fallback


ARTIFACTS_ROOT: Final[Path] = _discover_artifacts_root()


# ---------------------------------------------------------------------------
# Knowledge starters — static seed under ``artifacts/knowledge-starters/``.
# ---------------------------------------------------------------------------

_STATIC_KNOWLEDGE_STARTERS: Final[tuple[str, ...]] = ("code-style", "ui-runbook")


def knowledge_starter_slugs() -> tuple[str, ...]:
    """Return the seedable knowledge starter slugs in deterministic order."""
    return _STATIC_KNOWLEDGE_STARTERS


KNOWLEDGE_STARTERS: Final[tuple[str, ...]] = knowledge_starter_slugs()


def knowledge_starter_files(
    selection: list[str] | tuple[str, ...] | None = None,
) -> list[tuple[str, str]]:
    """Return ``(path, content)`` tuples for the selected knowledge starters.

    ``selection`` filters :data:`KNOWLEDGE_STARTERS`; ``None`` means
    "seed everything". Unknown slugs raise :class:`CatalogError` so a
    stale UI can't silently drop a checkbox.
    """
    known = knowledge_starter_slugs()
    known_set = set(known)
    if selection is None:
        chosen: list[str] = list(known)
    else:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in selection:
            slug = raw.strip()
            if not slug or slug in seen:
                continue
            if slug not in known_set:
                raise CatalogError(
                    f"Unknown knowledge starter {slug!r}. "
                    f"Expected one of: {sorted(known)}"
                )
            cleaned.append(slug)
            seen.add(slug)
        chosen = cleaned

    root = ARTIFACTS_ROOT / "knowledge-starters"
    out: list[tuple[str, str]] = []
    for slug in chosen:
        source = root / f"{slug}.md"
        try:
            content = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise CatalogError(
                f"Knowledge starter {slug!r} is missing on disk ({source}): {exc}"
            ) from exc
        out.append((f".ship/knowledge/{slug}.md", content))
    return out


# ---------------------------------------------------------------------------
# .ship/config.yml emission — used by the wizard seed and the Library
# editor's ``POST /v1/.../repos/{id}/config/propose`` round-trip path.
# ---------------------------------------------------------------------------


def bundle_lane_entries(
    bundle: "Iterable[str]",
) -> "OrderedDict[str, dict[str, object]]":
    """Phase 2.4 Step D stub.

    Pre-Step-D this folded a flat ``bundle`` of pattern keys into a
    lanes mapping by walking the catalog. The catalog is gone; the
    sole live caller (:mod:`backend.app.services.synthetic_lane_sync`)
    is itself a wizard-only synthetic layer scheduled for retirement.
    Returning an empty mapping makes the synthetic syncer a no-op
    instead of crashing on import.
    """
    return OrderedDict()


def bundle_routine_entries(
    lanes: "Mapping[str, Mapping[str, object]]",
) -> dict[str, dict[str, object]]:
    """Project a recipe map into ``process.routines`` YAML entries.

    Phase-2.4: emit ``specialist: <slug>`` (the agent-role registry
    surface ``shipctl run`` resolves through
    ``GET /v1/.../agent-roles/{slug}/resolve``). Legacy recipes that
    still spell things as ``pattern: role-X`` keep working — the
    fallback writes the legacy key so older repos that haven't
    re-seeded yet still resolve through ``cli/lib/runtime/routines.mjs``
    ``pickSpecialistSlug``.
    """
    routines: dict[str, dict[str, object]] = {}
    for routine_id, lane in lanes.items():
        trigger: dict[str, object]
        kind = str(lane.get("kind") or "")
        if kind == "schedule":
            trigger = {
                "type": "schedule",
                "cron": str(lane.get("cron") or "0 8 * * *"),
                "window": "30m",
                "catchup": "latest",
            }
        elif kind == "event":
            trigger = {
                "type": "event",
                "event": str(lane.get("on") or "push"),
            }
        else:
            trigger = {"type": "manual"}
        routine: dict[str, object] = {
            "name": str(routine_id).replace("_", " ").title(),
            "enabled": True,
            "trigger": trigger,
        }
        if isinstance(lane.get("specialist"), str):
            routine["specialist"] = str(lane["specialist"])
        elif isinstance(lane.get("pattern"), str):
            routine["pattern"] = str(lane["pattern"])
        if isinstance(lane.get("patterns"), list):
            routine["patterns"] = list(lane["patterns"])
        if isinstance(lane.get("fanout"), str):
            routine["fanout"] = str(lane["fanout"])
        # ``fsm_stage`` overrides the role's default stage when set —
        # lets one role drive multiple processes (e.g. ``ba`` runs at
        # ``ba_requirements`` for SDLC and ``wbs`` for decomposition).
        if isinstance(lane.get("fsm_stage"), str):
            routine["fsm_stage"] = str(lane["fsm_stage"])
        if isinstance(lane.get("description"), str):
            routine["description"] = str(lane["description"])
        routines[str(routine_id)] = routine
    return routines


def default_development_process_config(
    *, routines: "Mapping[str, Mapping[str, object]] | None" = None
) -> dict[str, object]:
    """Return the canonical FSM process seeded into new repo configs.

    Phase 1 introduced nine pipeline specialists (intake, bug-triage,
    ba, tech-architect, qa-architect, developer, qa-engineer,
    qa-automation, reviewer) and Phase 3 added the gates knob; this
    config now matches that canon. ``specialist.id`` carries the
    kebab-case agent-role slug — the same slug the runtime resolver
    accepts at ``/v1/.../agent-roles/{slug}/resolve``.

    Bug-triage is a parallel intake stage for bugs (no transition in
    from ``task_intake``); it converges on ``ba_requirements`` so the
    rest of the pipeline doesn't fork. The console process editor
    surfaces both entry stages in the planning column.
    """
    return {
        "id": "development",
        "name": "Development Process",
        "primary": True,
        # Phase 3: where the operator wants to interject. ``after_pr``
        # is the autonomous default — the agent reviewer runs the final
        # review and the human only approves + merges. Operators who
        # want earlier interjection points edit this in
        # ``.ship/config.yml`` (or the process editor, when the UI
        # lands). Allowed: ``after_ba | after_arch | after_pr``.
        "gates": "after_pr",
        "states": [
            {
                "id": "task_intake",
                "name": "Intake",
                "state": "planning",
                "specialist": {"id": "intake", "name": "Intake"},
                "instructions": (
                    "Shape new feature requests into structured tickets "
                    "before BA picks them up."
                ),
            },
            {
                "id": "bug_triage",
                "name": "Bug triage",
                "state": "planning",
                "specialist": {"id": "bug-triage", "name": "Bug triage"},
                "instructions": (
                    "Structure bug reports into reproducible tickets "
                    "(steps, expected, actual, env, severity) before "
                    "BA writes the fix spec."
                ),
            },
            {
                "id": "ba_requirements",
                "name": "Requirements",
                "state": "planning",
                "specialist": {"id": "ba", "name": "BA / specification"},
                "instructions": (
                    "Turn the shaped ticket into acceptance criteria, "
                    "constraints, risks, and an explicit test plan."
                ),
            },
            {
                "id": "tech_arch_plan",
                "name": "Tech architecture",
                "state": "planning",
                "specialist": {"id": "tech-architect", "name": "Tech architect"},
                "instructions": (
                    "Plan the architecture of the change — components "
                    "touched, contracts, risk + rollback. Design only, "
                    "no code."
                ),
            },
            {
                "id": "qa_arch_plan",
                "name": "Test architecture",
                "state": "planning",
                "specialist": {"id": "qa-architect", "name": "QA architect"},
                "instructions": (
                    "Design the test coverage strategy — unit / "
                    "integration / e2e split, fixtures, edge cases. "
                    "Design only, no test code."
                ),
            },
            {
                "id": "dev_implementation",
                "name": "Implementation",
                "state": "executing",
                "specialist": {"id": "developer", "name": "Developer"},
                "instructions": (
                    "Implement the change against the architecture "
                    "plan, run the relevant gates, open the PR."
                ),
            },
            {
                "id": "qa_manual",
                "name": "Manual QA",
                "state": "executing",
                "specialist": {"id": "qa-engineer", "name": "QA engineer"},
                "instructions": (
                    "Walk the manual test plan against the open PR; "
                    "report defects without fixing them."
                ),
            },
            {
                "id": "qa_automation",
                "name": "Test automation",
                "state": "executing",
                "specialist": {"id": "qa-automation", "name": "QA automation"},
                "instructions": (
                    "Add automated tests anchored to the architect's "
                    "test plan so the regression sticks."
                ),
            },
            {
                "id": "code_review",
                "name": "Code review",
                "state": "reviewing",
                "specialist": {"id": "reviewer", "name": "Reviewer"},
                "instructions": (
                    "Final agent-side review — comments only, never "
                    "pushes commits, never approves. Flags blockers "
                    "before the human merger sees the PR."
                ),
            },
        ],
        "transitions": [
            {"from": "task_intake", "to": "ba_requirements"},
            {"from": "bug_triage", "to": "ba_requirements"},
            {"from": "ba_requirements", "to": "tech_arch_plan"},
            {"from": "tech_arch_plan", "to": "qa_arch_plan"},
            {"from": "qa_arch_plan", "to": "dev_implementation"},
            {"from": "dev_implementation", "to": "qa_manual"},
            {"from": "qa_manual", "to": "qa_automation"},
            {"from": "qa_automation", "to": "code_review"},
        ],
        "routines": dict(routines or {}),
    }


def default_planning_process_config() -> dict[str, object]:
    """Return the canonical decomposition FSM (project-first delivery).

    Runs against the *planning anchor* of each new project (one issue
    per Linear project, tagged ``planning:anchor``). Sequential chain:
    BA produces a WBS, Tech-architect lays out the architecture, QA-
    architect designs test coverage, then QA-engineer + Developer
    slice the WBS into coarse child tickets. Each specialist patches
    its own named section of the project body so re-running a stage
    replaces just that section instead of the whole blob.

    Coarse-tasks contract: child tickets emitted by the ``tasks``
    stage are deliberately rough — their bodies are 2-3 lines and the
    per-ticket SDLC at ``task_intake`` refines further. Decomposition's
    BA and SDLC's BA are different invocations of the same role
    template; the mode-switch lives in the role file's prompt and is
    keyed on the FSM context's ``process`` field.

    Specialist reuse note: this config does NOT introduce a new
    ``decomposer`` role. The product call (ELS-75) is "make a chain
    out of existing specialists, don't grow the role corpus." The
    role files (``ba.md``, ``tech-architect.md``, …) handle the mode
    distinction; ``catalog.py`` carries the orchestration shape.

    Naming hazard: this is the **decomposition process**, distinct
    from (1) the dashboard's ``priority_state.planning`` bucket
    (UI label ``Drafts``) and (2) the kind-of-work tag
    ``state="planning"`` on the development process's task_intake
    stage. See the module-level docstring for the full disambiguation.
    """
    return {
        "id": "decomposition",
        "name": "Decomposition",
        "primary": False,
        # No human gates today — the operator's gate is the manual
        # **Hand off to decomposition** click on the dashboard. Once
        # they hit it, the chain runs autonomously through to
        # ``planning_done`` and the project flips Drafts → Parked
        # (the PO promotes Parked → Active when ready to ship).
        "gates": "after_pr",
        "states": [
            {
                "id": "wbs",
                "name": "Work breakdown structure",
                "state": "planning",
                "specialist": {"id": "ba", "name": "BA / specification"},
                "instructions": (
                    "Read the project brief and emit a coarse WBS — a "
                    "list of child-ticket stubs (name + 2-3 line scope). "
                    "Patch ONLY the ``## WBS`` section of the project "
                    "body; do not touch the brief or the architecture "
                    "below it. Do NOT create child tickets yourself; "
                    "the ``tasks`` stage at the end of this pipeline "
                    "creates them."
                ),
            },
            {
                "id": "architecture",
                "name": "Architecture",
                "state": "planning",
                "specialist": {"id": "tech-architect", "name": "Tech architect"},
                "instructions": (
                    "Read the brief + WBS and write the system "
                    "architecture — components touched, contracts, "
                    "risk + rollback. Patch ONLY the ``## Architecture`` "
                    "section. Design only, no code."
                ),
            },
            {
                "id": "test_architecture",
                "name": "Test architecture",
                "state": "planning",
                "specialist": {"id": "qa-architect", "name": "QA architect"},
                "instructions": (
                    "Read the brief + WBS + architecture and design "
                    "the test coverage strategy — unit / integration / "
                    "e2e split, fixtures, edge cases. Patch ONLY the "
                    "``## Test architecture`` section. Design only, no "
                    "test code."
                ),
            },
            {
                "id": "tasks",
                "name": "Task slicing",
                "state": "executing",
                "specialist": {"id": "developer", "name": "Developer"},
                "instructions": (
                    "Read the WBS + architecture + test architecture "
                    "and create child tickets — one per WBS line, "
                    "coarse (3-5 line bodies). Each child enters the "
                    "per-ticket SDLC at ``task_intake`` and refines "
                    "further; do NOT write detailed acceptance criteria "
                    "or test plans here, the SDLC's BA does that. "
                    "Patch the ``## Tasks`` section with a list of the "
                    "ticket identifiers + names you created."
                ),
            },
            {
                "id": "planning_done",
                "name": "Decomposition done",
                "state": "reviewing",
                # Terminal stage — no specialist runs here. ``ready_next_step``
                # with ``stage_next='planning_done'`` from the ``tasks`` stage
                # signals decomposition complete; the finish hook flips the
                # dashboard row from Drafts → Parked (the PO promotes Parked → Active manually; ELS-81).
                "specialist": {"id": "developer", "name": "Developer"},
                "instructions": (
                    "Terminal — no work. Reaching this stage flips the "
                    "project from Drafts to Parked on the dashboard "
                    "(the PO then promotes Parked → Active when ready)."
                ),
            },
        ],
        "transitions": [
            {"from": "wbs", "to": "architecture"},
            {"from": "architecture", "to": "test_architecture"},
            {"from": "test_architecture", "to": "tasks"},
            {"from": "tasks", "to": "planning_done"},
        ],
        # Cron-driven, symmetric to the development process. Each
        # non-terminal stage gets a routine that the customer-side
        # GitHub Actions cron polls via ``shipctl run --routine X``;
        # the routine carries an explicit ``fsm_stage`` so one role
        # (``ba``) can serve both decomposition (``stage:wbs``) and
        # SDLC (``ba_requirements``) without per-process role clones.
        # ``planning_done`` is terminal — no routine — and the finish
        # hook on the ``tasks`` stage flips the dashboard row Drafts →
        # Parked.
        "routines": {
            "wbs": {
                "name": "Decomposition WBS",
                "enabled": True,
                "specialist": "ba",
                "fsm_stage": "wbs",
                "trigger": {
                    "type": "schedule",
                    "cron": "*/30 * * * *",
                    "window": "30m",
                    "catchup": "latest",
                },
                "description": (
                    "Customer cron polls planning anchors in stage:wbs "
                    "and dispatches BA to produce the WBS section."
                ),
            },
            "architecture": {
                "name": "Decomposition Architecture",
                "enabled": True,
                "specialist": "tech-architect",
                "fsm_stage": "architecture",
                "trigger": {
                    "type": "schedule",
                    "cron": "*/30 * * * *",
                    "window": "30m",
                    "catchup": "latest",
                },
                "description": (
                    "Customer cron polls anchors in stage:architecture "
                    "and dispatches Tech-architect."
                ),
            },
            "test_architecture": {
                "name": "Decomposition Test architecture",
                "enabled": True,
                "specialist": "qa-architect",
                "fsm_stage": "test_architecture",
                "trigger": {
                    "type": "schedule",
                    "cron": "*/30 * * * *",
                    "window": "30m",
                    "catchup": "latest",
                },
                "description": (
                    "Customer cron polls anchors in "
                    "stage:test_architecture and dispatches QA-architect."
                ),
            },
            "tasks": {
                "name": "Decomposition Task slicing",
                "enabled": True,
                "specialist": "developer",
                "fsm_stage": "tasks",
                "trigger": {
                    "type": "schedule",
                    "cron": "*/30 * * * *",
                    "window": "30m",
                    "catchup": "latest",
                },
                "description": (
                    "Customer cron polls anchors in stage:tasks and "
                    "dispatches Developer to slice the WBS into child "
                    "tickets. On finish the project flips Drafts to Parked."
                ),
            },
        },
    }


def emit_config_yaml(
    *,
    preset_id: str | None,
    repo_full_name: str | None,
    lanes: "Mapping[str, Mapping[str, object]]",
    process: "Mapping[str, object] | None" = None,
) -> str:
    """Serialize a ``.ship/config.yml`` body in schema v2.

    Used by the wizard seed flow (composes the YAML emitted by
    ``compose_seed_files``) and by the Library editor's
    ``POST /v1/.../repos/{id}/config/propose`` round-trip path. Same
    preamble / quoting / key ordering for both so the console's diff
    view stays trustworthy when an operator edits a seeded config.
    """
    lines: list[str] = [
        "# Generated by Ship — install bundle",
        "# Learn more: https://app.ship.elmundi.com",
    ]
    if preset_id:
        lines.append(f"preset: {preset_id}")
    lines.append("version: 2")
    lines.append("shipctl_min: 0.12.0")
    if repo_full_name:
        lines.append(f"repo: {repo_full_name}")
    lines.extend(
        [
            "api:",
            '  base_url: "https://ship.elmundi.com"',
            "  channel: stable",
            "  ttl_hours: 24",
            "  offline_ok: true",
            "stack:",
            "  tracker: none",
            "  ci: gh-actions",
            "  agents: []",
            "  language: multi",
            "agent:",
            "  default: {}",
            "  overrides: {}",
        ]
    )

    if process:
        lines.append("process:")
        dumped = yaml.safe_dump(
            dict(process),
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=False,
        ).rstrip()
        for line in dumped.splitlines():
            lines.append(f"  {line}" if line else "")

    if lanes:
        lines.append("lanes:")
        for lane_id, trigger in lanes.items():
            lines.append(f"  {lane_id}:")
            for key, value in _normalise_lane_for_config(trigger).items():
                if isinstance(value, (list, tuple)):
                    inner = ", ".join(_render_yaml_scalar(v) for v in value)
                    lines.append(f"    {key}: [{inner}]")
                else:
                    lines.append(f"    {key}: {_render_yaml_scalar(value)}")

    return "\n".join(lines) + "\n"


def _normalise_lane_for_config(trigger: "Mapping[str, object]") -> dict[str, object]:
    """Render legacy flat lane triggers as current CLI schema v2 lanes."""
    out: dict[str, object] = {}
    if isinstance(trigger.get("kind"), str):
        kind = str(trigger["kind"])
    elif "once" in trigger:
        kind = "once"
    elif "event" in trigger:
        kind = "event"
    elif "schedule" in trigger:
        kind = "schedule"
    else:
        kind = ""

    if kind:
        out["kind"] = kind
    if kind == "event":
        event = trigger.get("on", trigger.get("event"))
        if event is not None:
            out["on"] = event
    elif kind == "schedule":
        cron = trigger.get("cron", trigger.get("schedule"))
        if cron is not None:
            out["cron"] = cron
    elif kind == "once":
        once = trigger.get("once")
        if once is not None:
            out["once"] = once

    for key in (
        "pattern",
        "patterns",
        "pattern_version",
        "fanout",
        "permissions",
        "runner",
        "timeout_minutes",
        "concurrency",
    ):
        if key in trigger:
            out[key] = trigger[key]

    if kind == "once":
        idem = trigger.get("idempotency")
        idem_key = trigger.get("idempotency_key")
        if isinstance(idem, dict):
            out["idempotency"] = idem
        elif isinstance(idem_key, str) and idem_key:
            out["idempotency"] = {
                "key": idem_key,
                "store": "file",
                "reset_on": "version-change",
            }
    return out


def _render_yaml_scalar(value: object) -> str:
    """Best-effort scalar renderer matching the legacy inline emitter.

    Quotes strings that contain whitespace or YAML-significant chars
    (``*``, ``{``, ``}``, ``/``, ``+``) so schedule crons and
    idempotency key templates don't get mis-parsed. Keeps ints/bools
    bare so the output matches what PyYAML round-trips.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if any(ch in text for ch in (" ", "*", "{", "}", "/", "+")):
        return f'"{text}"'
    return text
