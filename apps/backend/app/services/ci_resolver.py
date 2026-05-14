"""Pick a single :class:`CIGateway` for a workspace.

Mirrors :mod:`code_host_resolver`. Laptop profile short-circuits to
the in-Postgres MemoryCi; production resolution against GitHub
Actions / GitLab CI / ADO Pipelines is performed inline at the call
sites today and gradually migrates here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.integrations.gateway.ci import CIGateway


CiKind = Literal["github_actions", "gitlab_ci", "azure_pipelines", "memory"]


@dataclass(frozen=True)
class ResolvedCi:
    kind: CiKind
    gateway: CIGateway


async def resolve_for_workspace(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
) -> ResolvedCi | None:
    if getattr(settings, "use_memory_adapters", False):
        from backend.app.integrations.local.ci import MemoryCi

        return ResolvedCi(
            kind="memory",
            gateway=MemoryCi(
                session=session,
                workspace_id=workspace_id,
                console_origin=settings.console_url,
            ),
        )
    return None
