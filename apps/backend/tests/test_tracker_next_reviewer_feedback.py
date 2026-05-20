"""dev_not_converging fix — reviewer feedback reaches the dev re-run.

When a ticket cascades ``code_review (blocked) → dev_implementation``,
``GET /tracker/next`` stitches the latest non-dev SDLC verdict (plus any
operator hints posted after it) onto the dev's task body. Before this,
the dev only saw the original ticket body and never the reviewer's
finding, so it re-implemented the same brief and looped forever.

These cover the pure helper ``_fetch_reviewer_feedback_section`` —
selection, dev-comment exclusion, operator-hint append, and graceful
degradation — without standing up a tracker.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.app.api.v1.routes.agent_runs import (
    _fetch_reviewer_feedback_section,
)
from backend.app.integrations.gateway.tracker import CommentRef


def _c(body: str, *, minutes_ago: int) -> CommentRef:
    return CommentRef(
        id=f"c{minutes_ago}",
        body=body,
        author="agent",
        created_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
        url=None,
    )


def _resolved(comments: list[CommentRef] | Exception):
    async def _list_comments(_ref):
        if isinstance(comments, Exception):
            raise comments
        return list(comments)

    return SimpleNamespace(
        kind="linear",
        gateway=SimpleNamespace(list_comments=_list_comments),
    )


@pytest.mark.asyncio
async def test_returns_latest_reviewer_verdict() -> None:
    resolved = _resolved(
        [
            _c("Reviewer found blockers — fix the null guard.\n\n[Ship SDLC:role-reviewer]", minutes_ago=30),
            _c("Done, added the guard. [Ship SDLC:role-developer]", minutes_ago=20),
            _c("Still blocked: .gitignore line 59 wrong.\n\n[Ship SDLC:role-reviewer]", minutes_ago=10),
        ]
    )
    out = await _fetch_reviewer_feedback_section(resolved=resolved, ticket_ref="ELS-1")
    assert out is not None
    assert "Reviewer feedback to address" in out
    # latest reviewer verdict wins, not the older one
    assert ".gitignore line 59 wrong" in out
    assert "null guard" not in out


@pytest.mark.asyncio
async def test_excludes_developer_comments() -> None:
    resolved = _resolved(
        [
            _c("Implemented the foo. [Ship SDLC:role-developer]", minutes_ago=10),
        ]
    )
    out = await _fetch_reviewer_feedback_section(resolved=resolved, ticket_ref="ELS-1")
    assert out is None


@pytest.mark.asyncio
async def test_appends_operator_hint_after_verdict() -> None:
    resolved = _resolved(
        [
            _c("Blocked: conflict with merged base.\n\n[Ship SDLC:role-reviewer]", minutes_ago=30),
            _c("**[Operator hint to developer]** rebase onto main first.", minutes_ago=5),
        ]
    )
    out = await _fetch_reviewer_feedback_section(resolved=resolved, ticket_ref="ELS-1")
    assert out is not None
    assert "conflict with merged base" in out
    assert "rebase onto main first" in out


@pytest.mark.asyncio
async def test_other_sdlc_roles_count_as_feedback() -> None:
    resolved = _resolved(
        [
            _c("Auto-merger paused — overlap.\n\n[Ship SDLC:role-auto-merger]", minutes_ago=10),
        ]
    )
    out = await _fetch_reviewer_feedback_section(resolved=resolved, ticket_ref="ELS-1")
    assert out is not None
    assert "Auto-merger paused" in out


@pytest.mark.asyncio
async def test_non_dev_roles_get_neutral_framing() -> None:
    """reviewer / validation / auto-merger see the same verdict thread
    but framed as neutral context, not an imperative "fix this"."""
    resolved = _resolved(
        [
            _c("Blocked: missing null guard.\n\n[Ship SDLC:role-reviewer]", minutes_ago=10),
        ]
    )
    out = await _fetch_reviewer_feedback_section(
        resolved=resolved, ticket_ref="ELS-1", for_dev=False
    )
    assert out is not None
    assert "Recent ticket activity" in out
    assert "Reviewer feedback to address" not in out
    assert "missing null guard" in out


@pytest.mark.asyncio
async def test_no_comments_returns_none() -> None:
    out = await _fetch_reviewer_feedback_section(resolved=_resolved([]), ticket_ref="ELS-1")
    assert out is None


@pytest.mark.asyncio
async def test_tracker_error_degrades_to_none() -> None:
    out = await _fetch_reviewer_feedback_section(
        resolved=_resolved(RuntimeError("linear 5xx")), ticket_ref="ELS-1"
    )
    assert out is None


@pytest.mark.asyncio
async def test_feedback_is_byte_capped() -> None:
    huge = "x" * (20 * 1024)
    resolved = _resolved(
        [_c(f"{huge}\n\n[Ship SDLC:role-reviewer]", minutes_ago=5)]
    )
    out = await _fetch_reviewer_feedback_section(resolved=resolved, ticket_ref="ELS-1")
    assert out is not None
    assert "…(truncated)" in out
    # header + capped body, well under the raw 20KB
    assert len(out.encode("utf-8")) < 8 * 1024
