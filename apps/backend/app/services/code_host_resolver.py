"""Pick a single :class:`CodeHostGateway` for a repo / workspace.

Centralised because the laptop-offline profile
(``SHIP_USE_MEMORY_ADAPTERS=true``) needs to short-circuit before any
GitHub App installation lookup. Today most call-sites still construct
``GitHubCodeHost`` inline — those are a known incremental refactor and
not in scope here. New code should go through this resolver so the
laptop profile keeps working as the codebase migrates.

The returned :class:`ResolvedCodeHost` carries provenance so dashboards
can render "GitHub · org/repo · installation 12345" vs.
"Local memory · org/repo" without a second lookup.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.integrations.gateway.code_host import CodeHostGateway


logger = logging.getLogger(__name__)


CodeHostKind = Literal["github", "gitlab", "azure_devops", "memory"]


@dataclass(frozen=True)
class ResolvedCodeHost:
    kind: CodeHostKind
    gateway: CodeHostGateway


async def resolve_for_workspace(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
) -> ResolvedCodeHost | None:
    """Pick a code-host gateway for ``workspace_id``.

    The laptop profile short-circuits to MemoryCodeHost first; in
    production the (TODO) lookup against ``workspace_repos`` +
    ``installations`` falls through to the existing vendor adapter
    constructors. Today most call-sites construct GitHubCodeHost
    inline and bypass this resolver — the incremental refactor that
    routes them all through here is tracked separately.
    """
    if getattr(settings, "use_memory_adapters", False):
        from backend.app.integrations.local.code_host import MemoryCodeHost

        return ResolvedCodeHost(
            kind="memory",
            gateway=MemoryCodeHost(
                session=session,
                workspace_id=workspace_id,
                console_origin=settings.console_url,
            ),
        )

    # Production code-host resolution lives in the per-call-site
    # constructors today; this branch will be filled in as part of
    # the deferred refactor. Returning None lets callers fall back to
    # their existing inline construction.
    return None
