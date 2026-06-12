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

from sqlalchemy import asc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.policies import WorkspacePolicy


async def list_enabled_policies(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    role_slug: str | None = None,
) -> list[WorkspacePolicy]:
    """Return enabled policies for ``workspace_id`` in render order.

    Order: ``sort_order`` ascending, ties broken by ``created_at``
    so re-orders are stable across requests but new rules slot to
    the bottom of their priority bucket without explicit sort_order
    edits.

    ``role_slug`` filters to policies that target a specific role
    plus all global policies (``applies_to_roles IS NULL`` or empty
    array). When ``None``, only global policies are returned —
    that's the safe default for callers that don't know the role
    yet (legacy ``shipctl run`` clients pre-`?role=` query param).
    Both NULL and ``cardinality=0`` count as global so admin edits
    that empty the array don't accidentally silence a rule.
    """
    is_global = or_(
        WorkspacePolicy.applies_to_roles.is_(None),
        func.cardinality(WorkspacePolicy.applies_to_roles) == 0,
    )
    if role_slug is None:
        scope_clause = is_global
    else:
        scope_clause = or_(
            is_global,
            WorkspacePolicy.applies_to_roles.any(role_slug),
        )
    rows = (
        (
            await session.execute(
                select(WorkspacePolicy)
                .where(
                    WorkspacePolicy.workspace_id == workspace_id,
                    WorkspacePolicy.enabled.is_(True),
                    scope_clause,
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
    session: AsyncSession,
    workspace_id: uuid.UUID,
    role_slug: str | None = None,
) -> str | None:
    """Convenience wrapper: load + format in one call.

    ``role_slug`` is forwarded to :func:`list_enabled_policies` —
    pass the agent's role slug (``"developer"``, ``"navigator"``,
    …) to include role-scoped policies in the preamble alongside
    globals; pass ``None`` for global-only.
    """
    policies = await list_enabled_policies(
        session, workspace_id, role_slug=role_slug
    )
    return format_policies_preamble(policies)


__all__ = [
    "format_policies_preamble",
    "list_enabled_policies",
    "render_autonomy_preamble",
    "render_policies_preamble",
]


# ---------------------------------------------------------------------------
# Autonomy preamble (ELS-244, thesis 7) — rendered through the SAME
# module as the policies preamble so Navigator chat and shipctl run
# emit byte-identical text (thesis 5: value enters through the prompt,
# never tool-native config).
# ---------------------------------------------------------------------------

_AUTONOMY_BLOCKS: dict[str, str] = {
    "high": (
        "# Autonomy profile: HIGH\n"
        "\n"
        "Action rights for this workspace (opted in by the operator):\n"
        "\n"
        "- Skip optional approval pauses; proceed when CI is green.\n"
        "- Broader edits are allowed: refactor adjacent code when it\n"
        "  serves the task; keep commits reviewable.\n"
        "- Self-pick follow-up work and create tickets / decompose\n"
        "  without asking for confirmation first.\n"
        "- Your PRs are self-merge-eligible once the server gate sees\n"
        "  green CI (the gate still verifies — never claim falsely).\n"
        "\n"
        "Hard limits that NEVER loosen: CI must be green; the FSM gates\n"
        "and dispatch limits (lease/cap/cascade) apply unchanged; no\n"
        "force-pushes; no bypassing review stages by editing tracker\n"
        "state directly.\n"
    ),
    "balanced": (
        "# Autonomy profile: BALANCED\n"
        "\n"
        "Action rights for this workspace:\n"
        "\n"
        "- Proceed autonomously through the normal FSM stages.\n"
        "- Confirm before destructive or structural operations\n"
        "  (schema changes, deletions, dependency major bumps).\n"
        "- Ticket creation / decomposition: propose first when the\n"
        "  scope is new; proceed when it directly serves the current\n"
        "  ticket.\n"
        "- Merges require the review stage's approval as usual.\n"
    ),
    "conservative": (
        "# Autonomy profile: CONSERVATIVE\n"
        "\n"
        "Action rights for this workspace:\n"
        "\n"
        "- Keep edits narrow: only what the ticket explicitly asks.\n"
        "- Confirm before any merge, structural change, or new ticket\n"
        "  creation.\n"
        "- Prefer asking a clarification over assuming intent.\n"
    ),
}


async def _has_knowledge_surface(
    session: AsyncSession, workspace_id: uuid.UUID
) -> bool:
    """High autonomy only works WITH rich context (founder decision).

    'Non-empty knowledge surface' = at least one enabled policy OR at
    least one knowledge-base chunk for the workspace."""
    policies = await list_enabled_policies(session, workspace_id)
    if policies:
        return True
    from sqlalchemy import func, select as sa_select

    from backend.app.db.models.agent_memory import KbChunk

    chunks = await session.scalar(
        sa_select(func.count(KbChunk.id)).where(
            KbChunk.workspace_id == workspace_id
        )
    )
    return bool(chunks)


async def render_autonomy_preamble(
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> str:
    """Render the workspace's autonomy action-rights block.

    Coupling guard: ``high`` on a workspace with NO knowledge surface
    is downgraded to ``balanced`` for the rendered prompt, with an
    audited warning — high rights + thin context is the chaos
    combination the founder vetoed. The downgrade is per-render
    (effective), not persisted: fixing the knowledge surface restores
    high on the next render with no further action.
    """
    from backend.app.services.agent_provider_resolver import (
        resolve_autonomy_for_workspace,
    )

    profile = await resolve_autonomy_for_workspace(
        session=session, workspace_id=workspace_id
    )
    effective = profile
    if profile == "high" and not await _has_knowledge_surface(
        session, workspace_id
    ):
        effective = "balanced"
        from backend.app.db.models.tenancy import AuditLog

        session.add(
            AuditLog(
                workspace_id=workspace_id,
                actor_user_id=None,
                actor_token_id=None,
                action="workspace.autonomy.downgraded_effective",
                target_kind="workspace",
                target_id=str(workspace_id),
                payload={
                    "configured": "high",
                    "effective": "balanced",
                    "reason": "no_knowledge_surface",
                },
            )
        )
    block = _AUTONOMY_BLOCKS[effective]
    if effective != profile:
        block += (
            "\n_Note: this workspace is configured HIGH but has no "
            "knowledge surface (no enabled policies, empty knowledge "
            "base) — rights are rendered as BALANCED until context "
            "exists._\n"
        )
    return block
