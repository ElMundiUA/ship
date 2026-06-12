"""Tests for the legacy → Inbox dual-write path (RFC-0010 P2-08).

Covers the helper module
:mod:`backend.app.services.inbox.dual_write` plus the wire-in into
the legacy clarifications / improvements routes:

- POST creates land both a legacy row and an inbox mirror.
- A failing intake call NEVER blocks the legacy create (logged
  WARNING + swallowed exception).
- PATCH on a clarification (answered / skipped) and on an
  improvement (accepted / declined / deferred) propagates the
  terminal state into the mirror with the documented status /
  resolution / snoozed_until rules.
- The resolve mirror is idempotent on repeat PATCH and gracefully
  skips when no mirror exists (pre-cutover row).
- Audit-trail markers: ``payload.reason`` is ``legacy_mirror``
  on the create event (written by intake) and ``legacy_writeback``
  on the resolve event (written by dual_write).
- Pipeline-authored ingress writes the create event with
  ``actor_kind='system'`` (no HTTP user context).

Tests bind ``workspace_id = workspace.id`` immediately after the
fixture call to dodge the ``MissingGreenlet`` trap that fires when
``db_session.expire_all()`` (later in the test) makes the ORM
re-fetch a previously-loaded attribute outside an active greenlet.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Shared fixtures (mirror the conventions from
# test_v1_clarifications.py / test_v1_improvements.py)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seed_repo_and_install(db_session, seed_workspace):
    """Repo + install so the run_token-bearing pipeline tests work."""
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    _, raw, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=918_001,
        account_login="acme-dual",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=98_080_808,
        full_name="acme-dual/dual-write-repo",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme-dual/dual-write-repo",
        description=None,
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()
    return raw, workspace, install, repo


async def _mint_run_token_for(
    db_session, v1_client, workspace_id, raw_pat, repo_id, monkeypatch
):
    """Helper: dispatch a routine run and re-mint a usable run-token."""
    from backend.app.api.v1.routes import runs as runs_route
    from backend.app.api.v1.routes.runs import (
        _hash_run_token,
        _mint_run_token,
    )
    from backend.app.core.config import get_settings
    from backend.app.db.models.lanes import Routine, RoutineRun

    async def _probe(*_a, **_kw):
        return frozenset({"parallel-audit-lanes.yml"})

    async def _dispatch(*_a, **_kw):
        return None

    monkeypatch.setattr(runs_route, "list_repo_workflows", _probe)
    monkeypatch.setattr(runs_route, "dispatch_workflow", _dispatch)

    routine = Routine(
        workspace_id=workspace_id,
        repo_id=repo_id,
        lane_id="tech_debt",
        kind="event",
        pattern="parallel-audit-lanes",
    )
    db_session.add(routine)
    await db_session.flush()
    routine_id = routine.id

    dispatch = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/routines/{routine_id}/runs",
        headers={"Authorization": f"Bearer {raw_pat}"},
    )
    assert dispatch.status_code == 202, dispatch.text
    run_json = dispatch.json()
    settings = get_settings()
    raw_token = _mint_run_token(uuid.UUID(run_json["id"]), settings)
    run = await db_session.get(RoutineRun, uuid.UUID(run_json["id"]))
    run.run_token_hash = _hash_run_token(raw_token)
    await db_session.flush()
    return raw_token, run_json


# ---------------------------------------------------------------------------
# 1. Create-side mirroring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mirror_clarification_create_inserts_inbox_row(
    v1_client, db_session, seed_workspace
) -> None:
    """POST /clarifications writes both legacy row and an inbox mirror."""
    from backend.app.db.models.agent_surface import Clarification
    from backend.app.db.models.inbox import InboxItem

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/clarifications",
        headers={"Authorization": f"Bearer {raw}"},
        json={"question": "which queue?", "ticket_ref": "LIN-1"},
    )
    assert resp.status_code == 201, resp.text
    legacy_id = uuid.UUID(resp.json()["id"])

    db_session.expire_all()
    legacy_rows = (
        await db_session.execute(
            select(Clarification).where(
                Clarification.workspace_id == workspace_id
            )
        )
    ).scalars().all()
    assert len(legacy_rows) == 1
    assert legacy_rows[0].id == legacy_id

    mirror_rows = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "clarifications",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalars().all()
    assert len(mirror_rows) == 1
    assert mirror_rows[0].type == "clarification"
    assert mirror_rows[0].status == "new"
    assert mirror_rows[0].workspace_id == workspace_id
    assert mirror_rows[0].title == "which queue?"
    assert mirror_rows[0].payload["ticket_ref"] == "LIN-1"


@pytest.mark.asyncio
async def test_mirror_clarification_create_e2e_stamps_ttl(
    v1_client, db_session, seed_workspace
) -> None:
    """``context.e2e`` clarifications self-expire via a TTL on the mirror.

    Regression for the orphan ``validation-els141-... — validation
    probe?`` row that sat ``open`` for 3 days (2026-06-01): probe
    fixtures must auto-dismiss instead of WAITING forever.
    """
    from backend.app.db.models.inbox import InboxItem
    from backend.app.services.inbox.dual_write import _E2E_CLARIFICATION_TTL

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/clarifications",
        headers={"Authorization": f"Bearer {raw}"},
        json={"question": "probe?", "context": {"e2e": True}},
    )
    assert resp.status_code == 201, resp.text
    legacy_id = uuid.UUID(resp.json()["id"])

    db_session.expire_all()
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "clarifications",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalars().one()
    assert mirror.stale_after == _E2E_CLARIFICATION_TTL
    assert mirror.auto_resolvable is True


@pytest.mark.asyncio
async def test_mirror_clarification_create_non_e2e_has_no_ttl(
    v1_client, db_session, seed_workspace
) -> None:
    """Real (non-probe) clarifications keep the no-TTL default."""
    from backend.app.db.models.inbox import InboxItem

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/clarifications",
        headers={"Authorization": f"Bearer {raw}"},
        json={"question": "real question?"},
    )
    assert resp.status_code == 201, resp.text
    legacy_id = uuid.UUID(resp.json()["id"])

    db_session.expire_all()
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "clarifications",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalars().one()
    assert mirror.stale_after is None


@pytest.mark.asyncio
async def test_stale_sweep_stales_legacy_clarification(
    v1_client, db_session, seed_workspace
) -> None:
    """Sweeping an e2e mirror past its TTL also stales the legacy row."""
    from backend.app.db.models.agent_surface import Clarification
    from backend.app.db.models.inbox import InboxItem
    from backend.app.services.inbox.sweep import sweep_stale_inbox_items

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/clarifications",
        headers={"Authorization": f"Bearer {raw}"},
        json={"question": "probe?", "context": {"e2e": True}},
    )
    assert resp.status_code == 201, resp.text
    legacy_id = uuid.UUID(resp.json()["id"])

    db_session.expire_all()
    mirror = (
        await db_session.execute(
            select(InboxItem).where(InboxItem.source_id == legacy_id)
        )
    ).scalars().one()
    mirror_id = mirror.id
    # Backdate creation so ``created_at + stale_after`` is in the past.
    mirror.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.flush()

    swept = await sweep_stale_inbox_items(db_session)
    assert swept >= 1

    db_session.expire_all()
    refreshed_mirror = await db_session.get(InboxItem, mirror_id)
    assert refreshed_mirror.status == "dismissed"
    assert refreshed_mirror.resolution == "stale"
    legacy = await db_session.get(Clarification, legacy_id)
    assert legacy.status == "stale"


@pytest.mark.asyncio
async def test_mirror_clarification_create_does_not_block_on_intake_failure(
    monkeypatch, v1_client, db_session, seed_workspace
) -> None:
    """A blowup inside intake.emit_legacy_record must not 5xx the route."""
    from backend.app.db.models.agent_surface import Clarification
    from backend.app.services.inbox import dual_write

    async def _boom(*_a, **_kw):
        raise RuntimeError("simulated intake failure")

    monkeypatch.setattr(dual_write, "emit_legacy_record", _boom)

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/clarifications",
        headers={"Authorization": f"Bearer {raw}"},
        json={"question": "still creates?"},
    )
    assert resp.status_code == 201, resp.text

    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(Clarification).where(
                Clarification.workspace_id == workspace_id
            )
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].question == "still creates?"


@pytest.mark.asyncio
async def test_mirror_improvement_create_inserts_inbox_row(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.agent_surface import Improvement
    from backend.app.db.models.inbox import InboxItem

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    resp = await v1_client.post(
        f"/v1/workspaces/{workspace_id}/improvements",
        headers={"Authorization": f"Bearer {raw}"},
        json={
            "kind": "refactor",
            "title": "Drop dead code",
            "body": "Found unused module.",
        },
    )
    assert resp.status_code == 201, resp.text
    legacy_id = uuid.UUID(resp.json()["id"])

    db_session.expire_all()
    legacy = await db_session.get(Improvement, legacy_id)
    assert legacy is not None

    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "improvements",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalar_one()
    assert mirror.type == "improvement"
    assert mirror.status == "new"
    assert mirror.title == "Drop dead code"
    assert mirror.payload["kind"] == "refactor"


# ---------------------------------------------------------------------------
# 2. Resolve-side mirroring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mirror_clarification_resolve_updates_inbox_status(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.inbox import InboxItem

    user, raw, workspace = seed_workspace
    workspace_id = workspace.id
    user_id = user.id
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace_id}/clarifications",
            headers={"Authorization": f"Bearer {raw}"},
            json={"question": "answer me?"},
        )
    ).json()
    legacy_id = uuid.UUID(created["id"])

    patched = await v1_client.patch(
        f"/v1/workspaces/{workspace_id}/clarifications/{legacy_id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"answer": "yes"},
    )
    assert patched.status_code == 200

    db_session.expire_all()
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "clarifications",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalar_one()
    assert mirror.status == "resolved"
    assert mirror.resolution == "answered"
    assert mirror.resolved_at is not None
    assert mirror.resolved_by_user_id == user_id


@pytest.mark.asyncio
async def test_mirror_clarification_skip_marks_inbox_dismissed(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.inbox import InboxItem

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace_id}/clarifications",
            headers={"Authorization": f"Bearer {raw}"},
            json={"question": "skip?"},
        )
    ).json()
    legacy_id = uuid.UUID(created["id"])

    skipped = await v1_client.patch(
        f"/v1/workspaces/{workspace_id}/clarifications/{legacy_id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"status": "skipped"},
    )
    assert skipped.status_code == 200

    db_session.expire_all()
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "clarifications",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalar_one()
    assert mirror.status == "dismissed"
    assert mirror.resolution == "dismissed"
    assert mirror.resolved_at is not None


@pytest.mark.asyncio
async def test_mirror_improvement_decline_marks_inbox_dismissed(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.inbox import InboxItem

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace_id}/improvements",
            headers={"Authorization": f"Bearer {raw}"},
            json={"kind": "doc", "title": "x", "body": "y"},
        )
    ).json()
    legacy_id = uuid.UUID(created["id"])

    declined = await v1_client.patch(
        f"/v1/workspaces/{workspace_id}/improvements/{legacy_id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"decision": "declined", "decision_reason": "out of scope"},
    )
    assert declined.status_code == 200

    db_session.expire_all()
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "improvements",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalar_one()
    assert mirror.status == "dismissed"
    assert mirror.resolution == "dismissed"


@pytest.mark.asyncio
async def test_mirror_improvement_defer_marks_inbox_snoozed_with_7d_window(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.agent_surface import Improvement
    from backend.app.db.models.inbox import InboxItem

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace_id}/improvements",
            headers={"Authorization": f"Bearer {raw}"},
            json={"kind": "arch", "title": "later", "body": "soon"},
        )
    ).json()
    legacy_id = uuid.UUID(created["id"])

    deferred = await v1_client.patch(
        f"/v1/workspaces/{workspace_id}/improvements/{legacy_id}",
        headers={"Authorization": f"Bearer {raw}"},
        json={"decision": "deferred"},
    )
    assert deferred.status_code == 200

    db_session.expire_all()
    legacy = await db_session.get(Improvement, legacy_id)
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "improvements",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalar_one()
    assert mirror.status == "snoozed"
    assert mirror.resolution is None
    assert mirror.snoozed_until is not None
    expected = legacy.decided_at + timedelta(days=7)
    assert abs((mirror.snoozed_until - expected).total_seconds()) < 1


# ---------------------------------------------------------------------------
# 3. Idempotency + edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mirror_resolve_idempotent(
    v1_client, db_session, seed_workspace
) -> None:
    from backend.app.db.models.inbox import InboxItem, InboxItemEvent

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace_id}/clarifications",
            headers={"Authorization": f"Bearer {raw}"},
            json={"question": "twice?"},
        )
    ).json()
    legacy_id = uuid.UUID(created["id"])

    for _ in range(2):
        resp = await v1_client.patch(
            f"/v1/workspaces/{workspace_id}/clarifications/{legacy_id}",
            headers={"Authorization": f"Bearer {raw}"},
            json={"answer": "yes"},
        )
        assert resp.status_code == 200

    db_session.expire_all()
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "clarifications",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalar_one()
    assert mirror.status == "resolved"
    assert mirror.resolution == "answered"

    resolve_events = (
        await db_session.execute(
            select(InboxItemEvent).where(
                InboxItemEvent.item_id == mirror.id,
                InboxItemEvent.action == "resolved",
            )
        )
    ).scalars().all()
    assert len(resolve_events) == 1


@pytest.mark.asyncio
async def test_mirror_resolve_returns_false_when_no_mirror_exists(
    v1_client, db_session, seed_workspace
) -> None:
    """Manually drop the mirror, then PATCH; resolve helper logs INFO + skips."""
    from backend.app.db.models.agent_surface import Clarification
    from backend.app.db.models.inbox import InboxItem
    from backend.app.services.inbox.dual_write import (
        mirror_clarification_resolve,
    )

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace_id}/clarifications",
            headers={"Authorization": f"Bearer {raw}"},
            json={"question": "drop me"},
        )
    ).json()
    legacy_id = uuid.UUID(created["id"])

    db_session.expire_all()
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "clarifications",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalar_one()
    await db_session.delete(mirror)
    await db_session.flush()

    legacy_row = await db_session.get(Clarification, legacy_id)
    assert legacy_row is not None
    legacy_row.status = "answered"
    legacy_row.answer = "yes"
    legacy_row.answered_at = datetime.now(timezone.utc)
    await db_session.flush()

    result = await mirror_clarification_resolve(
        db_session,
        clarification=legacy_row,
        actor_user_id=None,
        actor_kind="system",
    )
    assert result is False


# ---------------------------------------------------------------------------
# 4. Audit-trail markers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mirror_create_writes_audit_event_with_legacy_marker(
    v1_client, db_session, seed_workspace
) -> None:
    """The create event payload carries the documented dual-write marker.

    The marker is ``payload.reason='legacy_mirror'`` (written by
    ``intake.emit_legacy_record``); see ``dual_write.py`` module
    docstring for the marker convention.
    """
    from backend.app.db.models.inbox import InboxItem, InboxItemEvent

    _, raw, workspace = seed_workspace
    workspace_id = workspace.id
    created = (
        await v1_client.post(
            f"/v1/workspaces/{workspace_id}/clarifications",
            headers={"Authorization": f"Bearer {raw}"},
            json={"question": "marker test"},
        )
    ).json()
    legacy_id = uuid.UUID(created["id"])

    db_session.expire_all()
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "clarifications",
                InboxItem.source_id == legacy_id,
            )
        )
    ).scalar_one()
    events = (
        await db_session.execute(
            select(InboxItemEvent)
            .where(InboxItemEvent.item_id == mirror.id)
            .where(InboxItemEvent.action == "created")
        )
    ).scalars().all()
    assert len(events) == 1
    assert events[0].payload.get("reason") == "legacy_mirror"
    assert events[0].payload.get("source_table") == "clarifications"


@pytest.mark.asyncio
async def test_mirror_pipeline_clarification_uses_system_actor_event(
    monkeypatch, v1_client, db_session, seed_repo_and_install
) -> None:
    """Pipeline-authored clarifications stamp the create event ``system``."""
    from backend.app.db.models.agent_surface import Clarification
    from backend.app.db.models.inbox import InboxItem, InboxItemEvent

    raw_pat, workspace, _install, repo = seed_repo_and_install
    workspace_id = workspace.id
    repo_id = repo.id
    raw_token, _run_json = await _mint_run_token_for(
        db_session, v1_client, workspace_id, raw_pat, repo_id, monkeypatch
    )

    ingress = await v1_client.post(
        "/v1/clarifications/pipeline",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"question": "pipeline question"},
    )
    assert ingress.status_code == 201, ingress.text

    db_session.expire_all()
    clarification = (
        await db_session.execute(
            select(Clarification).where(
                Clarification.workspace_id == workspace_id
            )
        )
    ).scalar_one()
    mirror = (
        await db_session.execute(
            select(InboxItem).where(
                InboxItem.source_table == "clarifications",
                InboxItem.source_id == clarification.id,
            )
        )
    ).scalar_one()
    create_event = (
        await db_session.execute(
            select(InboxItemEvent)
            .where(InboxItemEvent.item_id == mirror.id)
            .where(InboxItemEvent.action == "created")
        )
    ).scalar_one()
    assert create_event.actor_kind == "system"
    assert create_event.actor_user_id is None
