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
