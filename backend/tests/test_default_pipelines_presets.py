"""Preset → lane resolution tests.

Post-RFC-0008 C3.3 the source of truth moved from the old
``default_pipelines`` module into:

- Pattern ``spec.enabled_on_install.presets`` — the catalog says which
  presets enable which pattern-backed lanes.
- :data:`backend.app.services.lane_recipes._EXTRA_RECIPES` — the two
  lanes that don't have a pattern today (``code_map`` /
  ``tech_debt``); preset gating lives inline there.

Post-Wave-8a P5-01 the **preset catalog itself** collapsed to a single
canonical ``"default"`` entry. Legacy ids (``web-app``, ``api-backend``,
…) keep being accepted at the API boundary for backwards compatibility,
but they all funnel through
:func:`backend.app.services.lane_recipes.normalize_preset` which maps
them to ``"default"``. Tests below lock both the helper's pure-function
contract and the resulting lane-resolution shape so a stray edit can't
silently regress the collapse.

Full HTTP-contract / persistence coverage still lives in
``test_v1_repos.py::test_activate_with_preset_*``.
"""

from __future__ import annotations

import pytest

from backend.app.services.lane_recipes import (
    DEFAULT_BUNDLE,
    KNOWN_PRESETS,
    LEGACY_PRESETS,
    lane_recipes,
    list_lane_recipes,
    normalize_preset,
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


# ---------------------------------------------------------------------------
# normalize_preset — pure function under test (no I/O, no globals).
# ---------------------------------------------------------------------------


def test_normalize_preset_none_collapses_to_default():
    assert normalize_preset(None) == "default"


def test_normalize_preset_default_is_idempotent():
    assert normalize_preset("default") == "default"
    # Idempotency contract: ``normalize(normalize(x)) == normalize(x)``.
    assert normalize_preset(normalize_preset("default")) == "default"


@pytest.mark.parametrize("legacy", sorted(LEGACY_PRESETS))
def test_normalize_preset_collapses_every_legacy_id(legacy: str):
    """All 14 historical preset ids funnel into ``"default"``."""
    assert normalize_preset(legacy) == "default"


def test_normalize_preset_passes_unknown_strings_through_unchanged():
    """Forward-compat: a future custom preset id (not legacy, not
    ``"default"``) survives the helper unchanged so the catalog can
    introduce new presets without a code edit here."""
    assert normalize_preset("custom-x") == "custom-x"
    # Idempotent for unknown strings too.
    assert normalize_preset(normalize_preset("custom-x")) == "custom-x"


def test_known_presets_is_exactly_default():
    """Post-P5-01 only one preset is meaningful end-to-end."""
    assert KNOWN_PRESETS == ("default",)


def test_lane_recipes_dict_maps_default_to_canonical_bundle():
    """The ``lane_recipes`` map is the entry point sibling D's Plays
    installer reads. Post-collapse it has exactly one entry pointing
    at the canonical ``DEFAULT_BUNDLE`` tuple."""
    assert list(lane_recipes.keys()) == ["default"]
    assert lane_recipes["default"] is DEFAULT_BUNDLE


# ---------------------------------------------------------------------------
# resolve_enabled_lane_ids — wired through normalize_preset.
# ---------------------------------------------------------------------------


def test_resolve_enabled_lane_ids_handles_none():
    """``None`` collapses to ``"default"`` → all default-enabled
    recipes (every recipe except ``self_heal``)."""
    none_fallback = resolve_enabled_lane_ids(None)
    assert "pr_review" in none_fallback
    assert "self_heal" not in none_fallback


def test_resolve_enabled_lane_ids_default_returns_default_enabled_set():
    enabled = resolve_enabled_lane_ids("default")
    assert "pr_review" in enabled
    assert "tech_debt" in enabled
    assert "code_map" in enabled
    # ``self_heal`` is opt-in; ``"default"`` does NOT auto-enable it.
    assert "self_heal" not in enabled


@pytest.mark.parametrize("legacy", sorted(LEGACY_PRESETS))
def test_resolve_enabled_lane_ids_collapses_every_legacy_to_default(legacy: str):
    """Legacy preset strings normalize to ``"default"`` and resolve
    to the same lane set ``"default"`` does — the whole point of
    the P5-01 collapse."""
    assert resolve_enabled_lane_ids(legacy) == resolve_enabled_lane_ids("default")


def test_resolve_enabled_lane_ids_unknown_string_falls_through_to_per_recipe_gate():
    """Forward-compat: a future custom preset that's NOT in
    :data:`LEGACY_PRESETS` and NOT ``"default"`` falls through to
    per-recipe :attr:`LaneRecipe.preset_enabled` gating. With no
    recipe currently advertising such a preset, the result is the
    empty set — but the code path is exercised so the contract
    holds when sibling tickets opt recipes into custom presets."""
    assert resolve_enabled_lane_ids("custom-not-a-preset") == frozenset()


def test_every_known_preset_resolves_to_something():
    """Regression guard — the single canonical preset must always
    enable at least one lane, otherwise the dashboard renders empty
    on a brand-new workspace."""
    for preset in KNOWN_PRESETS:
        ids = resolve_enabled_lane_ids(preset)
        assert ids, f"preset {preset} enables no lanes"


@pytest.mark.asyncio
async def test_seed_default_pipelines_respects_preset_for_new_rows(
    db_session, seed_workspace
):
    _, _raw, workspace = seed_workspace

    # Legacy ``"cli"`` preset normalizes to ``"default"`` post-P5-01;
    # new rows reflect the default-enabled set, not the pre-collapse
    # cli-specific shape.
    pipelines = await seed_default_pipelines(
        db_session, workspace.id, preset="cli"
    )
    by_kind = {p.lane_id: p for p in pipelines}
    enabled = resolve_enabled_lane_ids("cli")
    for kind, row in by_kind.items():
        assert row.enabled is (kind in enabled), (
            f"{kind} enabled={row.enabled}; expected {kind in enabled}"
        )


@pytest.mark.asyncio
async def test_seed_default_pipelines_is_additive_only(db_session, seed_workspace):
    """Re-seeding with a different preset must not flip an existing
    row's ``enabled`` — user customisations win over presets."""
    _, _raw, workspace = seed_workspace

    # Both legacy presets collapse to ``"default"`` post-P5-01 so the
    # second call is functionally a no-op for ``enabled``; the test
    # still guards the additive-only invariant.
    await seed_default_pipelines(db_session, workspace.id, preset="cli")

    pipelines = await seed_default_pipelines(
        db_session, workspace.id, preset="monorepo"
    )
    by_kind = {p.lane_id: p for p in pipelines}
    # ``self_heal`` was created disabled on the first pass — the second
    # call does NOT re-enable it (additive-only).
    assert by_kind["self_heal"].enabled is False
