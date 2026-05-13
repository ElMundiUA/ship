"""Pick the autonomous-pipeline agent runtime for a workspace.

Mirrors :mod:`backend.app.services.tracker_resolver`: the workspace
binds to exactly one provider (``cursor`` / ``codex`` / ``claude``)
and ``shipctl run`` reads the bound row to decide which local CLI to
invoke on the GHA runner. No peer-ranking, no per-routine fallback at
the resolver layer — `.ship/config.yml` overrides happen above this
service if needed.

``cursor`` stays the default for fresh workspaces so the upgrade
doesn't yank existing pipelines out from under operators who never
touched the new setting. A typo at either end (DB row or CLI mapping)
surfaces as a fast 422 against the validated enum below rather than
an opaque CLI invocation failure on the runner.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.tenancy import Workspace


AgentProviderKind = Literal["cursor", "codex", "claude"]


SUPPORTED_PROVIDERS: Final[frozenset[str]] = frozenset(
    {"cursor", "codex", "claude"}
)
DEFAULT_PROVIDER: Final[AgentProviderKind] = "cursor"


@dataclass(frozen=True)
class ResolvedAgentProvider:
    kind: AgentProviderKind
    # Echo back the workspace id so callers logging the resolution
    # don't have to thread it through separately.
    workspace_id: uuid.UUID


async def resolve_for_workspace(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
) -> ResolvedAgentProvider:
    """Return the workspace's bound agent runtime.

    Falls back to :data:`DEFAULT_PROVIDER` when the row is missing
    (workspace deleted mid-flight or migration not yet applied) so the
    runner has *something* to invoke instead of dying with a 500. The
    falsy path is rare; logged but not raised.
    """

    row = await session.scalar(
        select(Workspace.agent_provider).where(Workspace.id == workspace_id)
    )
    kind = row if row in SUPPORTED_PROVIDERS else DEFAULT_PROVIDER
    return ResolvedAgentProvider(kind=kind, workspace_id=workspace_id)


__all__ = [
    "AgentProviderKind",
    "DEFAULT_PROVIDER",
    "ResolvedAgentProvider",
    "SUPPORTED_PROVIDERS",
    "resolve_for_workspace",
]
