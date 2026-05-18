"""HTTP tests for ``/v1/workspaces/{ws}/inbox`` (RFC-0010 P2-03).

Exercises list/detail/disposition + snooze/unsnooze/reassign +
event-append. Items are inserted directly via the ORM since
intake (``services.inbox.intake``) owns row creation; this PR is
reads + lifecycle only.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.app.db.models.inbox import (
    InboxItem,
    InboxItemEvent,
    InboxRoutingRule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


async def _mint_user(db_session, *, email: str | None = None):
    from backend.app.db.models.tenancy import User

    user = User(
        email=email or f"u-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Test User",
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _mint_role(db_session, workspace, role: str):
    """Create a workspace member + PAT bound to ``workspace``."""
    from backend.app.api.v1.deps import PAT_PREFIX, _hash_token
    from backend.app.db.models.tenancy import (
        ApiToken,
        WorkspaceMember,
    )

    user = await _mint_user(
        db_session, email=f"{role}-{uuid.uuid4().hex[:6]}@example.com"
    )
    db_session.add(
        WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role=role)
    )
    raw = f"{PAT_PREFIX}{secrets.token_urlsafe(24)}"
    db_session.add(
        ApiToken(
            user_id=user.id,
            name=f"{role}-token",
            hashed_secret=_hash_token(raw),
            prefix=PAT_PREFIX,
            scopes=[],
        )
    )
    await db_session.flush()
    return user, raw


async def _make_item(
    db_session,
    workspace,
    *,
    type: str = "clarification",
    status: str = "new",
    owner_user_id: uuid.UUID | None = None,
    title: str | None = None,
    payload: dict | None = None,
    play_key: str | None = None,
    repo_id: uuid.UUID | None = None,
    snoozed_until: datetime | None = None,
    created_at: datetime | None = None,
) -> InboxItem:
    item = InboxItem(
        workspace_id=workspace.id,
        type=type,
        status=status,
        owner_user_id=owner_user_id,
        title=title or f"item-{uuid.uuid4().hex[:6]}",
        summary="test summary",
        payload=payload or {},
        play_key=play_key,
        repo_id=repo_id,
        snoozed_until=snoozed_until,
        intake_handle="test_handle",
        intake_reason="test:fixture",
    )
    db_session.add(item)
    await db_session.flush()
    if created_at is not None:
        item.created_at = created_at
        await db_session.flush()
    return item


# ---------------------------------------------------------------------------
# 1. list/empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_includes_action_item_count(v1_client, seed_workspace, db_session):
    _, raw, ws = seed_workspace
    await _make_item(
        db_session,
        ws,
        type="report",
        title="digest",
        payload={
            "action_items": [
                {"id": "a", "prompt": "Q1"},
                {"id": "b", "prompt": "Q2"},
            ]
        },
    )
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox", headers=_auth(raw)
    )
    assert res.status_code == 200, res.text
    row = res.json()["items"][0]
    assert row["action_item_count"] == 2


@pytest.mark.asyncio
async def test_list_includes_headline_fields_from_payload(
    v1_client, seed_workspace, db_session
):
    _, raw, ws = seed_workspace
    await _make_item(
        db_session,
        ws,
        type="blocker",
        title="agent blocked: validation",
        payload={"ticket_ref": "ELS-99", "fsm_stage": "validation"},
    )
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox", headers=_auth(raw)
    )
    assert res.status_code == 200, res.text
    row = res.json()["items"][0]
    assert row["ticket_ref"] == "ELS-99"
    assert row["fsm_stage"] == "validation"


@pytest.mark.asyncio
async def test_list_empty_returns_zero_counts(v1_client, seed_workspace):
    _, raw, ws = seed_workspace
    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox", headers=_auth(raw)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["items"] == []
    assert body["total"] == 0
    assert body["counts_by_type"] == {
        "clarification": 0,
        "improvement": 0,
        "failure": 0,
        "approval": 0,
        "exception": 0,
        "stuck": 0,
        "blocker": 0,
    }
    assert body["counts_by_status"] == {
        "new": 0,
        "snoozed": 0,
        "resolved": 0,
        "dismissed": 0,
    }
    assert body["next_cursor"] is None


# ---------------------------------------------------------------------------
# 2. ownership=mine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_by_ownership_mine(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    stranger = await _mint_user(db_session)

    await _make_item(db_session, ws, owner_user_id=user.id, title="mine-1")
    await _make_item(db_session, ws, owner_user_id=stranger.id, title="other-1")
    await _make_item(db_session, ws, owner_user_id=stranger.id, title="other-2")

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox?ownership=mine", headers=_auth(raw)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "mine-1"
    assert body["items"][0]["owner"]["user_id"] == str(user.id)


# ---------------------------------------------------------------------------
# 3. type filter (multi)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_filters_by_type_multi(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    for t in ("clarification", "improvement", "failure", "approval", "exception"):
        await _make_item(
            db_session, ws, type=t, owner_user_id=user.id, title=f"t-{t}"
        )

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox"
        "?ownership=mine&type=clarification&type=approval",
        headers=_auth(raw),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    titles = {i["title"] for i in body["items"]}
    assert titles == {"t-clarification", "t-approval"}
    # Counts-by-type must IGNORE the type filter so the chip
    # counts stay honest while the user is excluding types.
    assert body["counts_by_type"]["failure"] == 1
    assert body["counts_by_type"]["improvement"] == 1


# ---------------------------------------------------------------------------
# 4. cursor pagination round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pagination_cursor_round_trip(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    base = datetime.now(timezone.utc) - timedelta(days=1)
    for i in range(30):
        await _make_item(
            db_session,
            ws,
            owner_user_id=user.id,
            title=f"item-{i:02d}",
            created_at=base + timedelta(seconds=i),
        )

    seen_ids: list[str] = []
    cursor: str | None = None
    pages = 0
    totals: list[int] = []
    while True:
        url = f"/v1/workspaces/{ws.id}/inbox?ownership=mine&limit=10"
        if cursor is not None:
            url += f"&cursor={cursor}"
        res = await v1_client.get(url, headers=_auth(raw))
        assert res.status_code == 200, res.text
        body = res.json()
        seen_ids.extend(i["id"] for i in body["items"])
        totals.append(body["total"])
        pages += 1
        cursor = body.get("next_cursor")
        if cursor is None:
            break
        if pages > 5:  # safety brake
            pytest.fail("pagination did not terminate")
    assert pages == 3
    assert len(seen_ids) == 30
    assert len(set(seen_ids)) == 30  # no duplicates across pages
    assert all(t == 30 for t in totals)


# ---------------------------------------------------------------------------
# 5. counts endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_counts_endpoint_groups_by_type_and_status(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    stranger = await _mint_user(db_session)

    # mine + new
    await _make_item(
        db_session, ws, owner_user_id=user.id, type="clarification"
    )
    await _make_item(
        db_session, ws, owner_user_id=user.id, type="approval"
    )
    # unassigned + new
    await _make_item(db_session, ws, owner_user_id=None, type="failure")
    # someone else + new
    await _make_item(
        db_session, ws, owner_user_id=stranger.id, type="improvement"
    )
    # snoozed (counts as open but not new)
    await _make_item(
        db_session,
        ws,
        owner_user_id=user.id,
        type="exception",
        status="snoozed",
        snoozed_until=datetime.now(timezone.utc) + timedelta(days=1),
    )
    # resolved
    await _make_item(
        db_session,
        ws,
        owner_user_id=user.id,
        type="clarification",
        status="resolved",
    )

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/counts", headers=_auth(raw)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mine"] == 2  # clarification + approval, both 'new'
    assert body["unassigned"] == 1
    assert body["all_open"] == 5  # 4 new + 1 snoozed
    assert body["by_type"]["clarification"] == 1  # only 'new'
    assert body["by_type"]["approval"] == 1
    assert body["by_type"]["failure"] == 1
    assert body["by_type"]["improvement"] == 1
    assert body["by_type"]["exception"] == 0  # exception is snoozed, not new
    assert body["by_type"]["stuck"] == 0
    assert body["by_type"]["blocker"] == 0
    assert body["by_status"]["new"] == 4
    assert body["by_status"]["snoozed"] == 1
    assert body["by_status"]["resolved"] == 1
    assert body["by_status"]["dismissed"] == 0
    # Cache hint set so the nav badge poller doesn't hammer it.
    assert "max-age=10" in res.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# 6. detail with events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_detail_includes_events_in_order(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    item = await _make_item(db_session, ws, owner_user_id=user.id)
    base = datetime.now(timezone.utc) - timedelta(minutes=5)
    for i, action in enumerate(("created", "assigned", "commented")):
        ev = InboxItemEvent(
            item_id=item.id,
            actor_user_id=user.id,
            actor_kind="user" if action == "commented" else "system",
            action=action,
            payload={"i": i},
        )
        db_session.add(ev)
        await db_session.flush()
        ev.created_at = base + timedelta(seconds=i)
        await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}", headers=_auth(raw)
    )
    assert res.status_code == 200, res.text
    body = res.json()
    actions = [e["action"] for e in body["events"]]
    assert actions == ["created", "assigned", "commented"]
    assert body["payload"] == {}
    assert body["source_table"] is None


# ---------------------------------------------------------------------------
# 7. resolve disposition
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disposition_resolve_sets_status_and_records_event(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    item = await _make_item(db_session, ws, owner_user_id=user.id)

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/disposition",
        headers=_auth(raw),
        json={"action": "resolve", "payload": {"note": "lgtm"}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "resolved"
    assert body["resolution"] == "acknowledged"
    assert body["resolved_at"] is not None
    actions = [e["action"] for e in body["events"]]
    assert "resolved" in actions
    resolved_event = next(e for e in body["events"] if e["action"] == "resolved")
    assert resolved_event["payload"]["disposition"] == "resolve"


# ---------------------------------------------------------------------------
# 8. approve only valid for approval type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disposition_approve_only_valid_for_approval_type(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    item = await _make_item(
        db_session, ws, owner_user_id=user.id, type="clarification"
    )
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/disposition",
        headers=_auth(raw),
        json={"action": "approve"},
    )
    assert res.status_code == 422, res.text
    assert "approval" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 9. answer requires payload.answer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disposition_answer_requires_payload_answer(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    item = await _make_item(
        db_session, ws, owner_user_id=user.id, type="clarification"
    )
    bad = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/disposition",
        headers=_auth(raw),
        json={"action": "answer"},
    )
    assert bad.status_code == 422, bad.text

    good = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/disposition",
        headers=_auth(raw),
        json={"action": "answer", "answer": "the answer is 42"},
    )
    assert good.status_code == 200, good.text
    body = good.json()
    assert body["resolution"] == "answered"
    assert body["payload"]["answer"] == "the answer is 42"


# ---------------------------------------------------------------------------
# 10. owner-or-admin RBAC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disposition_owner_or_admin_only(
    v1_client, seed_workspace, db_session
):
    """A plain member viewing someone else's item cannot resolve it."""
    user, _, ws = seed_workspace
    item = await _make_item(db_session, ws, owner_user_id=user.id)

    _, viewer_raw = await _mint_role(db_session, ws, "viewer")
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/disposition",
        headers=_auth(viewer_raw),
        json={"action": "resolve"},
    )
    assert res.status_code == 403, res.text


