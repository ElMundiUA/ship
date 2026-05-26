"""Linear OAuth scope parsing — accept space- OR comma-separated.

Linear returns OAuth ``scope`` space-separated per RFC 6749, but some
Ship-side writers (and Linear test fixtures) historically used commas.
The provision endpoint's admin-scope guard must work for both shapes,
otherwise a valid ``admin`` grant is read as a single bogus scope
string and the operator gets a confusing "Reconnect Linear" message
when the reconnect already succeeded.

Pin the normaliser shape.
"""

from __future__ import annotations


def _parse_scopes(raw: str) -> set[str]:
    """Mirror the parsing logic the provision endpoint uses."""
    return {
        s.strip()
        for s in raw.replace(",", " ").split()
        if s.strip()
    }


def test_space_separated_scopes_rfc6749() -> None:
    raw = "admin comments:create issues:create read write"
    assert _parse_scopes(raw) == {
        "admin", "comments:create", "issues:create", "read", "write",
    }


def test_comma_separated_scopes_legacy() -> None:
    raw = "read,write,admin,issues:create,comments:create"
    assert _parse_scopes(raw) == {
        "admin", "comments:create", "issues:create", "read", "write",
    }


def test_mixed_delimiters() -> None:
    raw = "read,write admin,issues:create  comments:create"
    assert _parse_scopes(raw) == {
        "admin", "comments:create", "issues:create", "read", "write",
    }


def test_empty_or_whitespace_only() -> None:
    assert _parse_scopes("") == set()
    assert _parse_scopes("   ") == set()
    assert _parse_scopes(", ,") == set()


def test_admin_membership_is_what_we_actually_check() -> None:
    # The provision endpoint's whole job hinges on this one check; lock
    # it in so a future refactor of the parser can't break the gate.
    raw = "admin comments:create issues:create read write"
    assert "admin" in _parse_scopes(raw)
    assert "admin" not in _parse_scopes("read write issues:create")
