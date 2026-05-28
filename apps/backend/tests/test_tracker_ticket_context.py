"""Unit tests for tracker comment serialization helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.app.integrations.gateway.tracker import CommentRef
from backend.app.services.tracker_ticket_context import (
    format_comments_markdown,
    serialize_ticket_comments,
)


def _comment(body: str, *, minutes: int) -> CommentRef:
    return CommentRef(
        id=f"c-{minutes}",
        body=body,
        author="Agent",
        created_at=datetime(2026, 5, 28, 12, minutes, tzinfo=timezone.utc),
    )


def test_serialize_comments_chronological_and_cap() -> None:
    comments = [_comment(f"line-{i}", minutes=i) for i in range(25)]
    rows, truncated = serialize_ticket_comments(comments, max_count=20)
    assert truncated is True
    assert len(rows) == 20
    assert rows[0]["body"] == "line-5"
    assert rows[-1]["body"] == "line-24"


def test_format_comments_markdown_includes_agent_question() -> None:
    comments = [
        _comment("Need a call on scope.\n\n[Ship SDLC:role-developer]", minutes=0)
    ]
    block = format_comments_markdown(comments)
    assert block is not None
    assert "### Recent Linear comments" in block
    assert "[Ship SDLC:role-developer]" in block
