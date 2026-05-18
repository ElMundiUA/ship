"""Unit tests for inbox headline derivation (ELS-145)."""

from __future__ import annotations

from backend.app.services.inbox.headline import HEADLINE_MAX_LEN, derive_headline


def test_derive_headline_prefers_explicit() -> None:
    assert (
        derive_headline(
            headline="Yellow: 3 blockers",
            summary="ignored\nbody",
            title="Daily digest",
        )
        == "Yellow: 3 blockers"
    )


def test_derive_headline_first_line_of_summary() -> None:
    assert (
        derive_headline(
            summary="Short line\nLong body",
            title="Daily digest",
        )
        == "Short line"
    )


def test_derive_headline_falls_back_to_title() -> None:
    assert derive_headline(summary=None, title="Daily digest — 2026-05-18") == (
        "Daily digest — 2026-05-18"
    )


def test_derive_headline_truncates_to_eighty() -> None:
    long = "x" * 120
    assert len(derive_headline(summary=long, title="t")) == HEADLINE_MAX_LEN
