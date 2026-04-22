"""Preset → lane resolution tests.

Post-RFC-0008 C3.3 the source of truth moved from the old
``default_pipelines`` module into:

- Pattern ``spec.enabled_on_install.presets`` — the catalog says which
  presets enable which pattern-backed lanes.
- :data:`backend.app.services.lane_recipes._EXTRA_RECIPES` — the two
  lanes that don't have a pattern today (``code_map`` /
  ``tech_debt``); preset gating lives inline there.

These tests lock the combined behaviour so a stray edit to either
surface can't silently disable a lane for a preset that used to have
it. Full HTTP-contract / persistence coverage still lives in
``test_v1_repos.py::test_activate_with_preset_*``.
"""

from __future__ import annotations

import pytest

from backend.app.services.lane_recipes import (
    KNOWN_PRESETS,
    list_lane_recipes,
    resolve_enabled_lane_ids,
    seed_default_pipelines,
)


DEFAULT_LANE_IDS = {r.lane_id for r in list_lane_recipes()}


def test_recipes_cover_the_five_stable_lane_ids():
    """The Console, dashboard and a handful of tests hard-code the
    lane ids that ship out of the box. Guard the set so renaming a
    pattern's ``lane_id`` surfaces here, not in a production 500."""
    assert DEFAULT_LANE_IDS == {
        "pr_review",
        "daily_standup",
        "code_map",
        "tech_debt",
        "self_heal",
    }


def test_resolve_enabled_lane_ids_falls_back_for_unknown_presets():
    # Unknown preset → default (every recipe whose ``default_enabled``
    # is truthy — everything except ``self_heal``).
    fallback = resolve_enabled_lane_ids("not-a-preset")
    assert "self_heal" not in fallback
    assert "pr_review" in fallback


def test_resolve_enabled_lane_ids_handles_none():
    none_fallback = resolve_enabled_lane_ids(None)
    assert "pr_review" in none_fallback
    assert "self_heal" not in none_fallback


def test_monorepo_preset_opts_into_self_heal():
    assert "self_heal" in resolve_enabled_lane_ids("monorepo")


def test_web_app_preset_covers_full_sdlc_grid():
    """Web-app is the flagship "Elmundi-grade SDLC" preset: every
    materialised lane is enabled, including ``self_heal``. The UI
    pitches it as the full Elmundi grid — keep that promise."""
    assert resolve_enabled_lane_ids("web-app") == frozenset(
        {"pr_review", "daily_standup", "tech_debt", "self_heal", "code_map"}
    )


def test_marketing_preset_covers_copy_and_cadence_lanes():
    """Marketing preset: PR gate + standup + code_map, no tech-debt,
    no self-heal. See ``artifacts/collections/preset-marketing/
    ARTIFACT.md`` for the product-shape rationale."""
    marketing = resolve_enabled_lane_ids("marketing")
    assert marketing == frozenset({"pr_review", "daily_standup", "code_map"})
    # Guard against the two lanes we intentionally leave off.
    assert "tech_debt" not in marketing
    assert "self_heal" not in marketing


def test_adoption_minimum_preset_is_minimum():
    ids = resolve_enabled_lane_ids("adoption-minimum")
    # Contract: minimum = just PR review + code map (so the dashboard
    # isn't empty on a brand-new workspace). Tighten if the product
    # direction changes.
    assert ids == frozenset({"pr_review", "code_map"})


def test_every_known_preset_resolves_to_something():
    """Regression guard — a preset missing from any pattern's
    ``enabled_on_install.presets`` AND from the ``_EXTRA_RECIPES``
    gating is still a valid preset string, but resolves to an empty
    set which silently disables the entire dashboard for that
    preset. Assert every preset turns *something* on."""
    for preset in KNOWN_PRESETS:
        ids = resolve_enabled_lane_ids(preset)
        assert ids, f"preset {preset} enables no lanes"


@pytest.mark.asyncio
async def test_seed_default_pipelines_respects_preset_for_new_rows(
    db_session, seed_workspace
):
    _, _raw, workspace = seed_workspace

    pipelines = await seed_default_pipelines(
        db_session, workspace.id, preset="cli"
    )
    by_kind = {p.lane_id: p for p in pipelines}
    cli_enabled = resolve_enabled_lane_ids("cli")
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
    by_kind = {p.lane_id: p for p in pipelines}
    # ``self_heal`` was created disabled on the first pass — the second
    # call with a broader preset does NOT re-enable it (additive-only).
    assert by_kind["self_heal"].enabled is False
