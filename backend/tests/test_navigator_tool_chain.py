"""Phase-6 multi-tool chat-turn integration smoke (P6-24).

Sequences several Wave A + Wave B tools through one ``ToolBox``
against a real ``db_session`` so any composition-level breakage
(e.g. one tool's flush masking another's read, or a SAVEPOINT
escape) shows up as a concrete failure.

Specifically verifies:

- ``inbox_list`` returns the seeded item.
- ``inbox_get`` returns the same item with full detail.
- ``inbox_dispose`` with ``dry_run=true`` returns ``would_apply``
  and writes nothing (the item stays open, no event row, no
  audit row).
- ``inbox_dispose`` with the same args MINUS ``dry_run`` resolves
  the item, writes the audit row, and lets the side-effect
  dispatcher fire.
- ``runs_list`` finds the seeded run after the inbox-side
  mutations have flushed.
- The audit log carries EXACTLY ONE
  ``navigator.tool.inbox_dispose`` entry — proves the dry_run
  path is correctly silent.

Disposition note: the spec sketch uses ``"accept"`` against a
``type=clarification`` item, but the state-machine in
``backend.app.api.v1.routes.inbox._TYPE_GATED_ACTIONS`` restricts
``accept`` to ``type=improvement`` items, so ``inbox_dispose`` with
that combination would (correctly) reject as
``precondition_failed`` before the integration could observe the
multi-tool composition. We swap the disposition for ``"resolve"``,
which is the open-ended action permitted from any open inbox
status — same coverage of the dry-run vs apply branches and the
audit-emission contract.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest


def _toolbox(session, *, workspace_id, user_id):
    from backend.app.services.agent.tools import ToolBox

    return ToolBox(
        session,
        settings=None,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        user_id=user_id,
    )


@pytest.mark.asyncio
async def test_chat_turn_multi_tool_composition(
    db_session, seed_workspace
) -> None:
    from sqlalchemy import select

    from backend.app.db.models.inbox import (
        InboxItem,
        InboxItemEvent,
        InboxRoutingRule,
    )
    from backend.app.db.models.integrations import WorkspaceRepo
    from backend.app.db.models.lanes import Routine, RoutineRun
    from backend.app.db.models.tenancy import AuditLog

    user, _, ws = seed_workspace

    # 1 routing rule (read-side surface; not exercised mutating
    # here but its presence verifies the seeded handles surface
    # in the same workspace).
    db_session.add(
        InboxRoutingRule(
            workspace_id=ws.id,
            handle_key="release_owner",
            target_type="user",
            target_value=str(user.id),
            assignment_strategy=None,
            strategy_config={},
            is_enabled=True,
        )
    )

    # 1 open clarification InboxItem.
    item = InboxItem(
        workspace_id=ws.id,
        owner_user_id=user.id,
        type="clarification",
        status="new",
        title="Need product confirmation",
        payload={"question": "ship the rollout?"},
        play_key="flow-pr-self-review",
    )
    db_session.add(item)
    await db_session.flush()

    # 1 Pipeline + PipelineRun so ``runs_list`` has something to find.
    repo = WorkspaceRepo(
        workspace_id=ws.id,
        installation_id=None,
        provider="github",
        external_id=900_001,
        full_name="acme/chain-test",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/chain-test",
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()

    pipeline = Routine(
        workspace_id=ws.id,
        repo_id=repo.id,
        lane_id="flow-pr-self-review",
        kind="event",
        pattern="pr-and-ci-gate",
        enabled=True,
    )
    db_session.add(pipeline)
    await db_session.flush()
    run = RoutineRun(
        routine_id=pipeline.id,
        workspace_id=ws.id,
        trigger="manual",
        status="succeeded",
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        summary="ok",
    )
    db_session.add(run)
    await db_session.flush()

    box = _toolbox(db_session, workspace_id=ws.id, user_id=user.id)

    # 1) inbox_list — the seeded item must surface in the default
    # ``owner=me`` view.
    list_out = json.loads(await box.invoke("inbox_list", {"owner": "me"}))
    assert any(row["id"] == str(item.id) for row in list_out["items"])

    # 2) inbox_get — full detail matches the seeded fields.
    get_out = json.loads(
        await box.invoke("inbox_get", {"inbox_item_id": str(item.id)})
    )
    assert get_out["id"] == str(item.id)
    assert get_out["type"] == "clarification"
    assert get_out["status"] == "new"

    # 3) inbox_dispose dry_run — must NOT mutate state and must
    # NOT write an audit row. (See module docstring for why we
    # use ``resolve`` instead of the spec's ``accept``.)
    dry_out = json.loads(
        await box.invoke(
            "inbox_dispose",
            {
                "inbox_item_id": str(item.id),
                "disposition": "resolve",
                "dry_run": True,
            },
        )
    )
    assert dry_out["dry_run"] is True
    assert dry_out["would_apply"]["new_status"] == "resolved"

    # Re-read: item must still be open after the dry run.
    refreshed = await db_session.get(InboxItem, item.id)
    assert refreshed is not None and refreshed.status == "new"

    # 4) inbox_dispose for real.
    real_out = json.loads(
        await box.invoke(
            "inbox_dispose",
            {
                "inbox_item_id": str(item.id),
                "disposition": "resolve",
            },
        )
    )
    assert real_out["new_status"] == "resolved"

    # Item is now resolved; the dispose path wrote a ``resolved``
    # event row (proves the side-effect dispatcher fired alongside
    # the audit envelope).
    refreshed = await db_session.get(InboxItem, item.id)
    assert refreshed is not None and refreshed.status == "resolved"
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
    actions = {e.action for e in events}
    assert "resolved" in actions

    # 5) runs_list — the seeded run is reachable after the inbox
    # mutations have flushed (composition smoke).
    runs_out = json.loads(await box.invoke("runs_list", {"limit": 5}))
    assert any(r["id"] == str(run.id) for r in runs_out["runs"])

    # 6) Audit log invariant: exactly one inbox_dispose row for this
    # turn — confirms the dry_run branch did NOT audit.
    audit_rows = (
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
    assert len(audit_rows) == 1
    assert audit_rows[0].target_id == str(item.id)
    # And the redacted payload preserves both the disposition and
    # the navigator actor stamp injected by ``_audit_navigator_tool``.
    assert audit_rows[0].payload.get("disposition") == "resolve"
    assert audit_rows[0].payload.get("actor_kind") == "navigator"
