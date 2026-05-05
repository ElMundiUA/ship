"""Pure-unit coverage for the ``knowledge_canon`` route adapters.

Endpoint-level integration is exercised by
``tests/test_v1_knowledge_canon.py`` (DB-bound, runs in CI). This
file just locks the wire shape of ``ClaimSummary`` so a refactor to
the model doesn't silently break consumers — the agent and CLI
unmarshal these dicts directly.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from backend.app.api.v1.routes.knowledge_canon import _claim_to_summary


@dataclass
class _FakeClaim:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    workspace_id: uuid.UUID = field(default_factory=uuid.uuid4)
    claim_md: str = "X is Y."
    kind: str = "fact"
    status: str = "active"
    topic_tags: list = field(default_factory=lambda: ["t1", "t2"])
    confidence: float = 1.0
    source_links: list = field(default_factory=list)
    supersedes_id: uuid.UUID | None = None
    superseded_by_id: uuid.UUID | None = None
    first_seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def test_claim_to_summary_emits_typed_source_links():
    """Raw JSONB shape converts cleanly into ``ClaimSourceLink`` rows
    on the wire — agents and the CLI can rely on the object shape
    rather than dict-key probing."""
    claim = _FakeClaim(
        source_links=[
            {
                "source_item_id": "doc-1",
                "external_url": "https://example/doc",
                "title": "Doc title",
                "excerpt": "An excerpt …",
                "extracted_at": "2026-05-06T00:00:00Z",
            },
            {"source_item_id": "doc-2"},  # partial entries still typed
            "garbage",  # non-dict skipped silently
        ]
    )
    summary = _claim_to_summary(claim)
    assert len(summary.source_links) == 2
    first = summary.source_links[0]
    assert first.source_item_id == "doc-1"
    assert first.external_url == "https://example/doc"
    assert first.title == "Doc title"
    second = summary.source_links[1]
    assert second.source_item_id == "doc-2"
    assert second.external_url is None


def test_claim_to_summary_handles_missing_source_links():
    summary = _claim_to_summary(_FakeClaim(source_links=None))
    assert summary.source_links == []


def test_claim_to_summary_preserves_topic_tags_order():
    summary = _claim_to_summary(_FakeClaim(topic_tags=["b", "a", "c"]))
    assert summary.topic_tags == ["b", "a", "c"]
