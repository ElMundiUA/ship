"""Workspace prose-rule policies service (Workspace policy injection).

This module is the **shared rendering layer** for the prose-rule
:class:`WorkspacePolicy` model. Both injection sites consume it:

- :func:`backend.app.services.agent.topic.TopicService.assemble_messages`
  appends ``render_policies_preamble`` as a system message right
  after the base agent prompt so the LLM treats it as a hard rule.
- The ``GET /v1/pipelines/runs/{run_id}/policies-preamble`` endpoint
  serves the same string to ``shipctl run``, which prepends it to
  the pattern markdown printed on stdout.

Keeping rendering centralised guarantees the chat agent and the
GitHub-Actions agent see *byte-identical* preambles for the same
workspace state, which matters for reproducibility and operator
trust ("the rule I added on the policies page is the same one the
agent in CI sees").
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import asc, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.policies import WorkspacePolicy


async def list_enabled_policies(
    session: AsyncSession, workspace_id: uuid.UUID
) -> list[WorkspacePolicy]:
    """Return enabled policies for ``workspace_id`` in render order.

    Order: ``sort_order`` ascending, ties broken by ``created_at``
    so re-orders are stable across requests but new rules slot to
    the bottom of their priority bucket without explicit sort_order
    edits.
    """
    rows = (
        (
            await session.execute(
                select(WorkspacePolicy)
                .where(
                    WorkspacePolicy.workspace_id == workspace_id,
                    WorkspacePolicy.enabled.is_(True),
                )
                .order_by(
                    asc(WorkspacePolicy.sort_order),
                    asc(WorkspacePolicy.created_at),
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def format_policies_preamble(
    policies: Sequence[WorkspacePolicy],
) -> str | None:
    """Render policies as a single markdown block, or ``None``.

    Returns ``None`` when the workspace has no enabled policies so
    callers can skip emitting an empty system message / stdout
    block instead of polluting the prompt with a "no rules yet"
    placeholder. Each policy renders as an ``## <title>`` heading
    followed by the body verbatim — bodies are user-authored
    markdown and we trust them rather than escaping.
    """
    if not policies:
        return None
    lines: list[str] = [
        "# Workspace policies",
        "",
        (
            "These standing rules apply to all work in this workspace. "
            "Follow them strictly."
        ),
        "",
    ]
    for policy in policies:
        lines.append(f"## {policy.title}")
        lines.append("")
        body = policy.body.rstrip()
        if body:
            lines.append(body)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


async def render_policies_preamble(
    session: AsyncSession, workspace_id: uuid.UUID
) -> str | None:
    """Convenience wrapper: load + format in one call."""
    policies = await list_enabled_policies(session, workspace_id)
    return format_policies_preamble(policies)


__all__ = [
    "format_policies_preamble",
    "list_enabled_policies",
    "render_policies_preamble",
]
