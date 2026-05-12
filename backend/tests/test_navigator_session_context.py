"""Navigator session-context frame (the system message the agent reads
each turn before any tool call).

Two surfaces are covered here:

- ``_render_session_context`` — pure rendering. Given a ``_SessionFacts``
  dataclass it must spit out a markdown block with workspace + user +
  tracker + repos + inbox lines. We assert structurally so editorial
  changes to the copy don't trip the test, but the load-bearing tokens
  (workspace name, tracker kind, status, repo full names, inbox count)
  must show up.

- ``_collect_session_facts`` — integration. Given a real workspace +
  seeded ``WorkspaceRepo`` rows + a couple of inbox items, the helper
  reports the right counts. The tracker resolver runs against the
  seeded workspace (which has none) so we assert the unbound-tracker
  branch surfaces in the rendered frame.

Why this matters: PR1 of the Navigator overhaul wires the agent's
identity into the prompt so it stops asking the user "which tracker?"
or "what's my workspace?" every turn. If a future change drops one of
the load-bearing fields these tests catch it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest


def _repo(name: str = "acme/api", **extra):
    from backend.app.services.agent.topic import _RepoSnapshot

    base = dict(
        id=f"00000000-0000-0000-0000-{abs(hash(name)) % (16**12):012x}",
        full_name=name,
        default_branch="main",
        top_languages=["python", "typescript"],
        frameworks=["fastapi", "next.js"],
        kb_chunk_count=42,
        kb_last_indexed_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
    )
    base.update(extra)
    return _RepoSnapshot(**base)


def _facts(**overrides):
    from backend.app.services.agent.topic import _SessionFacts

    base = dict(
        workspace_name="Acme",
        workspace_slug="acme",
        user_name="Alice",
        user_email="alice@acme.test",
        tracker_kind="linear",
        tracker_scope_hint="ENG",
        tracker_status="connected",
        tracker_health_error=None,
        repos=[_repo("acme/api"), _repo("acme/web")],
        inbox_open_total=3,
        inbox_by_type={"clarification": 2, "approval": 1},
    )
    base.update(overrides)
    return _SessionFacts(**base)


def test_render_includes_load_bearing_fields() -> None:
    from backend.app.services.agent.topic import _render_session_context

    out = _render_session_context(
        workspace_id=uuid.UUID("00000000-0000-0000-0000-000000000042"),
        now=datetime(2026, 5, 5, tzinfo=timezone.utc),
        facts=_facts(),
    )
    # Workspace identity
    assert "Acme" in out
    assert "acme" in out  # slug
    # User identity
    assert "Alice" in out
    assert "alice@acme.test" in out
    # Tracker
    assert "linear" in out
    assert "ENG" in out
    assert "connected" in out
    # Repos
    assert "acme/api" in out
    assert "acme/web" in out
    # Inbox snapshot
    assert "3 open" in out
    assert "clarification=2" in out
    # Date pin
    assert "2026-05-05" in out
    assert "Tuesday" in out


def test_render_falls_back_when_facts_unavailable() -> None:
    """A failed fact-collection must not block the turn — the bare
    frame still pins date + workspace id."""
    from backend.app.services.agent.topic import _render_session_context

    workspace_id = uuid.UUID("00000000-0000-0000-0000-000000000043")
    out = _render_session_context(
        workspace_id=workspace_id,
        now=datetime(2026, 5, 5, tzinfo=timezone.utc),
        facts=None,
    )
    assert "2026-05-05" in out
    assert str(workspace_id) in out
    # No identity fields when facts are missing
    assert "Bound tracker" not in out
    assert "Activated repos" not in out


def test_render_unbound_tracker_says_so() -> None:
    """An unbound tracker must surface explicitly so the agent knows
    not to call ``ticket_create`` (and not to ask the user which one
    to use). Refusal-grade copy beats a polite null."""
    from backend.app.services.agent.topic import _render_session_context

    out = _render_session_context(
        workspace_id=uuid.uuid4(),
        now=datetime(2026, 5, 5, tzinfo=timezone.utc),
        facts=_facts(tracker_kind=None, tracker_scope_hint=None, tracker_status="disconnected"),
    )
    assert "Bound tracker:" in out
    assert "none" in out.lower()
    assert "ticket_create" in out  # explicit about what's broken


def test_render_tracker_error_surfaces_health_message() -> None:
    """When the resolved tracker is in error state, the rendered frame
    must show ``status=error`` AND the underlying message so the agent
    can phrase a useful response without dialling /_debug-tracker."""
    from backend.app.services.agent.topic import _render_session_context

    out = _render_session_context(
        workspace_id=uuid.uuid4(),
        now=datetime(2026, 5, 5, tzinfo=timezone.utc),
        facts=_facts(
            tracker_status="error",
            tracker_health_error="401 Unauthorized — token expired",
        ),
    )
    assert "status=error" in out
    assert "401 Unauthorized" in out


def test_render_caps_repo_list_with_tail() -> None:
    """Long repo lists get a ``+N more (omitted for brevity)`` tail
    so the prefix stays bounded. Past the cap the agent has the
    list_count and can grep audit if it really needs to know the
    13th repo, but the common case is the first half-dozen."""
    from backend.app.services.agent.topic import _render_session_context

    repos = [_repo(f"acme/{i}") for i in range(20)]
    out = _render_session_context(
        workspace_id=uuid.uuid4(),
        now=datetime(2026, 5, 5, tzinfo=timezone.utc),
        facts=_facts(repos=repos),
    )
    assert "Activated repos (20)" in out
    assert "+ 14 more" in out  # 20 - 6-cap


def test_render_zero_inbox_says_zero() -> None:
    """Empty inbox surfaces as `0 open` so the agent knows there's no
    queue to mention; absence of the line would invite hallucination."""
    from backend.app.services.agent.topic import _render_session_context

    out = _render_session_context(
        workspace_id=uuid.uuid4(),
        now=datetime(2026, 5, 5, tzinfo=timezone.utc),
        facts=_facts(inbox_open_total=0, inbox_by_type={}),
    )
    assert "0 open" in out


@pytest.mark.asyncio
async def test_collect_session_facts_reports_seeded_state(
    db_session, seed_workspace
) -> None:
    """End-to-end fact collection: the helper queries workspace, user,
    tracker resolver, repos, and inbox counts. Test seeds a workspace
    with no tracker + no repos + a couple of inbox items and asserts
    the helper surfaces the expected shape."""
    from backend.app.core.config import get_settings
    from backend.app.db.models.inbox import InboxItem
    from backend.app.services.agent.topic import _collect_session_facts

    user, _, workspace = seed_workspace
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=None,
            type="clarification",
            title="t1",
            status="new",
            intake_handle=None,
        )
    )
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=None,
            type="approval",
            title="t2",
            status="new",
            intake_handle=None,
        )
    )
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=None,
            type="failure",
            title="t3",
            status="dismissed",  # NOT counted — closed.
            intake_handle=None,
        )
    )
    await db_session.flush()

    facts = await _collect_session_facts(
        db_session,
        workspace_id=workspace.id,
        user_id=user.id,
        settings=get_settings(),
    )

    # Identity
    assert facts.workspace_name == workspace.name
    assert facts.workspace_slug == workspace.slug
    assert facts.user_email == user.email
    # Tracker — none seeded; resolver returns None and helper reports unbound
    assert facts.tracker_kind is None
    # Repos — none seeded
    assert facts.repos == []
    # Inbox — 2 open (the dismissed row is excluded)
    assert facts.inbox_open_total == 2
    assert facts.inbox_by_type == {"clarification": 1, "approval": 1}
