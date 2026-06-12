"""Autonomy preamble (ELS-244, theses 5+7).

Pins: per-profile distinct action-rights blocks; the same renderer
feeds chat + CI (byte-identical); high-without-knowledge downgrades
to balanced WITH an audit row, never silently runs high; no
tool-native config involved (value enters through the prompt).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.app.db.models.tenancy import AuditLog
from backend.app.services.policies import (
    _AUTONOMY_BLOCKS,
    render_autonomy_preamble,
)


def test_profiles_render_distinct_blocks() -> None:
    blocks = set(_AUTONOMY_BLOCKS.values())
    assert len(blocks) == 3
    assert "HIGH" in _AUTONOMY_BLOCKS["high"]
    assert "self-merge-eligible" in _AUTONOMY_BLOCKS["high"]
    assert "Confirm before destructive" in _AUTONOMY_BLOCKS["balanced"]
    assert "Confirm before any merge" in _AUTONOMY_BLOCKS["conservative"]
    # The hard floor never loosens in the high block.
    assert "CI must be green" in _AUTONOMY_BLOCKS["high"]


@pytest.mark.asyncio
async def test_balanced_renders_directly(db_session, seed_workspace) -> None:
    _, _, ws = seed_workspace  # default balanced
    block = await render_autonomy_preamble(db_session, ws.id)
    assert block == _AUTONOMY_BLOCKS["balanced"]


@pytest.mark.asyncio
async def test_high_without_knowledge_downgrades_with_audit(
    db_session, seed_workspace
) -> None:
    _, _, ws = seed_workspace
    ws.autonomy = "high"
    await db_session.flush()
    block = await render_autonomy_preamble(db_session, ws.id)
    # Rendered rights are BALANCED + the explanatory note.
    assert "Autonomy profile: BALANCED" in block
    assert "configured HIGH but has no knowledge surface" in block
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.workspace_id == ws.id,
                AuditLog.action == "workspace.autonomy.downgraded_effective",
            )
        )
    ).scalars().all()
    assert len(audit) == 1
    assert audit[0].payload == {
        "configured": "high",
        "effective": "balanced",
        "reason": "no_knowledge_surface",
    }


@pytest.mark.asyncio
async def test_high_with_policy_keeps_high(db_session, seed_workspace) -> None:
    from backend.app.db.models.policies import WorkspacePolicy

    _, _, ws = seed_workspace
    ws.autonomy = "high"
    db_session.add(
        WorkspacePolicy(
            workspace_id=ws.id,
            title="No force pushes",
            body="Never force-push.",
            enabled=True,
        )
    )
    await db_session.flush()
    block = await render_autonomy_preamble(db_session, ws.id)
    assert "Autonomy profile: HIGH" in block
    assert "configured HIGH but has no knowledge surface" not in block


def test_automerger_role_references_profile() -> None:
    from pathlib import Path

    md = (
        Path(__file__).resolve().parents[1]
        / "app" / "resources" / "agent_roles" / "auto-merger.md"
    ).read_text()
    assert "Autonomy profile" in md
    for token in ("HIGH", "BALANCED", "CONSERVATIVE"):
        assert token in md
    assert "CI red or incomplete is never mergeable" in md


def test_no_tool_native_config_for_the_dial() -> None:
    """Thesis 5: the dial must not be delivered via .cursorrules /
    claude hooks / codex config — only through the prompt path."""
    from pathlib import Path

    policies_src = (
        Path(__file__).resolve().parents[1] / "app" / "services" / "policies.py"
    ).read_text()
    for needle in (".cursorrules", "CLAUDE.md", "codex.toml", "mcp.json"):
        assert needle not in policies_src
