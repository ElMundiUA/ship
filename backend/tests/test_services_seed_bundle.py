"""Tests for the unified wizard seed bundle composer (Wizard v2 iter 5)."""

from __future__ import annotations

import pytest


def test_compose_bundles_preset_workflows_and_config() -> None:
    from backend.app.services.seed_bundle import compose_seed_files

    bundle = compose_seed_files(
        presets=["web-app"],
        knowledge_slugs=[],
        tracker_kind=None,
        repo_full_name="acme/widgets",
    )

    paths = [p for p, _ in bundle.files]
    # At least one workflow + config.yml — exact kinds depend on the
    # preset's enabled-kinds; assert the shape not the specific list.
    assert any(p.startswith(".github/workflows/") for p in paths)
    assert ".ship/config.yml" in paths
    # Config mentions the preset label so downstream tooling can
    # discover which shape to assume.
    config_body = next(c for p, c in bundle.files if p == ".ship/config.yml")
    assert "preset: web-app" in config_body
    assert "acme/widgets" in config_body


def test_compose_includes_fsm_by_default() -> None:
    from backend.app.services.seed_bundle import compose_seed_files

    bundle = compose_seed_files(
        presets=["web-app"],
        knowledge_slugs=[],
        tracker_kind="linear",
        repo_full_name="acme/widgets",
    )

    paths = [p for p, _ in bundle.files]
    assert ".ship/tracker-fsm.md" in paths
    fsm_body = next(c for p, c in bundle.files if p == ".ship/tracker-fsm.md")
    # Mapping header for the chosen tracker must appear.
    assert "linear" in fsm_body.lower()
    # States show up as headings regardless of tracker.
    assert "rework" in fsm_body.lower()
    assert "needs_info" in fsm_body.lower()


def test_compose_skips_fsm_when_disabled() -> None:
    from backend.app.services.seed_bundle import compose_seed_files

    bundle = compose_seed_files(
        presets=["web-app"],
        knowledge_slugs=[],
        tracker_kind="linear",
        include_fsm=False,
        repo_full_name="acme/widgets",
    )

    assert not any(p == ".ship/tracker-fsm.md" for p, _ in bundle.files)
    assert bundle.includes_fsm is False


def test_compose_knowledge_none_means_seed_all() -> None:
    from backend.app.services.catalog import KNOWLEDGE_STARTERS
    from backend.app.services.seed_bundle import compose_seed_files

    bundle = compose_seed_files(
        presets=["web-app"],
        knowledge_slugs=None,
        tracker_kind=None,
        repo_full_name="acme/widgets",
    )

    assert set(bundle.knowledge_slugs) == set(KNOWLEDGE_STARTERS)
    for slug in KNOWLEDGE_STARTERS:
        assert f".ship/knowledge/{slug}.md" in [p for p, _ in bundle.files]


def test_compose_knowledge_empty_list_skips() -> None:
    from backend.app.services.seed_bundle import compose_seed_files

    bundle = compose_seed_files(
        presets=["web-app"],
        knowledge_slugs=[],
        tracker_kind=None,
        repo_full_name="acme/widgets",
    )
    assert bundle.knowledge_slugs == []
    assert not any(p.startswith(".ship/knowledge/") for p, _ in bundle.files)


def test_compose_deduplicates_overlapping_presets() -> None:
    """Two presets overlapping on a workflow should yield one copy."""

    from backend.app.services.seed_bundle import compose_seed_files

    bundle = compose_seed_files(
        presets=["web-app", "api-backend"],
        knowledge_slugs=[],
        tracker_kind=None,
        repo_full_name="acme/widgets",
    )

    paths = [p for p, _ in bundle.files]
    # Path uniqueness is the core invariant the tree builder relies on.
    assert len(paths) == len(set(paths))
    # Exactly one config.yml even though each preset ships one.
    assert paths.count(".ship/config.yml") == 1


def test_compose_sorts_output_for_reviewable_diffs() -> None:
    from backend.app.services.seed_bundle import compose_seed_files

    bundle = compose_seed_files(
        presets=["web-app"],
        knowledge_slugs=None,
        tracker_kind="linear",
        repo_full_name="acme/widgets",
    )

    # Workflow YAMLs come first, then .ship/config.yml, then
    # knowledge, then the FSM. Reviewers scan top-down.
    ranks = []
    for path, _ in bundle.files:
        if path.startswith(".github/workflows/"):
            ranks.append(0)
        elif path == ".ship/config.yml":
            ranks.append(1)
        elif path.startswith(".ship/knowledge/"):
            ranks.append(2)
        elif path == ".ship/tracker-fsm.md":
            ranks.append(3)
        else:
            ranks.append(9)
    assert ranks == sorted(ranks)


def test_compose_rejects_empty_preset_list() -> None:
    from backend.app.services.seed_bundle import compose_seed_files

    with pytest.raises(ValueError):
        compose_seed_files(
            presets=[],
            knowledge_slugs=[],
            tracker_kind=None,
        )


def test_render_tracker_fsm_handles_no_tracker() -> None:
    from backend.app.services.tracker_fsm import render_tracker_fsm

    body = render_tracker_fsm(None, repo_full_name="acme/widgets")
    assert "not connected yet" in body.lower()
    # Mapping section should point at the "re-run the seed" hint.
    assert "no tracker selected yet" in body.lower()


def test_render_tracker_fsm_surfaces_workspace_override() -> None:
    from backend.app.services.tracker_fsm import render_tracker_fsm

    body = render_tracker_fsm(
        "jira",
        workspace_default_kind="linear",
        repo_full_name="acme/widgets",
    )
    # Header must show both so operators know the repo overrode.
    assert "jira" in body.lower()
    assert "overrides the workspace default" in body.lower()
    assert "linear" in body.lower()
