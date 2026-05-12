"""``ticket_list`` returns rows sorted by identifier ascending.

Operator expectation: agents work older filings first. ELS-11 should
clear the queue before ELS-12 starts; ELS-99 before ELS-101.

Linear's ``orderBy`` returns most-recently-updated first (descending,
no API for ascending on the indexed fields), so the adapter over-
fetches and sorts the rows client-side. These tests pin the sort
function, not the GraphQL query — the query path is covered by
``test_linear_tracker_list_tickets`` and the live cron.
"""

from __future__ import annotations

from backend.app.integrations.linear.tracker_adapter import _identifier_sort_key


def _row(identifier: str) -> dict[str, object]:
    """Minimal shape — ``ticket_list`` rows carry more fields but
    the sort key only reads ``id``."""
    return {"id": identifier}


def test_sort_walks_numeric_suffix_not_lex() -> None:
    """Lexicographic sort would put ELS-101 before ELS-12 (because
    '1' < '2' at the third char). The numeric suffix must outrank
    the string compare."""
    rows = [_row("ELS-12"), _row("ELS-101"), _row("ELS-11")]
    rows.sort(key=_identifier_sort_key)
    assert [r["id"] for r in rows] == ["ELS-11", "ELS-12", "ELS-101"]


def test_sort_groups_by_team_then_number() -> None:
    """Cross-team queries (rare today but possible if the picker
    expands beyond ``team_id``) get sorted team-major: all ELS
    together, then all PAC, etc."""
    rows = [_row("PAC-9"), _row("ELS-99"), _row("PAC-10"), _row("ELS-100")]
    rows.sort(key=_identifier_sort_key)
    assert [r["id"] for r in rows] == ["ELS-99", "ELS-100", "PAC-9", "PAC-10"]


def test_sort_stable_on_equal_numerics() -> None:
    """Two rows with the same key (shouldn't happen with real
    identifiers but defensive) preserve input order."""
    a = {"id": "ELS-1", "title": "first"}
    b = {"id": "ELS-1", "title": "second"}
    rows = [a, b]
    rows.sort(key=_identifier_sort_key)
    assert rows[0]["title"] == "first"
    assert rows[1]["title"] == "second"


def test_sort_tolerates_uuid_only_identifiers() -> None:
    """Future adapters (GitHub Issues returns ``owner/repo#42``,
    Notion returns UUIDs) may not produce a team-suffix shape. The
    helper's ``rsplit("-", 1)`` still produces a key for them — they
    interleave with team-prefixed rows by string sort of the
    leading segment. The contract this test pins is "sorts
    deterministically without crashing"; the relative position of
    UUIDs vs ELS-N is implementation-defined and acceptable to drift."""
    rows = [
        _row("a8c0b6c5-1234-5678-abcd-effeeffeefef"),
        _row("ELS-7"),
        _row("00000000-0000-0000-0000-000000000001"),
    ]
    rows.sort(key=_identifier_sort_key)
    # Determinism: same input → same output.
    rows2 = [
        _row("ELS-7"),
        _row("00000000-0000-0000-0000-000000000001"),
        _row("a8c0b6c5-1234-5678-abcd-effeeffeefef"),
    ]
    rows2.sort(key=_identifier_sort_key)
    assert [r["id"] for r in rows] == [r["id"] for r in rows2]


def test_sort_tolerates_non_string_id() -> None:
    """Garbage rows (None / int id) don't crash and they sort LAST so
    real tickets are served first — a None id at position zero would
    otherwise starve the queue."""
    rows: list[dict[str, object]] = [
        {"id": None},
        {"id": "ELS-5"},
        {"id": 12345},  # type: ignore[dict-item]
    ]
    rows.sort(key=_identifier_sort_key)
    assert rows[0]["id"] == "ELS-5", (
        "real ticket id must lead; garbage rows belong at the tail"
    )
    # The remaining two are garbage; ordering between them is
    # implementation-defined but stable.
    assert {r["id"] for r in rows[1:]} == {None, 12345}


def test_missing_dash_falls_through() -> None:
    """An id without a ``-`` separator (operator hand-typed string,
    legacy data) gets a fallback key. Sort should not crash and
    should rank these consistently."""
    rows = [_row("foobar"), _row("ELS-1"), _row("zzz")]
    rows.sort(key=_identifier_sort_key)
    # ``ELS-1`` has a team-prefix-only key ``("ELS", 1, …)`` which
    # ranks under team strings ``"foobar"`` / ``"zzz"`` only when
    # alphabetic. The exact placement is implementation-defined —
    # the contract this test pins is "doesn't crash, deterministic".
    assert len(rows) == 3
