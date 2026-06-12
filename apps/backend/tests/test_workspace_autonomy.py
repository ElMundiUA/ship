"""Workspace autonomy dial (ELS-221, thesis 7).

Covers the resolver default (``balanced``), reading an explicit
profile, the CHECK constraint at the DB layer, and the fallback for
out-of-enum values. The dial is agent action-rights only — the
control-plane invariant test lives in Phase 2 (ELS-227).
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError


@pytest.mark.asyncio
async def test_autonomy_defaults_to_balanced(db_session, seed_workspace) -> None:
    from backend.app.services.agent_provider_resolver import (
        DEFAULT_AUTONOMY,
        resolve_autonomy_for_workspace,
    )

    _, _, workspace = seed_workspace
    assert workspace.autonomy == "balanced"
    profile = await resolve_autonomy_for_workspace(
        session=db_session, workspace_id=workspace.id
    )
    assert profile == DEFAULT_AUTONOMY == "balanced"


@pytest.mark.asyncio
async def test_autonomy_reads_explicit_profile(db_session, seed_workspace) -> None:
    from backend.app.services.agent_provider_resolver import (
        resolve_autonomy_for_workspace,
    )

    _, _, workspace = seed_workspace
    workspace.autonomy = "high"
    await db_session.flush()

    profile = await resolve_autonomy_for_workspace(
        session=db_session, workspace_id=workspace.id
    )
    assert profile == "high"


@pytest.mark.asyncio
async def test_autonomy_missing_workspace_falls_back(db_session) -> None:
    import uuid

    from backend.app.services.agent_provider_resolver import (
        resolve_autonomy_for_workspace,
    )

    profile = await resolve_autonomy_for_workspace(
        session=db_session, workspace_id=uuid.uuid4()
    )
    assert profile == "balanced"


@pytest.mark.asyncio
async def test_autonomy_check_constraint_rejects_garbage(
    db_session, seed_workspace
) -> None:
    _, _, workspace = seed_workspace
    workspace.autonomy = "yolo"
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()
