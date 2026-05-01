"""Tests for the Phase-1a knowledge-note harvester (ELS-34 / KB-1).

The harvester walks resolved :class:`Clarification` rows and writes
one :class:`Improvement` row of ``kind='knowledge_note'`` per row.
Phase 1a uses identity extraction (the operator's answer becomes the
note body verbatim) and dedupes by checking ``context->>'source_id'``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

from backend.app.db.models.agent_surface import Clarification, Improvement
from backend.app.services.knowledge_harvest import (
    NOTE_KIND,
    SOURCE_KIND_CLARIFICATION,
    harvest_workspace,
)


@pytest_asyncio.fixture
async def answered_clarification(db_session, seed_workspace):
    """One workspace-scoped clarification with a non-empty answer."""
    _, _, workspace = seed_workspace
    clar = Clarification(
        workspace_id=workspace.id,
        ticket_ref="ELS-27",
        question=(
            "Should every label matching prefix `ready:` be a namespace, "
            "or is there a fixed allowlist?"
        ),
        answer=(
            "Treat any `ready:*` label as one namespace; no fixed "
            "allowlist. Tracker writes only land via the finish endpoint."
        ),
        status="answered",
        source="manual",
        answered_at=datetime.now(timezone.utc),
    )
    db_session.add(clar)
    await db_session.flush()
    return workspace, clar


@pytest.mark.asyncio
async def test_harvest_creates_one_note_per_answered_clarification(
    db_session, answered_clarification
):
    workspace, clar = answered_clarification
    report = await harvest_workspace(db_session, workspace_id=workspace.id)

    assert report.inspected == 1
    assert report.created == 1
    assert report.skipped_duplicate == 0
    assert report.skipped_no_answer == 0

    rows = (
        await db_session.execute(
            select(Improvement).where(Improvement.workspace_id == workspace.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    note = rows[0]
    assert note.kind == NOTE_KIND
    assert note.body == clar.answer
    assert note.title.startswith("ELS-27: ")
    assert note.context["source_kind"] == SOURCE_KIND_CLARIFICATION
    assert note.context["source_id"] == str(clar.id)
    assert note.context["ticket_ref"] == "ELS-27"
    assert note.context["routed_bucket_id"] is None
    assert note.context["extractor"] == "identity_v1"


@pytest.mark.asyncio
async def test_harvest_is_idempotent(db_session, answered_clarification):
    workspace, _ = answered_clarification
    first = await harvest_workspace(db_session, workspace_id=workspace.id)
    second = await harvest_workspace(db_session, workspace_id=workspace.id)

    assert first.created == 1
    assert second.created == 0
    assert second.skipped_duplicate == 1

    count = (
        await db_session.execute(
            select(Improvement).where(Improvement.kind == NOTE_KIND)
        )
    ).scalars().all()
    assert len(count) == 1


@pytest.mark.asyncio
async def test_harvest_skips_open_clarifications(db_session, seed_workspace):
    """Only ``status='answered'`` rows are eligible."""
    _, _, workspace = seed_workspace
    db_session.add(
        Clarification(
            workspace_id=workspace.id,
            ticket_ref="ELS-99",
            question="Still open?",
            answer=None,
            status="open",
            source="manual",
        )
    )
    await db_session.flush()

    report = await harvest_workspace(db_session, workspace_id=workspace.id)
    assert report.inspected == 0
    assert report.created == 0


@pytest.mark.asyncio
async def test_harvest_skips_answered_with_blank_answer(
    db_session, seed_workspace
):
    """``status='answered'`` but empty/whitespace ``answer`` is a noop."""
    _, _, workspace = seed_workspace
    db_session.add(
        Clarification(
            workspace_id=workspace.id,
            ticket_ref="ELS-100",
            question="Empty answer?",
            answer="   ",
            status="answered",
            source="manual",
            answered_at=datetime.now(timezone.utc),
        )
    )
    await db_session.flush()

    report = await harvest_workspace(db_session, workspace_id=workspace.id)
    assert report.inspected == 1
    assert report.created == 0
    assert report.skipped_no_answer == 1


@pytest.mark.asyncio
async def test_harvest_scopes_to_workspace(
    db_session, answered_clarification, seed_other_workspace
):
    """A clarification in workspace A is invisible to workspace B's harvest."""
    workspace_a, _ = answered_clarification
    _, _, workspace_b = seed_other_workspace

    report_b = await harvest_workspace(db_session, workspace_id=workspace_b.id)
    assert report_b.inspected == 0
    assert report_b.created == 0

    # Sanity: A still sees its own row.
    report_a = await harvest_workspace(db_session, workspace_id=workspace_a.id)
    assert report_a.inspected == 1
    assert report_a.created == 1


@pytest_asyncio.fixture
async def seed_other_workspace(db_session, seed_workspace):
    """A second workspace in the same org so we can exercise tenant isolation."""
    from backend.app.db.models.tenancy import Workspace, WorkspaceMember

    user, _, ws_a = seed_workspace
    other = Workspace(
        org_id=ws_a.org_id,
        slug="kb-other-" + uuid.uuid4().hex[:6],
        name="KB other",
    )
    db_session.add(other)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=other.id,
            user_id=user.id,
            role="owner",
            answer_specialist_slugs=["*"],
        )
    )
    await db_session.flush()
    return user, None, other
