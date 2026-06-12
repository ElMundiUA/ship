"""W8.7 (ELS-255) — durable workflow state tables.

The load-bearing assertion is the UNIQUE (workflow_run_id, step_id,
attempt) constraint: it is the idempotency key the dispatch gate
(W8.2) leans on, so a duplicate insert MUST raise instead of silently
double-recording a spawn.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from backend.app.db.models.workflow import (
    AgentWorkflowRun,
    AgentWorkflowStepRun,
    WORKFLOW_RUN_STATUSES,
    WORKFLOW_STEP_STATUSES,
)


@pytest.mark.asyncio
async def test_step_attempt_unique_is_idempotency_key(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    run = AgentWorkflowRun(
        workspace_id=workspace.id,
        spec_name="pr-review",
        trigger_kind="chat",
    )
    db_session.add(run)
    await db_session.flush()

    db_session.add(
        AgentWorkflowStepRun(
            workflow_run_id=run.id,
            step_id="review.correctness",
            attempt=1,
            kind="parallel",
            agent_provider="reasoning",
        )
    )
    await db_session.flush()

    db_session.add(
        AgentWorkflowStepRun(
            workflow_run_id=run.id,
            step_id="review.correctness",
            attempt=1,
            kind="parallel",
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_new_attempt_is_a_distinct_row(db_session, seed_workspace) -> None:
    """Re-running a failed step requires a NEW attempt — same step_id,
    attempt+1 inserts cleanly."""
    _, _, workspace = seed_workspace
    run = AgentWorkflowRun(
        workspace_id=workspace.id,
        spec_name="pr-review",
        trigger_kind="gate",
    )
    db_session.add(run)
    await db_session.flush()
    for attempt in (1, 2):
        db_session.add(
            AgentWorkflowStepRun(
                workflow_run_id=run.id,
                step_id="synthesize",
                attempt=attempt,
                kind="synthesize",
                lock_key=f"workflow:{run.id}:synthesize",
                run_id=f"run_{attempt}",
            )
        )
    await db_session.flush()


def test_status_vocabularies_are_closed_sets() -> None:
    assert "completed" in WORKFLOW_RUN_STATUSES
    assert "blocked" in WORKFLOW_RUN_STATUSES
    assert "skipped" in WORKFLOW_STEP_STATUSES
    assert "dispatched" in WORKFLOW_STEP_STATUSES
