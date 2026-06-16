"""ELS-328 — a Linear team binds to at most one workspace.

The tracker is the dispatch-signal source: two workspaces on the same
Linear team both dispatch the same ``ELS-###`` ticket and the repo
resolves per-workspace → cross-tenant action (the ship-landing /
``no_pr_url`` incident, 2026-06). ``_assert_team_unclaimed`` blocks a
*second* workspace from claiming a team another workspace already binds
and actively polls; re-binding inside the same workspace stays allowed
(enforced by the ``workspace_id != current`` filter in the query).

These tests pin the helper's branch logic against a mocked session,
matching the mock-based style of test_phase4_code_review_validation.py.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.app.api.v1.routes.linear_oauth import _assert_team_unclaimed


def _session(first_result):
    s = AsyncMock()
    s.execute = AsyncMock(
        return_value=MagicMock(first=MagicMock(return_value=first_result))
    )
    return s


def test_blocks_when_team_claimed_by_other_workspace() -> None:
    s = _session(("other-workspace-uuid",))
    with pytest.raises(HTTPException) as ei:
        asyncio.new_event_loop().run_until_complete(
            _assert_team_unclaimed(
                s, workspace_id=uuid.uuid4(), team_id="team-123"
            )
        )
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "linear_team_already_bound"
    assert ei.value.detail["team_id"] == "team-123"


def test_allows_when_team_unclaimed() -> None:
    s = _session(None)
    # No raise — the team is free.
    asyncio.new_event_loop().run_until_complete(
        _assert_team_unclaimed(s, workspace_id=uuid.uuid4(), team_id="team-123")
    )


def test_noop_on_empty_team_id() -> None:
    # An unset team_id (multi-team connect awaiting a repick) short-circuits
    # before touching the DB — nothing to claim yet.
    s = _session(("should-not-be-read",))
    asyncio.new_event_loop().run_until_complete(
        _assert_team_unclaimed(s, workspace_id=uuid.uuid4(), team_id=None)
    )
    s.execute.assert_not_called()
