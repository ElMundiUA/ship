"""Unit tests for :mod:`backend.app.services.catalog`.

The catalog is on-disk truth: we walk ``artifacts/**/ARTIFACT.md`` once
per mtime signature and cache the parsed frontmatter. These tests pin
the contract the rest of the backend relies on (presets are filtered
by ``group == 'preset'`` and starter-workflow YAMLs — the four baked-in
``.github/workflows/*.yml`` templates the Pipeline install flow
commits into customer repos — load through the
:mod:`starter_workflows` shim) so a missing ARTIFACT.md or a typo in
``spec`` is caught at CI time rather than in production.

RFC-0007 Phase 6 retired ``artifact_kind=workflow`` from the public
catalog: the only workflow-related surface that remains is the
starter-YAML shim exposed as ``workflow_install_filename`` /
``read_starter_yaml``.
"""

from __future__ import annotations

import pytest

from backend.app.services import catalog


@pytest.fixture(autouse=True)
def _clear_cache():
    catalog.invalidate_cache()
    yield
    catalog.invalidate_cache()


def test_workflow_install_filename_covers_default_pipelines():
    """Every default pipeline has an installable starter YAML."""
    for workflow_id in (
        "pr-and-ci-gate",
        "scheduled-sdlc-lane",
        "parallel-audit-lanes",
        "pipeline-self-heal",
    ):
        assert catalog.workflow_install_filename(workflow_id) == (
            f"{workflow_id}.yml"
        )


def test_read_starter_yaml_returns_body_for_starter_workflows():
    for workflow_id in (
        "pr-and-ci-gate",
        "scheduled-sdlc-lane",
        "parallel-audit-lanes",
        "pipeline-self-heal",
    ):
        body = catalog.read_starter_yaml(workflow_id)
        assert body is not None, f"missing workflow.yml for {workflow_id}"
        assert "ship_callback_url" in body, (
            f"{workflow_id}.yml missing the Ship callback input"
        )


def test_read_starter_yaml_returns_none_for_unknown_workflow():
    assert catalog.read_starter_yaml("does-not-exist") is None


def test_workflow_install_filename_returns_none_for_unknown():
    assert catalog.workflow_install_filename("does-not-exist") is None


def test_list_presets_filters_by_group():
    presets = catalog.list_presets()
    for entry in presets:
        assert entry.group == "preset", entry.id
    preset_ids = {p.id for p in presets}
    # The catalog ships at least these presets today.
    assert "preset-web-app" in preset_ids
    assert "preset-api-backend" in preset_ids
    assert "preset-mobile-app" in preset_ids


def test_preset_exposes_preset_id():
    entry = catalog.get_collection("preset-web-app")
    assert entry is not None
    assert entry.preset_id == "web-app"


# ---------------------------------------------------------------------------
# RFC-0008 metadata & workflow resolution
# ---------------------------------------------------------------------------


class _FakeArtifact:
    """Shim that mimics :class:`CatalogArtifact` for the resolver tests.

    Only the fields ``resolve_lane_workflow`` inspects matter, so we
    avoid building a full on-disk artifact per test case.
    """

    def __init__(
        self,
        *,
        pattern_id: str,
        category: str | None,
        modes: list[str],
        default_trigger: dict | None = None,
        lane_workflow: str | None = None,
    ) -> None:
        self.id = pattern_id
        self.category = category
        self.modes = modes
        self.default_trigger = default_trigger
        self.lane_workflow = lane_workflow


def test_resolve_lane_workflow_explicit_override():
    fake = _FakeArtifact(
        pattern_id="scan-foo",
        category="scan",
        modes=["lane"],
        lane_workflow="my-custom",
    )
    assert catalog.resolve_lane_workflow(fake) == "my-custom"


def test_resolve_lane_workflow_pr_review_by_trigger():
    fake = _FakeArtifact(
        pattern_id="flow-pr-self-review",
        category="flow",
        modes=["lane"],
        default_trigger={"kind": "event", "event": "pull_request"},
    )
    assert catalog.resolve_lane_workflow(fake) == "pr-and-ci-gate"


