"""Unit tests for :data:`DEFAULT_BUNDLE` (Plays/Inbox redesign — Wave 8a P5-04).

Originally validated against ``artifacts/patterns/`` ARTIFACT.md files;
the catalog was retired in Phase 2.4 Step D and the canonical role
defaults moved to ``backend/app/resources/agent_roles/<slug>.md``. This
file pins the new shape:

* The tuple is non-empty, no duplicates.
* Every entry maps to a real ``agent_roles/<slug>.md`` file.
* Every entry has a short, human-readable inclusion reason for the
  wizard's "Confirm bootstrap" step.
* Every routine specialist referenced by ``DEFAULT_SEED_LANES`` is
  inside ``DEFAULT_BUNDLE`` (the seed bundle and the routine schedule
  cannot drift out of sync).
"""

from __future__ import annotations

from pathlib import Path

from backend.app.services.lane_recipes import (
    DEFAULT_BUNDLE,
    DEFAULT_BUNDLE_REASONS,
    DEFAULT_SEED_LANES,
)


AGENT_ROLES_DIR = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "resources"
    / "agent_roles"
)


def test_default_bundle_nonempty() -> None:
    assert DEFAULT_BUNDLE, "DEFAULT_BUNDLE must list at least one role"
    # Sanity: bundle stays in a reasonable window. 7 routines + ≤16
    # specialists is the canonical upper bound.
    assert 7 <= len(DEFAULT_BUNDLE) <= 25, (
        f"DEFAULT_BUNDLE drifted outside the 7–25 target window "
        f"(got {len(DEFAULT_BUNDLE)})"
    )
    # No duplicates — order matters for display but the set has to be unique.
    assert len(set(DEFAULT_BUNDLE)) == len(DEFAULT_BUNDLE), (
        "DEFAULT_BUNDLE has duplicate keys"
    )


def test_every_bundle_role_file_exists() -> None:
    for slug in DEFAULT_BUNDLE:
        path = AGENT_ROLES_DIR / f"{slug}.md"
        assert path.is_file(), (
            f"DEFAULT_BUNDLE references {slug!r} but "
            f"{path.relative_to(AGENT_ROLES_DIR.parents[3])} does not exist"
        )


def test_every_bundle_role_has_reason() -> None:
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


def test_every_routine_specialist_is_in_bundle() -> None:
    """Routines and the bundle must agree on which roles ship by default.

    If a lane lists ``specialist: foo`` but ``foo`` isn't in
    ``DEFAULT_BUNDLE``, the wizard renders a routine that has no
    matching role file — drift the seed cannot recover from.
    """
    routine_specialists = {
        body["specialist"]
        for body in DEFAULT_SEED_LANES.values()
        if isinstance(body.get("specialist"), str)
    }
    missing = routine_specialists - set(DEFAULT_BUNDLE)
    assert not missing, (
        f"DEFAULT_SEED_LANES reference specialists not in DEFAULT_BUNDLE: "
        f"{missing}"
    )
