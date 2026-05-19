"""ELS-168 — unit tests for the proposal validator.

The Anthropic call itself isn't exercised here (would hit prod
credentials). We test:

- valid proposals round-trip
- bad epic.key shapes raise
- depends_on referencing unknown keys raises
- self-depend raises
- cycles raise
- proposal size limits (empty, oversized) raise
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.services.planning.requirements_extraction import (
    EpicProposal,
    MassPlanProposal,
    ProjectProposal,
)


def _ok_proposal() -> MassPlanProposal:
    return MassPlanProposal(
        project=ProjectProposal(name="Demo", description="Demo desc"),
        epics=[
            EpicProposal(
                key="e1-bootstrap",
                title="Bootstrap",
                brief="Lay the foundation.",
                depends_on=[],
            ),
            EpicProposal(
                key="e2-core",
                title="Core",
                brief="Build the core.",
                depends_on=["e1-bootstrap"],
            ),
            EpicProposal(
                key="e3-ui",
                title="UI",
                brief="Ship the UI.",
                depends_on=["e2-core"],
            ),
        ],
    )


def test_valid_proposal_round_trips() -> None:
    p = _ok_proposal()
    assert len(p.epics) == 3
    assert p.epics[1].depends_on == ["e1-bootstrap"]


def test_epic_key_must_be_lowercase() -> None:
    with pytest.raises(ValidationError):
        EpicProposal(
            key="E1-bootstrap",
            title="t",
            brief="b",
        )


def test_epic_key_must_have_no_whitespace() -> None:
    with pytest.raises(ValidationError):
        EpicProposal(
            key="e1 bootstrap",
            title="t",
            brief="b",
        )


def test_depends_on_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError, match="references unknown key"):
        MassPlanProposal(
            project=ProjectProposal(name="P", description="d"),
            epics=[
                EpicProposal(
                    key="e1-only",
                    title="t",
                    brief="b",
                    depends_on=["e9-ghost"],
                ),
            ],
        )


def test_self_depend_rejected() -> None:
    with pytest.raises(ValidationError, match="cannot depend on itself"):
        MassPlanProposal(
            project=ProjectProposal(name="P", description="d"),
            epics=[
                EpicProposal(
                    key="e1-self",
                    title="t",
                    brief="b",
                    depends_on=["e1-self"],
                ),
            ],
        )


def test_cycle_rejected() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        MassPlanProposal(
            project=ProjectProposal(name="P", description="d"),
            epics=[
                EpicProposal(
                    key="e1-a", title="A", brief="b", depends_on=["e2-b"]
                ),
                EpicProposal(
                    key="e2-b", title="B", brief="b", depends_on=["e1-a"]
                ),
            ],
        )


def test_empty_epics_rejected() -> None:
    with pytest.raises(ValidationError):
        MassPlanProposal(
            project=ProjectProposal(name="P", description="d"),
            epics=[],
        )


def test_max_epics_24() -> None:
    too_many = [
        EpicProposal(key=f"e{i}-x", title="t", brief="b")
        for i in range(25)
    ]
    with pytest.raises(ValidationError):
        MassPlanProposal(
            project=ProjectProposal(name="P", description="d"),
            epics=too_many,
        )


def test_chain_topo_passes() -> None:
    """Linear chain a→b→c→d → no cycle, all four keys reachable."""
    proposal = MassPlanProposal(
        project=ProjectProposal(name="P", description="d"),
        epics=[
            EpicProposal(key="e1-a", title="A", brief="b", depends_on=[]),
            EpicProposal(
                key="e2-b", title="B", brief="b", depends_on=["e1-a"]
            ),
            EpicProposal(
                key="e3-c", title="C", brief="b", depends_on=["e2-b"]
            ),
            EpicProposal(
                key="e4-d", title="D", brief="b", depends_on=["e3-c"]
            ),
        ],
    )
    assert len(proposal.epics) == 4


def test_diamond_topo_passes() -> None:
    """Diamond: a→b, a→c, b→d, c→d. No cycle, valid."""
    proposal = MassPlanProposal(
        project=ProjectProposal(name="P", description="d"),
        epics=[
            EpicProposal(key="e1-a", title="A", brief="b"),
            EpicProposal(
                key="e2-b", title="B", brief="b", depends_on=["e1-a"]
            ),
            EpicProposal(
                key="e3-c", title="C", brief="b", depends_on=["e1-a"]
            ),
            EpicProposal(
                key="e4-d",
                title="D",
                brief="b",
                depends_on=["e2-b", "e3-c"],
            ),
        ],
    )
    assert len(proposal.epics) == 4


@pytest.mark.asyncio
async def test_extract_rejects_empty_bytes() -> None:
    from backend.app.services.planning.requirements_extraction import (
        extract_proposal_from_pdf,
    )

    with pytest.raises(ValueError, match="empty"):
        await extract_proposal_from_pdf(b"", api_key="dummy")


@pytest.mark.asyncio
async def test_extract_rejects_oversized_pdf() -> None:
    from backend.app.services.planning.requirements_extraction import (
        extract_proposal_from_pdf,
    )

    big = b"x" * (33 * 1024 * 1024)
    with pytest.raises(ValueError, match="32MB"):
        await extract_proposal_from_pdf(big, api_key="dummy")