# ---------------------------------------------------------------------------
# 11. snooze requires future
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snooze_requires_future_timestamp(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    item = await _make_item(db_session, ws, owner_user_id=user.id)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/snooze",
        headers=_auth(raw),
        json={"snoozed_until": past},
    )
    assert res.status_code == 422, res.text
    assert "future" in res.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 12. snooze cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snooze_cap_30_days(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    item = await _make_item(db_session, ws, owner_user_id=user.id)
    too_far = (datetime.now(timezone.utc) + timedelta(days=60)).isoformat()
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/snooze",
        headers=_auth(raw),
        json={"snoozed_until": too_far},
    )
    assert res.status_code == 422, res.text
    assert "30" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 13. unsnooze only from snoozed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsnooze_only_from_snoozed_state(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    item = await _make_item(db_session, ws, owner_user_id=user.id, status="new")
    bad = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/unsnooze",
        headers=_auth(raw),
    )
    assert bad.status_code == 422, bad.text

    # Now snooze, then unsnooze should succeed.
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    snoozed = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/snooze",
        headers=_auth(raw),
        json={"snoozed_until": future},
    )
    assert snoozed.status_code == 200, snoozed.text
    assert snoozed.json()["status"] == "snoozed"

    woken = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/unsnooze",
        headers=_auth(raw),
    )
    assert woken.status_code == 200, woken.text
    body = woken.json()
    assert body["status"] == "new"
    assert body["snoozed_until"] is None


