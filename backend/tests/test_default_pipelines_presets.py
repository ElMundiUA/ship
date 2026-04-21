"""Unit tests for :mod:`backend.app.services.default_pipelines` presets.

Covers only the in-process resolution + seeding logic. Full end-to-end
preset-flow coverage (HTTP contract, persistence, audit log) lives in
``test_v1_repos.py::test_activate_with_preset_*``.
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.services.default_pipelines import (
    DEFAULT_PIPELINES,
    KNOWN_PRESETS,
    PRESET_ENABLED_KINDS,
    resolve_enabled_kinds,
    seed_default_pipelines,
)


DEFAULT_KINDS = {spec.kind for spec in DEFAULT_PIPELINES}


def test_known_presets_match_preset_enabled_map():
    """Guards against a preset being added to one side and not the other —
    the wizard + repos route both use ``KNOWN_PRESETS`` for validation."""
    assert set(KNOWN_PRESETS) == set(PRESET_ENABLED_KINDS)


def test_preset_enabled_kinds_only_reference_real_pipelines():
    """Typos in ``PRESET_ENABLED_KINDS`` would silently disable the
    whole preset's lane set; fail loud in tests instead."""
    for preset, kinds in PRESET_ENABLED_KINDS.items():
        unknown = kinds - DEFAULT_KINDS
        assert not unknown, f"preset {preset} references unknown kinds: {unknown}"


def test_resolve_enabled_kinds_falls_back_for_unknown_presets():
    # Unknown preset → default (everything except self-heal).
    fallback = resolve_enabled_kinds("not-a-preset")
    assert "self_heal" not in fallback
    assert "pr_review" in fallback


def test_resolve_enabled_kinds_handles_none():
    none_fallback = resolve_enabled_kinds(None)
    assert "pr_review" in none_fallback
    assert "self_heal" not in none_fallback


def test_monorepo_preset_opts_into_self_heal():
    assert "self_heal" in PRESET_ENABLED_KINDS["monorepo"]


def test_web_app_preset_covers_full_sdlc_grid():
    """Web-app is the flagship "Elmundi-grade SDLC" preset: every
    materialised lane is enabled, including ``self_heal``. The UI
    pitches it as the full Elmundi grid — keep that promise in code."""
    assert PRESET_ENABLED_KINDS["web-app"] == frozenset(
        {"pr_review", "daily_standup", "tech_debt", "self_heal", "code_map"}
    )


def test_marketing_preset_covers_copy_and_cadence_lanes():
    """Marketing preset: PR gate + standup + code_map, no tech-debt,
    no self-heal. See ``artifacts/collections/preset-marketing/
    ARTIFACT.md`` for the product-shape rationale."""
    assert PRESET_ENABLED_KINDS["marketing"] == frozenset(
        {"pr_review", "daily_standup", "code_map"}
    )
    # Guard against the two lanes we intentionally leave off.
    assert "tech_debt" not in PRESET_ENABLED_KINDS["marketing"]
    assert "self_heal" not in PRESET_ENABLED_KINDS["marketing"]


def test_adoption_minimum_preset_is_minimum():
    kinds = PRESET_ENABLED_KINDS["adoption-minimum"]
    # Contract: minimum = just PR review + code map (so the dashboard
    # isn't empty on a brand-new workspace). Tighten if the product
    # direction changes.
    assert kinds == frozenset({"pr_review", "code_map"})


@pytest.mark.asyncio
async def test_seed_default_pipelines_respects_preset_for_new_rows(
    db_session, seed_workspace
):
    _, _raw, workspace = seed_workspace

    pipelines = await seed_default_pipelines(
        db_session, workspace.id, preset="cli"
    )
    by_kind = {p.kind: p for p in pipelines}
    cli_enabled = PRESET_ENABLED_KINDS["cli"]
    for kind, row in by_kind.items():
        assert row.enabled is (kind in cli_enabled), (
            f"{kind} enabled={row.enabled}; expected {kind in cli_enabled}"
        )


@pytest.mark.asyncio
async def test_seed_default_pipelines_is_additive_only(db_session, seed_workspace):
    """Re-seeding with a different preset must not flip an existing
    row's ``enabled`` — user customisations win over presets."""
    _, _raw, workspace = seed_workspace

    # First call materialises the rows per ``cli`` preset.
    await seed_default_pipelines(db_session, workspace.id, preset="cli")

    # Second call with ``monorepo`` (which would enable self_heal).
    pipelines = await seed_default_pipelines(
        db_session, workspace.id, preset="monorepo"
    )
    by_kind = {p.kind: p for p in pipelines}
    # ``self_heal`` was created disabled on the first pass — the second
    # call with a broader preset does NOT re-enable it (additive-only).
    assert by_kind["self_heal"].enabled is False
