"""Pick a single :class:`TrackerGateway` for a (workspace, repo) pair.

E14 puts the FSM mapping on the server side: the CLI sends abstract
"transition this ticket from X to Y" calls, the server picks the right
adapter and translates. This helper centralises that pick so every
write-side route resolves the same way.

Preference order:
    1. Per-repo ``Integration`` row (``kind=linear|github|jira``,
       ``repo_id`` set). The wizard binds this when the operator picks
       a tracker for the specific repo.
    2. Workspace-level Linear OAuth row.
    3. GitHub Issues via the App installation on the repo.

Returns ``None`` if no tracker is available — callers map that to a
4xx so the agent can surface ``blocked`` cleanly.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import Integration
from backend.app.integrations.gateway.tracker import TrackerGateway
from backend.app.integrations.github.issues_tracker import GitHubIssuesTracker
from backend.app.integrations.linear.tracker_adapter import LinearTracker


logger = logging.getLogger(__name__)


TrackerKind = Literal["linear", "github_issues", "jira"]


@dataclass(frozen=True)
class ResolvedTracker:
    kind: TrackerKind
    gateway: TrackerGateway
    # Vendor-specific scope hint (Linear team key, GH ``owner/repo``).
    # Adapters fall back to "the only available scope" when unset.
    scope_hint: str | None


async def resolve_for_repo(
    *,
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
) -> ResolvedTracker | None:
    """Pick the tracker the agent should read/write for this repo.

    Per-repo binding wins; workspace Linear comes second; the GitHub
    App installation is the last fallback.
    """
    from backend.app.api.v1.routes.integrations import decrypt  # lazy

    # 1. Per-repo binding row. Carries kind + optional scope hint in
    #    config (e.g. ``{team_key: "ENG"}`` for Linear).
    per_repo = (
        await session.execute(
            select(Integration).where(
                Integration.workspace_id == workspace_id,
                Integration.repo_id == repo_id,
                Integration.kind.in_(("linear", "github", "jira")),
            )
            .order_by(Integration.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if per_repo and per_repo.kind == "linear":
        token = await _decrypt_linear_token(per_repo, decrypt)
        if token is None:
            return None
        scope = (per_repo.config or {}).get("team_key")
        return ResolvedTracker(
            kind="linear", gateway=LinearTracker(token), scope_hint=scope
        )

    # 2. Workspace-level Linear (the OAuth-installed tracker). Used when
    #    no per-repo binding exists or the per-repo row picks ``github``.
    workspace_linear = (
        await session.execute(
            select(Integration).where(
                Integration.workspace_id == workspace_id,
                Integration.repo_id.is_(None),
                Integration.kind == "linear",
            )
            .order_by(Integration.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if per_repo is None and workspace_linear is not None:
        # No per-repo row and we have workspace Linear → use it.
        token = await _decrypt_linear_token(workspace_linear, decrypt)
        if token is not None:
            scope = (workspace_linear.config or {}).get("team_key")
            return ResolvedTracker(
                kind="linear", gateway=LinearTracker(token), scope_hint=scope
            )

    # 3. GitHub Issues via the repo's Ship App installation.
    repo_row = await session.get(WorkspaceRepo, repo_id)
    if repo_row is None or repo_row.workspace_id != workspace_id:
        return None
    install = (
        await session.get(GitHubInstallation, repo_row.installation_id)
        if repo_row.installation_id
        else None
    )
    if install is None:
        return None
    owner, _, name = (repo_row.full_name or "").partition("/")
    if not owner or not name:
        return None
    return ResolvedTracker(
        kind="github_issues",
        gateway=GitHubIssuesTracker(
            installation_id=install.installation_id,
            owner=owner,
            repo=name,
            settings=settings,
        ),
        scope_hint=f"{owner}/{name}",
    )


async def _decrypt_linear_token(row: Integration, decrypt) -> str | None:
    if not row.secret_ciphertext:
        return None
    try:
        return decrypt(row.secret_ciphertext)
    except Exception:  # noqa: BLE001 — log + degrade
        logger.warning("linear token unreadable for integration=%s", row.id)
        return None
