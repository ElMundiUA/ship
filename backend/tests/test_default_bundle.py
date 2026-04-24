"""Unit tests for :data:`DEFAULT_BUNDLE` (Plays/Inbox redesign — Wave 8a P5-04).

Locks in the contract sibling subagents downstream of this ticket
depend on:

* The tuple is non-empty and every entry maps to a real pattern
  directory under ``artifacts/patterns/``.
* Every entry has a short, human-readable inclusion reason for the
  Wave-8c "Confirm bootstrap" wizard step.
* No silent / system-internal pattern leaks into the operator-visible
  default set.
* The bundle covers a minimum trigger diversity (at least one
  PR-attached, one scheduled, one release-time Play) so a fresh repo
  sees the three loops fire without manual configuration.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from backend.app.services import catalog as catalog_service
from backend.app.services.lane_recipes import (
    DEFAULT_BUNDLE,
    DEFAULT_BUNDLE_REASONS,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PATTERNS_DIR = REPO_ROOT / "artifacts" / "patterns"


@lru_cache(maxsize=1)
def _patterns_by_id() -> dict[str, catalog_service.CatalogArtifact]:
    return {entry.id: entry for entry in catalog_service.list_patterns()}


def _entry(pattern_key: str) -> catalog_service.CatalogArtifact:
    entry = _patterns_by_id().get(pattern_key)
    assert entry is not None, (
        f"{pattern_key}: not found in catalog — does the pattern dir exist?"
    )
    return entry


def test_default_bundle_nonempty() -> None:
    assert DEFAULT_BUNDLE, "DEFAULT_BUNDLE must list at least one pattern"
    # Sanity: bundle stays in the 6–10 range called out by the
    # planning doc; flag if someone silently doubles it.
    assert 6 <= len(DEFAULT_BUNDLE) <= 10, (
        f"DEFAULT_BUNDLE drifted outside the 6–10 target window "
        f"(got {len(DEFAULT_BUNDLE)})"
    )
    # No duplicates — order matters for display but the set has to be unique.
    assert len(set(DEFAULT_BUNDLE)) == len(DEFAULT_BUNDLE), (
        "DEFAULT_BUNDLE has duplicate pattern keys"
    )


def test_every_bundle_pattern_exists() -> None:
    for key in DEFAULT_BUNDLE:
        artifact = PATTERNS_DIR / key / "ARTIFACT.md"
        assert artifact.is_file(), (
            f"DEFAULT_BUNDLE references {key!r} but "
            f"{artifact.relative_to(REPO_ROOT)} does not exist"
        )


def test_every_bundle_pattern_has_reason() -> None:
    missing = [k for k in DEFAULT_BUNDLE if k not in DEFAULT_BUNDLE_REASONS]
    assert not missing, (
        f"DEFAULT_BUNDLE_REASONS is missing entries for: {missing}"
    )
    extras = [k for k in DEFAULT_BUNDLE_REASONS if k not in DEFAULT_BUNDLE]
    assert not extras, (
        f"DEFAULT_BUNDLE_REASONS has stale entries not in the bundle: "
        f"{extras}"
    )


def test_every_reason_is_short_and_human() -> None:
    # Internal vocabulary that should not leak into the wizard copy.
    forbidden = ("profile", "lane", "pattern_id", "scan_default", "flow_pr")
    for key, reason in DEFAULT_BUNDLE_REASONS.items():
        assert reason and reason.strip() == reason, (
            f"{key}: reason has surrounding whitespace or is empty"
        )
        assert len(reason) <= 120, (
            f"{key}: reason is {len(reason)} chars, must be ≤120"
        )
        lowered = reason.lower()
        for term in forbidden:
            assert term not in lowered, (
                f"{key}: reason contains internal term {term!r} — "
                f"keep wizard copy in plain English"
            )


def test_every_bundle_pattern_is_not_silent() -> None:
    for key in DEFAULT_BUNDLE:
        entry = _entry(key)
        inbox = entry.spec.get("inbox") or {}
        profile = inbox.get("profile") if isinstance(inbox, dict) else None
        assert profile != "silent", (
            f"{key}: inbox.profile=silent — system-internal patterns "
            f"must not appear in DEFAULT_BUNDLE"
        )


def _trigger_kind(
    entry: catalog_service.CatalogArtifact,
) -> tuple[str | None, str | None, str | None]:
    """Return ``(kind, event, pattern)`` from a pattern's ``default_trigger``."""
    trigger = entry.default_trigger or {}
    if not isinstance(trigger, dict):
        return (None, None, None)
    return (
        trigger.get("kind"),
        trigger.get("event"),
        trigger.get("pattern"),
    )


def test_bundle_has_minimum_diversity() -> None:
    has_pr_attached = False
    has_scheduled = False
    has_release_time = False

    for key in DEFAULT_BUNDLE:
        kind, event, pattern = _trigger_kind(_entry(key))
        if kind == "event" and isinstance(event, str):
            if event.startswith("pull_request"):
                has_pr_attached = True
            elif event == "push" and isinstance(pattern, str) and "tags/" in pattern:
                has_release_time = True
        elif kind == "schedule":
            has_scheduled = True

    assert has_pr_attached, (
        "DEFAULT_BUNDLE needs at least one PR-attached play "
        "(default_trigger.kind=event, event=pull_request*)"
    )
    assert has_scheduled, (
        "DEFAULT_BUNDLE needs at least one scheduled scanner "
        "(default_trigger.kind=schedule)"
    )
    assert has_release_time, (
        "DEFAULT_BUNDLE needs at least one release-time play "
        "(default_trigger.kind=event, event=push, pattern=refs/tags/*)"
    )
