"""Resolve a Firecrawl API key for the current workspace.

Two sources, in order:

1. **Workspace integration row** — ``Integration(kind='firecrawl', repo_id IS NULL)``.
   The encrypted ``secret_ciphertext`` is the API key. Operators add
   this via the standard ``PUT /v1/integrations/firecrawl`` flow (or
   the Settings → Integrations UI once Phase 4 lands).

2. **Environment fallback** — ``FIRECRAWL_API_KEY``. Lets dev /
   single-tenant installs work without a DB row, and lets a deploy
   pin a shared key when multi-tenant separation isn't a goal.

If neither is set the tool surfaces a structured ``firecrawl_unconfigured``
error to the LLM — no exception, no 5xx. The agent can then surface
that to the operator (``"web tools need a Firecrawl API key — add
one in Settings → Integrations"``).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.tenancy import Integration


_FIRECRAWL_KIND = "firecrawl"
_ENV_KEY = "FIRECRAWL_API_KEY"


@dataclass(frozen=True)
class ResolvedFirecrawlKey:
    """Result of :func:`resolve_firecrawl_key` — opaque carrier."""

    api_key: str
    source: str  # "workspace_integration" | "env"


async def resolve_firecrawl_key(
    session: AsyncSession, workspace_id: uuid.UUID
) -> ResolvedFirecrawlKey | None:
    """Return the workspace's Firecrawl key + provenance, or ``None``
    when neither the integration row nor the env var is set."""
    # Workspace-scoped row wins. The integrations table stores the
    # ciphertext; ``decrypt`` is imported lazily because the
    # encryption module pulls heavyweight cryptography deps at import
    # time, and we want this resolver cheap to import from agent
    # tools.
    row = (
        await session.execute(
            select(Integration).where(
                Integration.workspace_id == workspace_id,
                Integration.kind == _FIRECRAWL_KIND,
                Integration.repo_id.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is not None and row.secret_ciphertext:
        from backend.app.security.encryption import decrypt

        try:
            api_key = decrypt(row.secret_ciphertext)
        except Exception:  # noqa: BLE001 — defensive against rotated kek
            api_key = None
        if api_key:
            return ResolvedFirecrawlKey(api_key=api_key, source="workspace_integration")

    env_key = (os.environ.get(_ENV_KEY) or "").strip()
    if env_key:
        return ResolvedFirecrawlKey(api_key=env_key, source="env")

    return None


__all__ = ["ResolvedFirecrawlKey", "resolve_firecrawl_key"]
