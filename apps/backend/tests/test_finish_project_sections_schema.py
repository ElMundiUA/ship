"""Schema for the decomposition write path on ``/agent-runs/finish``.

The decomposition agents (BA, tech-architect, qa-architect, qa-engineer,
developer) emit their stage's section of the project body via
``FinishIn.project_sections``. The server then routes each entry into
``LinearTracker.upsert_project_section``. The unit tests here exercise
just the Pydantic shape — the handler-side wiring is exercised
end-to-end in the agent run integration tests / live smoke (this file
exists to fail loudly on accidental field rename or validation drop).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.api.v1.routes.agent_runs import (
    ChildTicketCreate,
    FinishIn,
    ProjectSectionPatch,
    _collect_project_sections,
)


def test_project_sections_default_is_empty_list() -> None:
    """SDLC runs (no decomposition) finish without sections — must default to [].

    Concretely: an SDLC ``code_review`` run never sets
    ``project_sections`` and the handler must still accept the
    payload. A required-field default mistake here would make every
    SDLC tick 422.
    """
    finish = FinishIn(
        run_id="r1",
        outcome="ready_next_step",
        ticket_ref="ENG-1",
        stage_next="qa_manual",
    )
    assert finish.project_sections == []


def test_project_sections_accepts_dicts() -> None:
    """Agents post JSON; FastAPI coerces dicts → ProjectSectionPatch.

    Pin the coercion shape so a future Pydantic upgrade or BaseModel
    rewrite can't silently drop list-of-dict ingestion.
    """
    finish = FinishIn(
        run_id="r1",
        outcome="ready_next_step",
        ticket_ref="ENG-1",
        stage_next="architecture",
        process="decomposition",
        project_sections=[
            {"section": "WBS", "body": "- thing one\n- thing two"},
        ],
    )
    assert len(finish.project_sections) == 1
    assert finish.project_sections[0].section == "WBS"
    assert finish.project_sections[0].body.startswith("- thing one")


def test_project_sections_accepts_multiple_entries() -> None:
    """A single role only owns one section, but the schema permits a
    list because the developer stage may patch ``Tasks`` while a
    legacy run-replay re-emits an upstream section in the same call —
    and forcing exactly-one would force agents to chain 2 finish
    calls. We let the role prompt enforce single-section intent.
    """
    finish = FinishIn(
        run_id="r1",
        outcome="ready_next_step",
        ticket_ref="ENG-1",
        stage_next="planning_done",
        process="decomposition",
        project_sections=[
            {"section": "Tasks", "body": "- ENG-101 thing\n- ENG-102 thing"},
            {"section": "WBS", "body": "(unchanged)"},
        ],
    )
    assert [p.section for p in finish.project_sections] == ["Tasks", "WBS"]


def test_section_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        ProjectSectionPatch(section="", body="…")


def test_section_capped_at_64_chars() -> None:
    """Linear's section heading goes into a markdown line; an
    operator who pastes a 4kB heading by accident shouldn't blow up
    the project body parser."""
    with pytest.raises(ValidationError):
        ProjectSectionPatch(section="x" * 65, body="…")


def test_body_capped_at_50k() -> None:
    """Project bodies are bounded; reject before the Linear API
    rejects at its own (lower) limit so the audit row carries the
    intent rather than an opaque 4xx."""
    with pytest.raises(ValidationError):
        ProjectSectionPatch(section="WBS", body="x" * 50_001)


def test_body_zero_length_is_allowed() -> None:
    """An agent may need to *clear* a section it previously owned —
    e.g. re-running a stage that decided the prior content was wrong.
    Empty body is the explicit "wipe my section" signal; the
    upsert_project_section adapter handles the empty-block case.
    """
    patch = ProjectSectionPatch(section="WBS", body="")
    assert patch.body == ""


# ---- _collect_project_sections fallback ----


def _bare_finish(**overrides) -> FinishIn:
    base = dict(
        run_id="r1",
        outcome="ready_next_step",
        ticket_ref="PAC-9",
        stage_next="architecture",
        process="decomposition",
    )
    base.update(overrides)
    return FinishIn(**base)


def test_collect_prefers_top_level() -> None:
    """When both surfaces carry a list, the top-level wins — the
    Pydantic-validated form is the canonical shape."""
    finish = _bare_finish(
        project_sections=[{"section": "WBS", "body": "top"}],
        payload={"project_sections": [{"section": "WBS", "body": "nested"}]},
    )
    out = _collect_project_sections(finish)
    assert len(out) == 1
    assert out[0].body == "top"


def test_collect_falls_back_to_payload_dict() -> None:
    """Earlier role prompts said "on the finish payload" — Claude
    sometimes interpreted that as nesting under the catch-all
    ``payload`` dict. Server reads either surface."""
    finish = _bare_finish(
        payload={
            "project_sections": [
                {"section": "WBS", "body": "- thing one\n- thing two"}
            ]
        },
    )
    out = _collect_project_sections(finish)
    assert len(out) == 1
    assert out[0].section == "WBS"
    assert out[0].body.startswith("- thing one")


def test_collect_empty_when_neither_present() -> None:
    finish = _bare_finish()
    assert _collect_project_sections(finish) == []


def test_collect_empty_when_payload_dict_has_wrong_shape() -> None:
    """Garbage under ``payload.project_sections`` (string, dict,
    None) drops silently — agents misencoding the field shouldn't
    500 the finish handler."""
    for raw in ("string", {"section": "WBS"}, 42, None):
        finish = _bare_finish(payload={"project_sections": raw})
        assert _collect_project_sections(finish) == []


def test_collect_drops_malformed_entries_in_fallback() -> None:
    """Mixed list under fallback: keep valid, drop malformed.

    A length-overflow body or empty section name on one entry must
    not sink the rest — the audit row will simply miss the
    ``tracker:project_section:<X>`` action for the dropped one.
    """
    finish = _bare_finish(
        payload={
            "project_sections": [
                {"section": "WBS", "body": "ok"},
                {"section": "", "body": "bad"},  # min_length=1 fails
                "not a dict",
                {"section": "Tasks", "body": "also ok"},
            ]
        },
    )
    out = _collect_project_sections(finish)
    assert [p.section for p in out] == ["WBS", "Tasks"]


# ---- ChildTicketCreate / FinishIn.child_tickets ----


def test_child_tickets_default_is_empty_list() -> None:
    """Most roles never set child_tickets — required-field default
    mistake here would 422 every SDLC tick."""
    finish = FinishIn(
        run_id="r1",
        outcome="ready_next_step",
        ticket_ref="ENG-1",
        stage_next="qa_manual",
    )
    assert finish.child_tickets == []


def test_child_tickets_accepts_dicts() -> None:
    finish = FinishIn(
        run_id="r1",
        outcome="ready_next_step",
        ticket_ref="PAC-9",
        stage_next="planning_done",
        process="decomposition",
        child_tickets=[
            {"title": "Slice one", "body": "Goal\nScope\nArch ref"},
            {"title": "Slice two", "body": "etc"},
        ],
    )
    assert len(finish.child_tickets) == 2
    assert finish.child_tickets[0].title == "Slice one"


def test_child_ticket_title_required() -> None:
    with pytest.raises(ValidationError):
        ChildTicketCreate(title="", body="b")


def test_child_ticket_body_required() -> None:
    with pytest.raises(ValidationError):
        ChildTicketCreate(title="t", body="")


def test_child_ticket_priority_bounded() -> None:
    """Linear priorities are 0-4 (Urgent..No priority); reject anything
    outside the band so an off-by-one in the agent doesn't reach the
    tracker API."""
    ChildTicketCreate(title="t", body="b", priority=0)
    ChildTicketCreate(title="t", body="b", priority=4)
    with pytest.raises(ValidationError):
        ChildTicketCreate(title="t", body="b", priority=-1)
    with pytest.raises(ValidationError):
        ChildTicketCreate(title="t", body="b", priority=5)
