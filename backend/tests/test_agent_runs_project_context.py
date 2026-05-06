"""Project-body section extraction (ELS-86).

The picker lifts ``Brief`` / ``WBS`` / ``Architecture`` /
``Test architecture`` / ``Tasks`` blocks out of a decomposed
project's body and stitches them onto the task response so the
SDLC agent sees the surrounding plan. These tests pin the
extraction shape: section ordering, content preservation, per-
section caps + overall cap, case-insensitive heading match, and
degradation paths.
"""

from __future__ import annotations

from backend.app.api.v1.routes.agent_runs import _extract_project_sections


def test_extracts_canonical_sections_in_order() -> None:
    body = """\
# Project Foo

Some intro text not in any section.

## Brief

The PO wants a thing that does X.

## WBS

- Task 1: Y
- Task 2: Z

## Architecture

Components: A, B, C.

## Test architecture

Unit tests.

## Tasks

- ELS-100 — Task 1
- ELS-101 — Task 2
"""
    out = _extract_project_sections(body)
    assert out is not None
    # Canonical order.
    assert out.index("## Brief") < out.index("## WBS")
    assert out.index("## WBS") < out.index("## Architecture")
    assert out.index("## Architecture") < out.index("## Test architecture")
    assert out.index("## Test architecture") < out.index("## Tasks")
    # Content preserved.
    assert "The PO wants a thing that does X." in out
    assert "Components: A, B, C." in out
    assert "ELS-100 — Task 1" in out


def test_extracts_only_the_canonical_sections() -> None:
    """Non-canonical headings are dropped; their bodies don't leak in."""
    body = """\
## Brief

Brief body.

## Random other heading

This should NOT appear in the excerpt.

## WBS

WBS body.

## Stretch goals

Also not in the excerpt.
"""
    out = _extract_project_sections(body)
    assert out is not None
    assert "Brief body." in out
    assert "WBS body." in out
    assert "Random other heading" not in out
    assert "Stretch goals" not in out
    assert "should NOT appear" not in out


def test_keeps_subsections_inside_a_canonical_block() -> None:
    """`### Subheadings` ride along with the parent canonical section."""
    body = """\
## Architecture

### Components

- Foo
- Bar

### Open questions

- What about caching?

## Tasks

- T1
"""
    out = _extract_project_sections(body)
    assert out is not None
    assert "### Components" in out
    assert "### Open questions" in out
    assert "What about caching?" in out


def test_returns_none_when_no_canonical_sections() -> None:
    body = """\
# Project Foo

## Random heading

Body.

## Another random heading

More body.
"""
    assert _extract_project_sections(body) is None


def test_returns_none_for_empty_input() -> None:
    assert _extract_project_sections("") is None
    assert _extract_project_sections("   \n\n   \n") is None


def test_overall_cap_bytes_truncates_with_marker() -> None:
    """A body where every section is huge gets clipped at the
    overall cap and the marker survives. Per-section caps catch most
    runaways first; this exercises the belt-and-suspenders outer cap.
    """
    huge = "lorem ipsum " * 200
    body = (
        f"## Brief\n\n{huge}\n\n## WBS\n\n{huge}\n\n## Architecture\n\n{huge}\n"
    )
    out = _extract_project_sections(body, overall_cap_bytes=512)
    assert out is not None
    assert len(out.encode("utf-8")) <= 512 + len("\n\n…(truncated)") + 8
    # Either the per-section truncation or the overall-cap truncation
    # planted the marker somewhere — the agent gets the signal that
    # context was clipped either way.
    assert "…(truncated)" in out


def test_per_section_cap_keeps_tasks_visible() -> None:
    """A bloated WBS must NOT starve the Tasks section — sibling
    awareness is the most useful section for a child-ticket agent."""
    huge_wbs = "\n".join([f"- WBS line {i:04d}: detail detail detail" for i in range(300)])
    body = (
        "## Brief\n\nShort brief.\n\n"
        f"## WBS\n\n{huge_wbs}\n\n"
        "## Tasks\n\n- ELS-100 child A\n- ELS-101 child B\n"
    )
    out = _extract_project_sections(body)
    assert out is not None
    # Tasks survive even though WBS got truncated.
    assert "ELS-100 child A" in out
    assert "ELS-101 child B" in out
    # WBS got the marker.
    wbs_block_start = out.index("## WBS")
    tasks_block_start = out.index("## Tasks")
    wbs_block = out[wbs_block_start:tasks_block_start]
    assert "…(truncated)" in wbs_block


def test_case_insensitive_heading_match() -> None:
    """A drifted ``## brief`` still finds the canonical Brief slot."""
    body = (
        "## brief\n\nlowercase heading.\n\n"
        "## WBS\n\nstandard.\n"
    )
    out = _extract_project_sections(body)
    assert out is not None
    # Canonical name is emitted regardless of input casing.
    assert "## Brief" in out
    assert "lowercase heading." in out
    assert "## WBS" in out


def test_undersized_body_is_returned_verbatim_no_marker() -> None:
    body = "## Brief\n\nShort.\n\n## WBS\n\n- one item\n"
    out = _extract_project_sections(body)
    assert out is not None
    assert "…(truncated)" not in out
    assert "Short." in out
    assert "one item" in out


def test_preserves_section_order_even_when_body_is_out_of_order() -> None:
    body = """\
## Tasks

- T1

## Brief

Brief body.

## WBS

WBS body.
"""
    out = _extract_project_sections(body)
    assert out is not None
    assert out.index("## Brief") < out.index("## WBS")
    assert out.index("## WBS") < out.index("## Tasks")


def test_drops_empty_sections() -> None:
    """A heading with no body underneath shouldn't leave a blank block."""
    body = """\
## Brief



## WBS

Real WBS body.
"""
    out = _extract_project_sections(body)
    assert out is not None
    assert "## Brief" not in out  # empty Brief was dropped
    assert "Real WBS body." in out