# ---------------------------------------------------------------------------
# 14. reassign user_id sets owner + clears handle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_user_id_sets_owner_and_clears_handle(
    v1_client, seed_workspace, db_session
):
    owner, raw, ws = seed_workspace
    target_user, _ = await _mint_role(db_session, ws, "member")
    item = await _make_item(db_session, ws, owner_user_id=owner.id)

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/reassign",
        headers=_auth(raw),
        json={"user_id": str(target_user.id)},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["owner"]["user_id"] == str(target_user.id)
    assert body["intake_handle"] is None
    assert body["intake_reason"] == "manual:admin"
    actions = [e["action"] for e in body["events"]]
    assert "reassigned" in actions


# ---------------------------------------------------------------------------
# 15. reassign by handle uses routing resolver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_handle_uses_routing_resolver(
    v1_client, seed_workspace, db_session
):
    owner, raw, ws = seed_workspace
    target_user, _ = await _mint_role(db_session, ws, "member")
    # Seed a routing rule so the resolver returns target_user.
    db_session.add(
        InboxRoutingRule(
            workspace_id=ws.id,
            handle_key="security_officer",
            target_type="user",
            target_value=str(target_user.id),
            assignment_strategy=None,
            strategy_config={},
            is_enabled=True,
        )
    )
    await db_session.flush()

    item = await _make_item(db_session, ws, owner_user_id=owner.id)
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/reassign",
        headers=_auth(raw),
        json={"handle": "security_officer"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["owner"]["user_id"] == str(target_user.id)
    assert body["intake_handle"] == "security_officer"
    assert body["intake_reason"] == "rule:user"


# ---------------------------------------------------------------------------
# 16. reassign with unresolvable handle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reassign_handle_unresolved_returns_422(
    v1_client, seed_workspace, db_session, monkeypatch
):
    """A resolver that returns ``user_id=None`` produces a 422.

    The resolver's natural unresolved path requires a workspace with
    no owner/admin/maintainer fallback — but the caller of
    ``/reassign`` MUST be an admin to hit the endpoint, so we
    cannot construct that geometry without losing access. Instead
    we monkeypatch the route's bound resolver to return the
    unresolved sentinel and assert the route surfaces 422.
    """
    from backend.app.api.v1.routes import inbox as inbox_route
    from backend.app.services.inbox.routing import ResolvedTarget

    user, raw, ws = seed_workspace
    item = await _make_item(db_session, ws, owner_user_id=user.id)

    async def _fake_unresolved(_session, handle, _ctx, **_kw):
        return ResolvedTarget(
            user_id=None,
            group_id=None,
            intake_handle=handle,
            intake_reason="unresolved",
        )

    monkeypatch.setattr(inbox_route, "resolve_handle", _fake_unresolved)

    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/reassign",
        headers=_auth(raw),
        json={"handle": "made_up_handle_with_no_rule"},
    )
    assert res.status_code == 422, res.text
    assert "no user" in res.json()["detail"]


