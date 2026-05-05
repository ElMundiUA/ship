"""Pure-unit coverage for the canon-mixing additions to
``knowledge_search``.

The full v1 endpoints are exercised by
``tests/test_v1_knowledge_canon.py`` (DB-bound, runs in CI). Here we
keep the targeted adapters (``_topic_view_hit``, ``_claim_hit``)
under cheap regression coverage so any refactor to the wire shape
of ``KnowledgeSearchHit`` doesn't silently break the new sources.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from backend.app.services.knowledge_search import (
    _claim_hit,
    _topic_view_hit,
)


@dataclass
class _FakeView:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    topic_tag: str = "linear-fsm"
    title: str = "Linear FSM"
    body_md: str = "# Linear FSM\n\nThe FSM has 7 canonical states.\n"
    claim_count: int = 5


@dataclass
class _FakeClaim:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    claim_md: str = "Linear FSM uses 7 states."
    kind: str = "fact"
    status: str = "active"
    topic_tags: list = field(default_factory=lambda: ["linear-fsm", "integrations"])
    source_links: list = field(
        default_factory=lambda: [
            {
                "source_item_id": "doc-1",
                "external_url": "https://example/doc",
                "title": "Linear runbook",
                "excerpt": "FSM has 7 states defined under …",
                "extracted_at": "2026-05-06T00:00:00Z",
            }
        ]
    )


def test_topic_view_hit_carries_canon_rank_and_first_paragraph():
    view = _FakeView()
    hit = _topic_view_hit(view, score=0.91)

    assert hit.source == "topic_view"
    assert hit.rank_bucket == "canon"
    assert hit.scope_kind == "topic_view"
    assert hit.bucket_slug == view.topic_tag
    assert hit.title == "Linear FSM"
    # First non-empty paragraph after the H1 — `_first_paragraph` keeps
    # the H1 line because it's the first chunk separated by blank lines.
    assert "# Linear FSM" in hit.snippet
    assert hit.score == 0.91


def test_claim_hit_uses_excerpt_when_available_and_clamps_title():
    claim = _FakeClaim(
        claim_md=(
            "A very long claim that describes some specific behaviour at "
            "great length and would clobber the search list if rendered "
            "as a title without truncation."
        )
    )
    hit = _claim_hit(claim, score=0.83)

    assert hit.source == "claim"
    assert hit.rank_bucket == "canon"
    assert hit.scope_kind == "claim"
    # Title clamps at 120 chars with ellipsis.
    assert hit.title.endswith("…")
    assert len(hit.title) <= 120
    # Snippet picks the source_link excerpt over the claim text.
    assert hit.snippet.startswith("FSM has 7 states defined under")
    # First topic_tag is exposed in bucket_slug for grouping in the UI.
    assert hit.bucket_slug == "linear-fsm"


def test_claim_hit_falls_back_to_claim_text_when_no_excerpt():
    claim = _FakeClaim(source_links=[])
    hit = _claim_hit(claim, score=0.5)
    assert hit.snippet == claim.claim_md


def test_claim_hit_handles_missing_topic_tags():
    claim = _FakeClaim(topic_tags=[])
    hit = _claim_hit(claim, score=0.7)
    assert hit.bucket_slug is None
