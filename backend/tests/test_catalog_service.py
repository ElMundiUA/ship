"""Unit tests for :mod:`backend.app.services.catalog`.

The catalog is on-disk truth: we walk ``artifacts/**/ARTIFACT.md`` once
per mtime signature and cache the parsed frontmatter. These tests pin
the contract the rest of the backend relies on (workflows expose
``install_target`` / ``install_filename``, presets are filtered by
``group == 'preset'``, and starter YAMLs load from the workflow folder
when present) so a missing ARTIFACT.md or a typo in ``spec`` is caught
at CI time rather than in production.
"""

from __future__ import annotations

import pytest

from backend.app.services import catalog


@pytest.fixture(autouse=True)
def _clear_cache():
    catalog.invalidate_cache()
    yield
    catalog.invalidate_cache()


def test_list_workflows_yields_catalog_entries():
    workflows = catalog.list_workflows()
    ids = {w.id for w in workflows}
    # These are the five catalog workflow IDs default pipelines rely on
    # + the hosted-e2e helper introduced in Phase 2.
    assert "pr-and-ci-gate" in ids
    assert "scheduled-sdlc-lane" in ids
    assert "parallel-audit-lanes" in ids
    assert "pipeline-self-heal" in ids
    assert "hosted-e2e-regression" in ids


def test_workflow_install_target_and_filename():
    assert catalog.workflow_install_target("pr-and-ci-gate") == (
        ".github/workflows/pr-and-ci-gate.yml"
    )
    assert catalog.workflow_install_filename("pr-and-ci-gate") == (
        "pr-and-ci-gate.yml"
    )
    assert catalog.workflow_install_filename("scheduled-sdlc-lane") == (
        "scheduled-sdlc-lane.yml"
    )


def test_read_starter_yaml_returns_body_for_catalog_workflows():
    for workflow_id in (
        "pr-and-ci-gate",
        "scheduled-sdlc-lane",
        "parallel-audit-lanes",
        "pipeline-self-heal",
        "hosted-e2e-regression",
    ):
        body = catalog.read_starter_yaml(workflow_id)
        assert body is not None, f"missing workflow.yml for {workflow_id}"
        assert "ship_callback_url" in body, (
            f"{workflow_id}/workflow.yml missing the Ship callback input"
        )


def test_read_starter_yaml_returns_none_for_unknown_workflow():
    assert catalog.read_starter_yaml("does-not-exist") is None


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


def test_get_workflow_returns_none_for_missing():
    assert catalog.get_workflow("does-not-exist") is None
