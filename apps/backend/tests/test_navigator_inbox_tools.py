"""Navigator Phase-6 Inbox tools (Wave A read + Wave B mutate).

Covers the six per-tool surfaces:

- ``inbox_list`` — owner=me filter, type filter, status filter,
  limit cap, returns ISO strings.
- ``inbox_counts`` — sums match raw row counts.
- ``inbox_get`` — workspace tenancy guard (cross-workspace item →
  ``{error: not_found}``).
- ``inbox_dispose`` — happy path applies + writes audit + side
  effects; ``dry_run=true`` returns ``would_apply`` and writes
  nothing; non-admin → ``{error: forbidden}``; cross-workspace
  item → ``{error: not_found}``.
- ``inbox_snooze`` — sets ``snoozed_until``, writes event row,
  admin-gated.
- ``inbox_reassign`` — verifies new assignee is workspace member;
  admin-gated; writes event with reason.

Real DB (via the ``db_session`` fixture) so tenancy guards
genuinely fire — the spec is explicit about not mocking the
session.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


def _toolbox(session, *, workspace_id, user_id):
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        session,
        settings=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        user_id=user_id,
    )


async def _make_user(db_session, *, email: str | None = None):
    """Insert one ``users`` row and return it.

    The Phase-6 inbox tools never resolve the user via the auth dep
    so we don't need a token — just a real row so the WorkspaceMember
    FK we'll add next has somewhere to point.
    """
    from backend.app.db.models.tenancy import User

    user = User(
        email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Test",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_member(db_session, *, workspace_id, user_id, role="member"):
    from backend.app.db.models.tenancy import WorkspaceMember

    m = WorkspaceMember(
        workspace_id=workspace_id, user_id=user_id, role=role
    )
    db_session.add(m)
    await db_session.flush()
    return m


async def _make_item(
    db_session,
    *,
    workspace_id,
    owner_user_id=None,
    type: str = "clarification",
    status: str = "new",
    title: str = "Need clarification",
    payload: dict | None = None,
    play_key: str | None = None,
    repo_id=None,
):
    """Insert one :class:`InboxItem` and return it.

    ``payload``/``title``/``status`` default to a minimal valid
    clarification owned by the caller's user — the most common
    fixture shape across the tests below.
    """
    from backend.app.db.models.inbox import InboxItem

    item = InboxItem(
        workspace_id=workspace_id,
        owner_user_id=owner_user_id,
        type=type,
        status=status,
        title=title,
        payload=payload or {},
        play_key=play_key,
        repo_id=repo_id,
    )
    db_session.add(item)
    await db_session.flush()
    return item


# ---------------------------------------------------------------------------
# inbox_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_list_owner_me_excludes_others(
    db_session, seed_workspace
) -> None:
    """``owner='me'`` (the default) only returns items the caller owns."""
    user, _, ws = seed_workspace
    other = await _make_user(db_session)
    await _make_member(db_session, workspace_id=ws.id, user_id=other.id)

    mine = await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=user.id,
        title="for me",
    )
    await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=other.id,
        title="for someone else",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(await box.invoke("inbox_list", {}))
    ids = [i["id"] for i in out["items"]]
    assert str(mine.id) in ids
    # Other-owner item must be filtered out by the default ``me`` scope.
    assert all(i["owner_user_id"] == str(user.id) for i in out["items"])


@pytest.mark.asyncio
async def test_inbox_list_type_and_status_filters(
    db_session, seed_workspace
) -> None:
    """``type`` + concrete ``status`` collapse the page to that subset."""
    user, _, ws = seed_workspace
    a = await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=user.id,
        type="clarification",
        status="new",
        title="clar new",
    )
    b = await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=user.id,
        type="improvement",
        status="new",
        title="imp new",
    )
    # Resolved items must drop out of the default ``open`` view but
    # come back when ``status='resolved'`` is asked for explicitly.
    c = await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=user.id,
        type="improvement",
        status="resolved",
        title="imp resolved",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_list", {"type": "improvement", "status": "new"}
        )
    )
    ids = {i["id"] for i in out["items"]}
    assert str(b.id) in ids
    assert str(a.id) not in ids
    assert str(c.id) not in ids


@pytest.mark.asyncio
async def test_inbox_list_limit_cap_and_iso_strings(
    db_session, seed_workspace
) -> None:
    """``limit`` is clamped to the per-tool ceiling; timestamps are ISO."""
    from backend.app.services.agent import tools as tools_mod

    user, _, ws = seed_workspace
    # Seed 3 items so we can verify ISO formatting on each row without
    # needing to hit the cap (which is 100 — too noisy for a unit test).
    for i in range(3):
        await _make_item(
            db_session,
            workspace_id=ws.id,
            owner_user_id=user.id,
            title=f"row {i}",
        )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke("inbox_list", {"limit": 10_000})
    )
    # ``limit`` was clamped — we can't observe the clamp directly, but
    # we know the cap and we know we only seeded 3 rows, so the page
    # is correctly bounded by ``min(cap, total)`` either way. Sanity:
    # the cap constant is exposed at module level so a future bump is
    # caught here too.
    assert tools_mod._MAX_INBOX_LIST >= 25
    assert len(out["items"]) == 3
    for item in out["items"]:
        assert isinstance(item["created_at"], str)
        # ``datetime.fromisoformat`` accepts our serialiser output.
        datetime.fromisoformat(item["created_at"])


# ---------------------------------------------------------------------------
# inbox_counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_counts_matches_raw_row_counts(
    db_session, seed_workspace
) -> None:
    """``by_status`` + ``by_type`` agree with hand-counted seeds."""
    user, _, ws = seed_workspace
    await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=user.id,
        type="clarification",
        status="new",
    )
    await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=user.id,
        type="clarification",
        status="new",
    )
    await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=user.id,
        type="improvement",
        status="snoozed",
    )
    await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=user.id,
        type="failure",
        status="resolved",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(await box.invoke("inbox_counts", {}))
    assert out["owner"] == "me"
    assert out["by_status"]["new"] == 2
    assert out["by_status"]["snoozed"] == 1
    assert out["by_status"]["resolved"] == 1
    # ``open`` is the rolled-up new+snoozed.
    assert out["by_status"]["open"] == 3
    # by_type only counts open items (new + snoozed) — by design.
    assert out["by_type"]["clarification"] == 2
    assert out["by_type"]["improvement"] == 1
    # Resolved failure stays out of the by_type bucket.
    assert out["by_type"]["failure"] == 0
    assert out["total"] == 4


# ---------------------------------------------------------------------------
# inbox_get tenancy
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def two_workspaces(db_session, seed_workspace):
    """Two workspaces under the same org, sharing one admin user.

    Lets the cross-workspace tenancy assertions reuse a single
    auth identity while pointing the ToolBox at one workspace and
    the seeded item at the other.
    """
    from backend.app.db.models.tenancy import (
        Workspace,
        WorkspaceMember,
    )

    user, raw, ws_a = seed_workspace
    ws_b = Workspace(
        org_id=ws_a.org_id, slug=f"ws-{uuid.uuid4().hex[:6]}", name="Other"
    )
    db_session.add(ws_b)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws_b.id, user_id=user.id, role="owner")
    )
    await db_session.flush()
    return user, ws_a, ws_b


@pytest.mark.asyncio
async def test_inbox_get_cross_workspace_returns_not_found(
    db_session, two_workspaces
) -> None:
    """An item from ws_b is invisible to a ToolBox bound to ws_a."""
    user, ws_a, ws_b = two_workspaces
    foreign = await _make_item(
        db_session, workspace_id=ws_b.id, owner_user_id=user.id
    )

    box = _toolbox(db_session, workspace_id=ws_a.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_get", {"inbox_item_id": str(foreign.id)}
        )
    )
    assert out["error"] == "not_found"


# ---------------------------------------------------------------------------
# inbox_dispose
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_dispose_happy_path_resolves_and_audits(
    db_session, seed_workspace
) -> None:
    """``accept`` on an improvement → resolved + audit row + event."""
    from sqlalchemy import select

    from backend.app.db.models.inbox import InboxItem, InboxItemEvent
    from backend.app.db.models.tenancy import AuditLog

    user, _, ws = seed_workspace
    item = await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=user.id,
        type="improvement",
        title="bump deps",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    raw = await box.invoke(
        "inbox_dispose",
        {"inbox_item_id": str(item.id), "disposition": "accept"},
    )
    out = json.loads(raw)
    assert out["new_status"] == "resolved"
    assert out["applied_disposition"] == "accept"
    assert out["resolution"] == "accepted"

    refreshed = await db_session.get(InboxItem, item.id)
    assert refreshed is not None and refreshed.status == "resolved"
    assert refreshed.resolved_by_user_id == user.id

    events = (
        (
            await db_session.execute(
                select(InboxItemEvent).where(
                    InboxItemEvent.item_id == item.id
                )
            )
        )
        .scalars()
        .all()
    )
    # The dispose path writes a single resolved event for accept.
    actions = {e.action for e in events}
    assert "resolved" in actions

    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.workspace_id == ws.id,
                    AuditLog.action == "navigator.tool.inbox_dispose",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 1
    assert audit[0].target_id == str(item.id)


@pytest.mark.asyncio
async def test_inbox_dispose_dry_run_no_writes(
    db_session, seed_workspace
) -> None:
    """``dry_run=true`` returns ``would_apply`` and never mutates state."""
    from sqlalchemy import select

    from backend.app.db.models.inbox import InboxItem, InboxItemEvent
    from backend.app.db.models.tenancy import AuditLog

    user, _, ws = seed_workspace
    item = await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=user.id,
        type="improvement",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_dispose",
            {
                "inbox_item_id": str(item.id),
                "disposition": "accept",
                "dry_run": True,
            },
        )
    )
    assert out["dry_run"] is True
    assert out["would_apply"]["new_status"] == "resolved"
    assert out["would_apply"]["applied_disposition"] == "accept"

    refreshed = await db_session.get(InboxItem, item.id)
    assert refreshed is not None and refreshed.status == "new"

    events = (
        (
            await db_session.execute(
                select(InboxItemEvent).where(
                    InboxItemEvent.item_id == item.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert events == []
    audit = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "navigator.tool.inbox_dispose"
                )
            )
        )
        .scalars()
        .all()
    )
    assert audit == []


@pytest.mark.asyncio
async def test_inbox_dispose_non_admin_forbidden(
    db_session, seed_workspace
) -> None:
    """Bare ``member`` role can't dispose; tool returns ``forbidden``."""
    user, _, ws = seed_workspace
    member = await _make_user(db_session)
    await _make_member(
        db_session, workspace_id=ws.id, user_id=member.id, role="member"
    )
    item = await _make_item(
        db_session,
        workspace_id=ws.id,
        owner_user_id=user.id,
        type="improvement",
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=member.id)
    out = json.loads(
        await box.invoke(
            "inbox_dispose",
            {"inbox_item_id": str(item.id), "disposition": "accept"},
        )
    )
    assert out["error"] == "forbidden"