# ---------------------------------------------------------------------------
# 17. workspace isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_isolation_on_get_detail(
    v1_client, seed_workspace, db_session
):
    user, raw, ws_a = seed_workspace
    item = await _make_item(db_session, ws_a, owner_user_id=user.id)

    from backend.app.db.models.tenancy import (
        Org,
        OrgMember,
        Workspace,
        WorkspaceMember,
    )

    other_org = Org(
        slug=f"o-{uuid.uuid4().hex[:6]}", name="x", plan="free"
    )
    db_session.add(other_org)
    await db_session.flush()
    db_session.add(
        OrgMember(org_id=other_org.id, user_id=user.id, role="org_owner")
    )
    ws_b = Workspace(
        org_id=other_org.id, slug=f"wb-{uuid.uuid4().hex[:6]}", name="ws-b"
    )
    db_session.add(ws_b)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(workspace_id=ws_b.id, user_id=user.id, role="owner")
    )
    await db_session.flush()

    res = await v1_client.get(
        f"/v1/workspaces/{ws_b.id}/inbox/{item.id}", headers=_auth(raw)
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 18. event append
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_append_returns_event_and_appears_in_detail_list(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    item = await _make_item(db_session, ws, owner_user_id=user.id)

    appended = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/events",
        headers=_auth(raw),
        json={"body": "I'm looking into it"},
    )
    assert appended.status_code == 201, appended.text
    body = appended.json()
    assert body["action"] == "commented"
    assert body["payload"]["body"] == "I'm looking into it"

    detail = await v1_client.get(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}", headers=_auth(raw)
    )
    actions = [e["action"] for e in detail.json()["events"]]
    assert "commented" in actions


# ---------------------------------------------------------------------------
# 19. payload merge semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disposition_payload_merges_not_replaces(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    item = await _make_item(
        db_session,
        ws,
        owner_user_id=user.id,
        payload={"a": 1, "requires_approval": False},
    )
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/disposition",
        headers=_auth(raw),
        json={"action": "resolve", "payload": {"b": 2}},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # Original keys must survive the disposition merge.
    assert body["payload"]["a"] == 1
    assert body["payload"]["b"] == 2
    assert body["payload"]["requires_approval"] is False


# ---------------------------------------------------------------------------
# 20. resolved_at + resolved_by
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_resolved_at_and_resolved_by_set_correctly(
    v1_client, seed_workspace, db_session
):
    user, raw, ws = seed_workspace
    item = await _make_item(db_session, ws, owner_user_id=user.id)

    before = datetime.now(timezone.utc)
    res = await v1_client.post(
        f"/v1/workspaces/{ws.id}/inbox/{item.id}/disposition",
        headers=_auth(raw),
        json={"action": "resolve"},
    )
    assert res.status_code == 200, res.text
    after = datetime.now(timezone.utc)

    # Drill into the DB to confirm both ``resolved_at`` and
    # ``resolved_by_user_id`` were stamped — the API response only
    # surfaces ``resolved_at`` on the row.
    refreshed = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.id == item.id)
        )
    ).scalars().one()
    assert refreshed.resolved_by_user_id == user.id
    assert refreshed.resolved_at is not None
    assert before - timedelta(seconds=5) <= refreshed.resolved_at <= after + timedelta(seconds=5)
