"""Section-aware project body writes (project-first delivery, ELS-75).

The decomposition pipeline pins one chunk of the project body to one
specialist: BA owns ``## WBS``, Tech-architect owns ``## Architecture``,
QA-architect owns ``## Test architecture``, the Tasks stage owns
``## Tasks``. Re-running a stage replaces just its section; sections
owned by other stages must stay verbatim. These tests exercise the
upsert helper without going through Linear — the matching/replacement
logic is the load-bearing bit and is the same for any tracker that
keeps a markdown blob per project.
"""

from __future__ import annotations

from typing import Any

import pytest


class _RecordingTracker:
    """Linear adapter look-alike scoped to project-body operations.

    Captures the body across mutations so tests can read the final
    state without setting up a real tracker. Used to drive
    ``upsert_project_section``'s logic (which lives on
    ``LinearTracker``) — we instantiate the adapter under a fake gql
    transport so the upsert reads/writes through the recording layer.
    """

    def __init__(self, initial: str = "") -> None:
        self._content = initial
        self.calls: list[dict[str, Any]] = []

    async def gql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        # Two queries are issued by ``upsert_project_section``: a read
        # via ``get_project`` (which executes a ``project(id:...)``
        # query) and a write via ``projectUpdate``. Distinguish by the
        # query string.
        if "projectUpdate" in query:
            self.calls.append({"kind": "update", "vars": variables})
            self._content = variables["content"]
            return {"projectUpdate": {"success": True}}
        # get_project read.
        self.calls.append({"kind": "read", "vars": variables})
        return {
            "project": {
                "id": variables["id"],
                "name": "Test",
                "slugId": "test",
                "state": "started",
                "url": "https://linear.app/test",
                "color": "",
                "description": "",
                "content": self._content,
                "lead": None,
            }
        }

    @property
    def content(self) -> str:
        return self._content


def _adapter(tracker: _RecordingTracker):
    """Spin up a LinearTracker with the recording transport patched in."""
    from backend.app.integrations.linear.tracker_adapter import LinearTracker

    adapter = LinearTracker("test-token")
    adapter._gql = tracker.gql  # type: ignore[method-assign]
    return adapter


@pytest.mark.asyncio
async def test_upsert_appends_new_section_to_empty_body() -> None:
    tracker = _RecordingTracker(initial="")
    adapter = _adapter(tracker)

    await adapter.upsert_project_section(
        "proj-a", section="WBS", body="- export endpoint\n- pagination"
    )

    assert tracker.content.startswith("## WBS")
    assert "- export endpoint" in tracker.content
    assert "- pagination" in tracker.content


@pytest.mark.asyncio
async def test_upsert_appends_below_existing_unrelated_body() -> None:
    """A pre-existing brief stays at the top; the new section lands
    below separated by a blank line."""
    initial = "## Brief\n\nBuild PDF export.\n"
    tracker = _RecordingTracker(initial=initial)
    adapter = _adapter(tracker)

    await adapter.upsert_project_section(
        "proj-a", section="WBS", body="- export endpoint"
    )

    body = tracker.content
    assert body.startswith("## Brief")
    assert "Build PDF export." in body
    # New section lands AFTER the brief, with at least one blank line
    # between them.
    brief_idx = body.index("## Brief")
    wbs_idx = body.index("## WBS")
    assert wbs_idx > brief_idx
    assert "- export endpoint" in body


@pytest.mark.asyncio
async def test_upsert_replaces_existing_section_in_place() -> None:
    """Re-running a stage replaces just its own section; sections
    owned by other stages stay verbatim."""
    initial = (
        "## Brief\n\n"
        "Build PDF export.\n"
        "\n"
        "## WBS\n\n"
        "- old line 1\n"
        "- old line 2\n"
        "\n"
        "## Architecture\n\n"
        "Use puppeteer.\n"
    )
    tracker = _RecordingTracker(initial=initial)
    adapter = _adapter(tracker)

    await adapter.upsert_project_section(
        "proj-a",
        section="WBS",
        body="- new line A\n- new line B\n- new line C",
    )

    body = tracker.content
    # Brief untouched.
    assert "Build PDF export." in body
    # Architecture untouched.
    assert "Use puppeteer." in body
    # WBS replaced — old lines gone, new lines present.
    assert "old line 1" not in body
    assert "old line 2" not in body
    assert "new line A" in body
    assert "new line B" in body
    assert "new line C" in body
    # Section ordering preserved (Brief → WBS → Architecture).
    assert body.index("## Brief") < body.index("## WBS") < body.index("## Architecture")


@pytest.mark.asyncio
async def test_upsert_preserves_subheadings_inside_other_sections() -> None:
    """``###`` headings inside a section don't terminate the match —
    only ``## `` (level-2) headings do. Otherwise an architecture
    section with subheadings would look like multiple sections to the
    parser."""
    initial = (
        "## WBS\n\n"
        "- a\n"
        "\n"
        "## Architecture\n\n"
        "### Components\n"
        "- A\n"
        "\n"
        "### Risks\n"
        "- R1\n"
    )
    tracker = _RecordingTracker(initial=initial)
    adapter = _adapter(tracker)

    await adapter.upsert_project_section(
        "proj-a", section="WBS", body="- replaced"
    )

    body = tracker.content
    assert "- replaced" in body
    # Architecture's subheadings survive.
    assert "### Components" in body
    assert "### Risks" in body
    assert "- A" in body
    assert "- R1" in body


@pytest.mark.asyncio
async def test_upsert_idempotent_under_same_body() -> None:
    """Re-running a stage with identical content lands the same body —
    no stacking, no drift. The ``mutation`` runs (we don't dedupe at
    the network layer), but the resulting content is byte-stable."""
    tracker = _RecordingTracker(initial="")
    adapter = _adapter(tracker)

    await adapter.upsert_project_section(
        "proj-a", section="WBS", body="- one\n- two"
    )
    after_first = tracker.content
    await adapter.upsert_project_section(
        "proj-a", section="WBS", body="- one\n- two"
    )
    after_second = tracker.content
    assert after_first == after_second