def test_resolve_lane_workflow_self_heal_by_prefix():
    fake = _FakeArtifact(
        pattern_id="op-workflow-self-heal",
        category="op",
        modes=["lane"],
        default_trigger={"kind": "schedule", "cron": "0 4 * * *"},
    )
    assert catalog.resolve_lane_workflow(fake) == "pipeline-self-heal"


def test_resolve_lane_workflow_scan_by_category():
    fake = _FakeArtifact(
        pattern_id="scan-tech-debt",
        category="scan",
        modes=["lane"],
        default_trigger={"kind": "schedule", "cron": "0 6 * * 1"},
    )
    assert catalog.resolve_lane_workflow(fake) == "parallel-audit-lanes"


def test_resolve_lane_workflow_defaults_to_scheduled_sdlc_lane():
    fake = _FakeArtifact(
        pattern_id="role-ba",
        category="role",
        modes=["lane", "request"],
        default_trigger={"kind": "event", "event": "issues.labeled"},
    )
    assert catalog.resolve_lane_workflow(fake) == "scheduled-sdlc-lane"


def test_resolve_lane_workflow_returns_none_for_common_patterns():
    fake = _FakeArtifact(
        pattern_id="common-base",
        category="common",
        modes=[],
    )
    assert catalog.resolve_lane_workflow(fake) is None


def test_list_patterns_by_mode_filters_by_modes_field():
    """Post-RFC-0008: every pattern declares ``modes`` explicitly.

    ``common-*`` patterns (shared fragments, ``modes: []``) are
    non-executable and must not appear in either the lane picker or
    the request picker. Every other pattern must declare at least one
    of ``lane`` / ``request``.
    """
    lane_patterns = catalog.list_patterns_by_mode("lane")
    request_patterns = catalog.list_patterns_by_mode("request")
    all_patterns = catalog.list_patterns()

    common_ids = {p.id for p in all_patterns if p.id.startswith("common-")}
    assert common_ids, "expected at least one common-* pattern (shared fragment)"

    lane_ids = {p.id for p in lane_patterns}
    request_ids = {p.id for p in request_patterns}
    # Shared fragments must be excluded from both pickers.
    assert common_ids.isdisjoint(lane_ids)
    assert common_ids.isdisjoint(request_ids)

    # Every non-common pattern must surface in at least one picker.
    executable = {p.id for p in all_patterns} - common_ids
    assert executable <= (lane_ids | request_ids)


def test_list_patterns_by_mode_rejects_unknown_mode():
    import pytest
    with pytest.raises(ValueError):
        catalog.list_patterns_by_mode("bogus")


def test_catalog_splits_roles_from_knowledge_recipes():
    roles = catalog.list_specialist_role_patterns()
    recipes = catalog.list_knowledge_recipe_patterns()

    role_ids = {entry.id for entry in roles}
    recipe_ids = {entry.id for entry in recipes}

    assert "role-ba" in role_ids
    assert "role-developer" in role_ids
    assert "flow-pr-self-review" in recipe_ids
    assert "scan-security-deps" in recipe_ids
    assert role_ids.isdisjoint(recipe_ids)


def test_knowledge_recipe_starters_are_generated_from_patterns():
    slugs = catalog.knowledge_starter_slugs()
    assert "code-style" in slugs
    assert "ui-runbook" in slugs
    assert "ship-recipes/flow-pr-self-review" in slugs
    assert "ship-recipes/role-ba" not in slugs

    files = catalog.knowledge_starter_files(["ship-recipes/flow-pr-self-review"])
    assert [path for path, _ in files] == [
        ".ship/knowledge/ship-recipes/flow-pr-self-review.md"
    ]
    body = files[0][1]
    assert body.startswith("# PR self-review")
    assert "## Recommended Tools" in body
    assert "## Legacy Recipe Body" in body
    assert "Source: `pattern/flow-pr-self-review`" in body
