"""Tests for the unified wizard seed bundle composer (P5-05).

Eight bundle-shape tests rebased onto the new
:func:`backend.app.services.seed_bundle.compose_seed_files` contract
plus a couple of preserved ``render_tracker_fsm`` smoke tests that
were the only thing exercising the FSM module's no-tracker /
workspace-override branches.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest


def _seeded_at() -> datetime:
    """Pinned timestamp so the v2 marker hashes deterministically.

    Tests that re-call ``compose_seed_files`` need the same input
    ``seeded_at`` to assert byte-equality of the bundle output —
    ``datetime.now`` would tick between calls.
    """
    return datetime(2026, 4, 24, 12, 0, 0, tzinfo=timezone.utc)


def test_compose_default_bundle_emits_config_yml() -> None:
    """Default bundle composes ``.ship/config.yml`` rendered against
    the *whole* bundle (one ``process.routines`` block, no per-preset shards)."""

    from backend.app.services.lane_recipes import DEFAULT_BUNDLE
    from backend.app.services.seed_bundle import (
        CONFIG_PATH,
        compose_seed_files,
    )

    bundle = compose_seed_files(
        bundle=DEFAULT_BUNDLE,
        knowledge_slugs=[],
        tracker_kind=None,
        repo_full_name="acme/widgets",
        seeded_at=_seeded_at(),
    )

    paths = [p for p, _ in bundle.files]
    assert CONFIG_PATH in paths

    config_body = next(c for p, c in bundle.files if p == CONFIG_PATH)
    # Repo-scoping line — one place to grep when debugging the
    # wizard's "wrong repo on the YAML" reports.
    assert "acme/widgets" in config_body
    # ``routines:`` is the v2 runtime block — the mere presence of a
    # ``preset:`` header doesn't prove the new shape, but the
    # ``process.routines`` map only renders when the bundle renderer
    # is in play.
    assert "routines:" in config_body
    assert "lanes:" not in config_body
    assert "shipctl_min: 0.12.0" in config_body
    assert "api:\n" in config_body
    assert "stack:\n" in config_body
    assert "agent:\n" in config_body
    assert "process:\n" in config_body
    assert "name: Development Process" in config_body
    assert "type: schedule" in config_body
    assert "cron:" in config_body
    # The canonical seven routine ids land in process.routines (matches
    # backend.app.services.lane_recipes.DEFAULT_SEED_LANES).
    assert "daily:" in config_body
    assert "retro:" in config_body
    assert "healthcheck:" in config_body
    assert "tech_review:" in config_body
    assert "qa_review:" in config_body
    assert "security_review:" in config_body
    assert "process_review:" in config_body
    # Every routine specialist + every pipeline specialist surfaces as
    # a ``DEFAULT_BUNDLE`` entry — sanity-check the round-trip.
    assert "intake" in bundle.bundle
    assert "developer" in bundle.bundle
    assert "reviewer" in bundle.bundle
    # Phase 3: ``process.gates`` ships with the seed at the safe
    # default — autonomous through the agent reviewer, human only at
    # PR merge. Operators who want earlier interjection edit it.
    assert "gates: after_pr" in config_body
    # Phase 1.5: nine canonical pipeline stages, kebab-case specialist
    # slugs matching the agent-role files. Pinned so a regression in
    # ``default_development_process_config`` doesn't silently drop a
    # stage from new wizard seeds.
    for state_id in (
        "task_intake",
        "bug_triage",
        "ba_requirements",
        "tech_arch_plan",
        "qa_arch_plan",
        "dev_implementation",
        "qa_manual",
        "qa_automation",
        "code_review",
    ):
        assert f"id: {state_id}" in config_body, (
            f"missing process.states[].id={state_id} in seeded YAML"
        )
    for slug in (
        "id: ba\n",
        "id: tech-architect\n",
        "id: qa-architect\n",
        "id: developer\n",
        "id: qa-engineer\n",
        "id: qa-automation\n",
        "id: reviewer\n",
        "id: bug-triage\n",
    ):
        assert slug in config_body, (
            f"missing specialist {slug.strip()} in seeded YAML"
        )


def test_compose_default_bundle_emits_decomposition_routines() -> None:
    """Decomposition routines (ELS-79) land in ``process.routines``
    so customer-side ``shipctl run --routine wbs|architecture|...``
    drives the decomposition chain through customer cron.

    Each decomposition routine carries an explicit ``fsm_stage`` that
    overrides the role's default — that's how one role (``ba``) serves
    both ``ba_requirements`` for SDLC and ``wbs`` for decomposition
    without needing per-process role clones.
    """

    from backend.app.services.lane_recipes import DEFAULT_BUNDLE
    from backend.app.services.seed_bundle import (
        CONFIG_PATH,
        compose_seed_files,
    )

    bundle = compose_seed_files(
        bundle=DEFAULT_BUNDLE,
        knowledge_slugs=[],
        tracker_kind=None,
        repo_full_name="acme/widgets",
        seeded_at=_seeded_at(),
    )

    config_body = next(c for p, c in bundle.files if p == CONFIG_PATH)

    # The four non-terminal decomposition stages each get a routine.
    # ``planning_done`` is terminal — no routine — and the finish hook
    # on the ``tasks`` stage flips Drafts → Parked.
    for routine_id in ("wbs", "architecture", "test_architecture", "tasks"):
        assert f"    {routine_id}:" in config_body, (
            f"missing decomposition routine {routine_id} in seeded YAML"
        )

    # Each decomposition routine carries an ``fsm_stage`` so the CLI
    # can override the role's default stage — this is the key wire that
    # makes ``shipctl run --routine wbs`` poll ``stage:wbs`` instead of
    # the BA role's SDLC default ``ba_requirements``.
    for fsm_stage in ("wbs", "architecture", "test_architecture", "tasks"):
        assert f"fsm_stage: {fsm_stage}" in config_body, (
            f"missing fsm_stage: {fsm_stage} on decomposition routine"
        )

    # Specialists are reused from the SDLC role corpus.
    assert "specialist: ba" in config_body
    assert "specialist: tech-architect" in config_body
    assert "specialist: qa-architect" in config_body
    assert "specialist: developer" in config_body


def test_compose_default_bundle_emits_dedup_workflows() -> None:
    """Multiple patterns sharing a starter workflow collapse to one
    YAML on disk (path uniqueness is the tree builder's invariant)."""

    from backend.app.services.lane_recipes import DEFAULT_BUNDLE
    from backend.app.services.seed_bundle import compose_seed_files

    bundle = compose_seed_files(
        bundle=DEFAULT_BUNDLE,
        knowledge_slugs=[],
        tracker_kind=None,
        repo_full_name="acme/widgets",
        seeded_at=_seeded_at(),
    )

    workflow_paths = [
        p for p, _ in bundle.files if p.startswith(".github/workflows/")
    ]
    # Path uniqueness — ``commit_bundle_pr`` would explode on a
    # duplicate path even if the contents matched.
    assert len(workflow_paths) == len(set(workflow_paths))
    # At least one workflow must be installed; the precise list
    # depends on the catalog pattern → starter mapping but the
    # default bundle is non-trivial.
    assert workflow_paths
    assert ".github/workflows/ship-trigger-schedule.yml" in workflow_paths

    # Phase 2 scheduler shape — the trigger workflow runs at 30-min
    # cadence (free-tier math) and dispatches a single ``next_action``
    # per tick (one routine OR one pipeline-pick OR noop). Pin both
    # so a regression in the starter doesn't silently flip cadence
    # back to 15 min or reintroduce the legacy while-loop fan-out.
    workflow_body = next(
        c for p, c in bundle.files if p == ".github/workflows/ship-trigger-schedule.yml"
    )
    assert 'cron: "*/30 * * * *"' in workflow_body
    assert "--pipeline-fallback" in workflow_body
    assert "next_action.kind" in workflow_body
    assert "shipctl run --specialist" in workflow_body
    # The legacy while-loop is gone in Phase 2.
    assert "while IFS= read -r routine" not in workflow_body


def test_compose_omits_generated_knowledge_by_default() -> None:
    """PR 1 installs infra only; generated knowledge lands in PR 2."""

    from backend.app.services.seed_bundle import (
        REPO_INTEL_PLACEHOLDER_PATH,
        compose_seed_files,
    )

    bundle = compose_seed_files(
        knowledge_slugs=[],
        tracker_kind=None,
        repo_full_name="acme/widgets",
        seeded_at=_seeded_at(),
    )

    paths = [p for p, _ in bundle.files]
    assert REPO_INTEL_PLACEHOLDER_PATH not in paths
    assert not any(p.startswith(".ship/knowledge/") for p in paths)
    # Bundle 0.8: exactly one workflow file lands in the customer repo.
    workflow_paths = [p for p in paths if p.startswith(".github/workflows/")]
    assert workflow_paths == [".github/workflows/ship-trigger-schedule.yml"]


def test_compose_emits_repo_intel_placeholder_when_explicit() -> None:
    from backend.app.services.seed_bundle import (
        REPO_INTEL_PLACEHOLDER_PATH,
        compose_seed_files,
    )

    bundle = compose_seed_files(
        knowledge_slugs=[],
        tracker_kind=None,
        repo_intel_placeholder=True,
        seeded_at=_seeded_at(),
    )

    assert REPO_INTEL_PLACEHOLDER_PATH in [p for p, _ in bundle.files]


def test_compose_skips_repo_intel_placeholder_when_gated_false() -> None:
    """``repo_intel_placeholder=False`` omits the stub entirely so
    callers that bring their own intel doc don't get clobbered."""

    from backend.app.services.seed_bundle import (
        REPO_INTEL_PLACEHOLDER_PATH,
        compose_seed_files,
    )

    bundle = compose_seed_files(
        knowledge_slugs=[],
        tracker_kind=None,
        repo_intel_placeholder=False,
        seeded_at=_seeded_at(),
    )

    paths = [p for p, _ in bundle.files]
    assert REPO_INTEL_PLACEHOLDER_PATH not in paths


def test_compose_writes_v2_marker_with_bundle_hash() -> None:
    """``.ship/state/wizard-seed.v2.json`` carries the bundle hash so
    a re-run can detect drift without re-fetching the catalog."""

    from backend.app.services.lane_recipes import DEFAULT_BUNDLE
    from backend.app.services.seed_bundle import (
        WIZARD_SEED_MARKER_PATH,
        compose_seed_files,
    )

    bundle = compose_seed_files(
        bundle=DEFAULT_BUNDLE,
        knowledge_slugs=[],
        tracker_kind="linear",
        seeded_at=_seeded_at(),
        ship_version="0.11.2",
    )

    paths = [p for p, _ in bundle.files]
    assert WIZARD_SEED_MARKER_PATH in paths

    body = next(c for p, c in bundle.files if p == WIZARD_SEED_MARKER_PATH)
    payload = json.loads(body)
    # 12-char prefix of sha256 — narrow but enough surface for
    # eyeball drift detection in dashboard tooltips.
    assert isinstance(payload["bundle_version"], str)
    assert len(payload["bundle_version"]) == 12
    assert payload["bundle"] == list(DEFAULT_BUNDLE)
    assert payload["tracker_kind"] == "linear"
    # Marker is what the dashboard reads; unstable timestamp formats
    # would defeat its drift-detection role.
    assert payload["seeded_at"] == "2026-04-24T12:00:00Z"
    assert payload["ship_version"] == "0.11.2"
    # ``bundle_hash`` echoed onto SeedBundle for the caller (the
    # route audit-logs it).
    assert bundle.bundle_hash == payload["bundle_version"]


def test_compose_omits_adhoc_agent_run_workflow() -> None:
    """The retired one-shot Requests surface no longer ships a runner."""

    from backend.app.services.seed_bundle import compose_seed_files

    bundle = compose_seed_files(
        # Use a single-pattern bundle so we prove the ad-hoc workflow
        # ships even when the bundle wouldn't otherwise pull it.
        bundle=("flow-pr-self-review",),
        knowledge_slugs=[],
        tracker_kind=None,
        seeded_at=_seeded_at(),
    )

    paths = [p for p, _ in bundle.files]
    assert not any(p.endswith("/adhoc-agent-run.yml") for p in paths)


def test_compose_self_heal_workflow_inspects_failed_run_context() -> None:
    from backend.app.services import starter_workflows

    body = starter_workflows.read_yaml("pipeline-self-heal") or ""

    assert "ship_failed_run_id" in body
    assert "gh run view" in body
    assert "E2E_CONSOLE_BASE_URL" in body
    assert "missing_secret" in body


def test_compose_tracker_fsm_gated_by_kind() -> None:
    """``include_fsm=False`` drops the FSM doc entirely — the only
    way to opt the document out short of editing the seed PR by hand.

    With ``include_fsm=True`` (the default) the doc renders even when
    ``tracker_kind`` is ``None`` (so the operator sees the "not
    connected yet" header instead of a missing file)."""

    from backend.app.services.seed_bundle import compose_seed_files
    from backend.app.services.tracker_fsm import FSM_INSTALL_PATH

    on = compose_seed_files(
        knowledge_slugs=[],
        tracker_kind=None,
        include_fsm=True,
        seeded_at=_seeded_at(),
    )
    assert FSM_INSTALL_PATH in [p for p, _ in on.files]
    assert on.includes_fsm is True

    off = compose_seed_files(
        knowledge_slugs=[],
        tracker_kind=None,
        include_fsm=False,
        seeded_at=_seeded_at(),
    )
    assert FSM_INSTALL_PATH not in [p for p, _ in off.files]
    assert off.includes_fsm is False


def test_compose_idempotent_same_bundle_same_files() -> None:
    """Two ``compose_seed_files`` calls with the same inputs produce
    byte-identical files — the wizard's re-run path relies on this
    so idempotency-marker comparison stays meaningful across calls."""

    from backend.app.services.lane_recipes import DEFAULT_BUNDLE
    from backend.app.services.seed_bundle import compose_seed_files

    pinned = _seeded_at()
    a = compose_seed_files(
        bundle=DEFAULT_BUNDLE,
        knowledge_slugs=[],
        tracker_kind="linear",
        repo_full_name="acme/widgets",
        seeded_at=pinned,
        ship_version="0.11.2",
    )
    b = compose_seed_files(
        bundle=DEFAULT_BUNDLE,
        knowledge_slugs=[],
        tracker_kind="linear",
        repo_full_name="acme/widgets",
        seeded_at=pinned,
        ship_version="0.11.2",
    )

    assert a.files == b.files
    assert a.bundle_hash == b.bundle_hash
    assert a.knowledge_slugs == b.knowledge_slugs


# ---------------------------------------------------------------------------
# Legacy guard rails — kept because they're the only coverage of the
# tracker_fsm helper's no-tracker / workspace-override branches.
# ---------------------------------------------------------------------------


def test_compose_rejects_empty_bundle() -> None:
    """Empty bundle is a programming error — surface it loudly."""

    from backend.app.services.seed_bundle import compose_seed_files

    with pytest.raises(ValueError):
        compose_seed_files(
            bundle=(),
            knowledge_slugs=[],
            tracker_kind=None,
        )


def test_render_tracker_fsm_handles_no_tracker() -> None:
    from backend.app.services.tracker_fsm import render_tracker_fsm

    body = render_tracker_fsm(None, repo_full_name="acme/widgets")
    assert "not connected yet" in body.lower()
    assert "no tracker selected yet" in body.lower()


def test_render_tracker_fsm_surfaces_workspace_override() -> None:
    from backend.app.services.tracker_fsm import render_tracker_fsm

    body = render_tracker_fsm(
        "jira",
        workspace_default_kind="linear",
        repo_full_name="acme/widgets",
    )
    assert "jira" in body.lower()
    assert "overrides the workspace default" in body.lower()
    assert "linear" in body.lower()
