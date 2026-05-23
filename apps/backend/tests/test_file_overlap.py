"""Unit tests for file-coordination warnings (ELS-154)."""

from __future__ import annotations

from backend.app.services.file_overlap import (
    FileOverlapResult,
    _SiblingPr,
    _classify_overlaps,
    _is_lockfile_path,
    _is_schema_path,
    _render_warning_markdown,
)
from backend.app.services.ticket_ref import parse_ticket_refs_from_pr_title


def test_parse_ticket_refs_primary_first() -> None:
    refs = parse_ticket_refs_from_pr_title("feat(ELS-144): fix ELS-143 follow-up")
    assert refs == ["ELS-144", "ELS-143"]


def test_parse_ticket_refs_empty_when_unmapped() -> None:
    assert parse_ticket_refs_from_pr_title("fix stuff") == []


def test_schema_path_detection() -> None:
    p = "apps/backend/migrations/versions/0074_foo.py"
    assert _is_schema_path(p)
    assert not _is_schema_path("apps/backend/app/main.py")


def test_lockfile_excluded_from_hard_overlap() -> None:
    assert _is_lockfile_path("package-lock.json")
    siblings = [
        _SiblingPr(
            ticket_ref="ELS-1",
            pr_number=1,
            repo_full_name="org/ship",
            pr_html_url="https://github.com/org/ship/pull/1",
            paths=["package-lock.json"],
            extra_ticket_refs=[],
        ),
        _SiblingPr(
            ticket_ref="ELS-2",
            pr_number=2,
            repo_full_name="org/ship",
            pr_html_url="https://github.com/org/ship/pull/2",
            paths=["package-lock.json"],
            extra_ticket_refs=[],
        ),
    ]
    structured, hard = _classify_overlaps(siblings)
    assert hard == []
    assert structured == []


def test_schema_overlap_warning() -> None:
    mig = "apps/backend/migrations/versions/0074_a.py"
    siblings = [
        _SiblingPr(
            ticket_ref="ELS-144",
            pr_number=276,
            repo_full_name="org/ship",
            pr_html_url="https://github.com/org/ship/pull/276",
            paths=[mig],
            extra_ticket_refs=[],
        ),
    ]
    structured, hard = _classify_overlaps(siblings)
    assert hard == []
    assert len(structured) == 1
    assert structured[0]["overlap_kind"] == "schema"
    md = _render_warning_markdown(siblings, structured=structured, hard_paths=hard)
    assert "ELS-144" in md
    assert "276" in md
    assert "migrations/versions" in md


def test_hard_overlap_two_siblings_same_path() -> None:
    shared = "apps/console/src/inbox-types.ts"
    siblings = [
        _SiblingPr(
            ticket_ref="ELS-143",
            pr_number=10,
            repo_full_name="org/ship",
            pr_html_url="https://github.com/org/ship/pull/10",
            paths=[shared],
            extra_ticket_refs=[],
        ),
        _SiblingPr(
            ticket_ref="ELS-144",
            pr_number=11,
            repo_full_name="org/ship",
            pr_html_url="https://github.com/org/ship/pull/11",
            paths=[shared],
            extra_ticket_refs=[],
        ),
    ]
    structured, hard = _classify_overlaps(siblings)
    assert shared in hard
    assert any(w.get("overlap_kind") == "hard" for w in structured)
    md = _render_warning_markdown(siblings, structured=structured, hard_paths=hard)
    assert "Hard path overlap" in md
    assert shared in md


def test_file_overlap_result_empty() -> None:
    r = FileOverlapResult(warning_markdown=None, file_overlap_warnings=[])
    assert r.warning_markdown is None


def test_dev_file_overlap_workspace_flag_default_off() -> None:
    from backend.app.services.file_overlap import dev_file_overlap_warnings_enabled

    assert dev_file_overlap_warnings_enabled({}) is False
    assert dev_file_overlap_warnings_enabled(None) is False


def test_dev_file_overlap_workspace_flag_on() -> None:
    from backend.app.core.config import Settings
    from backend.app.services.file_overlap import file_overlap_warnings_active

    settings = Settings()
    assert file_overlap_warnings_active(
        settings, {"dev_file_overlap_warnings_enabled": True}
    )
    assert not file_overlap_warnings_active(settings, {})
