"""Unit tests for :mod:`backend.app.services.inbox.profiles` (P2-11).

Cover the catalog-load contract, override-merge semantics, inheritance
+ cycle detection, and the convenience entry that walks a real
pattern frontmatter.
"""

from __future__ import annotations

import pathlib

import pytest

from backend.app.services.inbox import profiles as profile_service
from backend.app.services.inbox.profiles import (
    INBOX_TYPES,
    ProfileCatalogError,
    load_profile_catalog,
    resolve_for_pattern,
    resolve_profile,
)


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    profile_service._reset_cache()
    yield
    profile_service._reset_cache()


def test_load_catalog_succeeds_on_real_file():
    catalog = load_profile_catalog()
    expected = {
        "silent",
        "scan_default",
        "scan_with_autofix",
        "flow_pr",
        "flow_release",
        "flow_incident",
        "flow_reporting",
        "role_reviewer",
        "onboarding",
    }
    assert set(catalog.keys()) == expected


def test_silent_profile_has_all_types_disabled():
    resolved = resolve_profile("silent")
    assert set(resolved.rules.keys()) == set(INBOX_TYPES)
    for inbox_type in INBOX_TYPES:
        rule = resolved.rules[inbox_type]
        assert rule.enabled is False
        assert rule.handle is None
        assert rule.when == ()


def test_scan_with_autofix_inherits_scan_default():
    resolved = resolve_profile("scan_with_autofix")

    clarification = resolved.rules["clarification"]
    assert clarification.enabled is False
    assert clarification.handle is None

    approval = resolved.rules["approval"]
    assert approval.enabled is True
    assert approval.handle == "code_owner"
    assert "autofix_proposed" in approval.when

    failure = resolved.rules["failure"]
    assert failure.enabled is True
    assert failure.handle == "ops_oncall"


def test_overrides_applied_to_specific_type_only():
    overrides = {"failure": {"handle": "repo_maintainer"}}
    resolved = resolve_profile("flow_incident", overrides=overrides)

    assert resolved.rules["failure"].handle == "repo_maintainer"
    assert resolved.rules["failure"].enabled is True
    assert "play_failed_repeatedly" in resolved.rules["failure"].when

    clarification = resolved.rules["clarification"]
    assert clarification.handle == "incident_commander"


def test_resolve_for_pattern_uses_silent_when_inbox_missing():
    pattern_meta: dict = {"id": "fake-pattern", "spec": {}}
    resolved = resolve_for_pattern(pattern_meta)
    assert resolved.profile_name == "silent"
    for inbox_type in INBOX_TYPES:
        assert resolved.rules[inbox_type].enabled is False


def test_resolve_for_pattern_handles_real_frontmatter():
    artifact = (
        _REPO_ROOT
        / "artifacts"
        / "patterns"
        / "scan-cost-delta"
        / "ARTIFACT.md"
    )
    # Defer to the catalog service's frontmatter parser: it handles
    # the `@author` / reserved-token quoting quirk that raw
    # ``yaml.safe_load`` chokes on. Keeping that quirk out of
    # ``profiles.py`` is intentional — the resolver consumes already
    # parsed dicts, not files.
    from backend.app.services.catalog import _split_frontmatter

    raw = artifact.read_text(encoding="utf-8")
    meta, _body = _split_frontmatter(raw, artifact)

    resolved = resolve_for_pattern(meta)
    assert resolved.profile_name == "scan_default"
    assert resolved.rules["improvement"].handle == "eng_manager"
    assert resolved.rules["improvement"].enabled is True
    assert "recurring_finding_detected" in resolved.rules["improvement"].when


def test_inheritance_cycle_raises(tmp_path: pathlib.Path):
    catalog_path = tmp_path / "cycle.yaml"
    catalog_path.write_text(
        """
inbox_profiles:
  alpha:
    inherits: beta
    failure: { enabled: false }
  beta:
    inherits: alpha
    failure: { enabled: false }
""".lstrip()
    )
    catalog = load_profile_catalog(catalog_path)
    with pytest.raises(ProfileCatalogError, match="cycle"):
        resolve_profile("alpha", catalog=catalog)


def test_unknown_profile_raises():
    with pytest.raises(ProfileCatalogError, match="does_not_exist"):
        resolve_profile("does_not_exist")


def test_enabled_rule_without_handle_is_invalid(tmp_path: pathlib.Path):
    catalog_path = tmp_path / "broken.yaml"
    catalog_path.write_text(
        """
inbox_profiles:
  broken:
    failure:
      enabled: true
      handle: null
""".lstrip()
    )
    with pytest.raises(ProfileCatalogError, match="handle"):
        load_profile_catalog(catalog_path)