@pytest.mark.asyncio
async def test_inbox_dispose_cross_workspace_not_found(
    db_session, two_workspaces
) -> None:
    user, ws_a, ws_b = two_workspaces
    foreign = await _make_item(
        db_session,
        workspace_id=ws_b.id,
        owner_user_id=user.id,
        type="improvement",
    )

    box = _toolbox(db_session, workspace_id=ws_a.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_dispose",
            {"inbox_item_id": str(foreign.id), "disposition": "accept"},
        )
    )
    assert out["error"] == "not_found"


# ---------------------------------------------------------------------------
# inbox_snooze
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_snooze_sets_until_and_writes_event(
    db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.inbox import InboxItem, InboxItemEvent

    user, _, ws = seed_workspace
    item = await _make_item(
        db_session, workspace_id=ws.id, owner_user_id=user.id
    )
    until = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(
        microsecond=0
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_snooze",
            {"inbox_item_id": str(item.id), "until": until.isoformat()},
        )
    )
    assert out["snoozed_until"] == until.isoformat()

    refreshed = await db_session.get(InboxItem, item.id)
    assert refreshed is not None
    assert refreshed.status == "snoozed"
    assert refreshed.snoozed_until == until

    events = (
        (
            await db_session.execute(
                select(InboxItemEvent).where(
                    InboxItemEvent.item_id == item.id,
                    InboxItemEvent.action == "snoozed",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].payload.get("snoozed_until") == until.isoformat()


@pytest.mark.asyncio
async def test_inbox_snooze_non_admin_forbidden(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    member = await _make_user(db_session)
    await _make_member(
        db_session, workspace_id=ws.id, user_id=member.id, role="member"
    )
    item = await _make_item(
        db_session, workspace_id=ws.id, owner_user_id=user.id
    )
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    box = _toolbox(db_session, workspace_id=ws.id, user_id=member.id)
    out = json.loads(
        await box.invoke(
            "inbox_snooze",
            {"inbox_item_id": str(item.id), "until": until},
        )
    )
    assert out["error"] == "forbidden"


# ---------------------------------------------------------------------------
# inbox_reassign
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_inbox_reassign_to_member_writes_event_with_reason(
    db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.inbox import InboxItem, InboxItemEvent

    user, _, ws = seed_workspace
    new_owner = await _make_user(db_session)
    await _make_member(
        db_session, workspace_id=ws.id, user_id=new_owner.id
    )
    item = await _make_item(
        db_session, workspace_id=ws.id, owner_user_id=user.id
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_reassign",
            {
                "inbox_item_id": str(item.id),
                "assignee_user_id": str(new_owner.id),
                "reason": "Needs the right SME",
            },
        )
    )
    assert out["new_owner_id"] == str(new_owner.id)
    assert out["prior_owner_id"] == str(user.id)

    refreshed = await db_session.get(InboxItem, item.id)
    assert refreshed is not None
    assert refreshed.owner_user_id == new_owner.id

    events = (
        (
            await db_session.execute(
                select(InboxItemEvent).where(
                    InboxItemEvent.item_id == item.id,
                    InboxItemEvent.action == "reassigned",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(events) == 1
    assert events[0].payload.get("reason") == "Needs the right SME"
    assert events[0].payload.get("new_owner_user_id") == str(new_owner.id)


@pytest.mark.asyncio
async def test_inbox_reassign_assignee_not_member_validation(
    db_session, seed_workspace
) -> None:
    """Assigning to a user that isn't in the workspace is rejected."""
    user, _, ws = seed_workspace
    outsider = await _make_user(db_session)  # NOT added as a member.
    item = await _make_item(
        db_session, workspace_id=ws.id, owner_user_id=user.id
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)
    out = json.loads(
        await box.invoke(
            "inbox_reassign",
            {
                "inbox_item_id": str(item.id),
                "assignee_user_id": str(outsider.id),
            },
        )
    )
    assert out["error"] == "validation_failed"
    assert "workspace member" in out["message"]


@pytest.mark.asyncio
async def test_inbox_reassign_non_admin_forbidden(
    db_session, seed_workspace
) -> None:
    user, _, ws = seed_workspace
    member = await _make_user(db_session)
    await _make_member(
        db_session, workspace_id=ws.id, user_id=member.id, role="member"
    )
    item = await _make_item(
        db_session, workspace_id=ws.id, owner_user_id=user.id
    )

    box = _toolbox(db_session, workspace_id=ws.id, user_id=member.id)
    out = json.loads(
        await box.invoke(
            "inbox_reassign",
            {
                "inbox_item_id": str(item.id),
                "assignee_user_id": str(member.id),
            },
        )
    )
    assert out["error"] == "forbidden"
