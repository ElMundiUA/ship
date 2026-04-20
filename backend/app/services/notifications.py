"""Workspace notification helpers (A4 + A5).

Thin write-side facade around :class:`WorkspaceNotification` so the
webhook handlers don't have to grow SQLAlchemy imports and dedupe
logic inline. Two public entry points:

- :func:`record_pr_merged_notification` — A4's "PR merged" banner.
- :func:`record_self_heal_notification` — A5's "we dispatched
  self-heal / we wanted to but couldn't" banner.

Both are *best-effort*: if the same dedupe key already exists (e.g.
GitHub replayed the webhook after a transient timeout) the helper
treats it as a no-op and returns ``None``. That's why they take an
:class:`AsyncSession` and do the work themselves rather than making
the caller worry about integrity errors.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.notifications import WorkspaceNotification


logger = logging.getLogger("ship.notifications")


async def _upsert_notification(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    title: str,
    body: str | None,
    href: str | None,
    payload: dict[str, Any],
    dedupe_key: str | None,
) -> WorkspaceNotification | None:
    """Insert a notification iff ``dedupe_key`` isn't already taken.

    When a caller passes a ``dedupe_key`` and a row with the same
    ``(workspace_id, dedupe_key)`` already exists we return ``None``
    instead of raising — the typical caller is a webhook replay
    handler and the "another event already minted this banner"
    branch must be a silent happy path.
    """
    if dedupe_key is not None:
        existing = (
            await session.execute(
                select(WorkspaceNotification).where(
                    WorkspaceNotification.workspace_id == workspace_id,
                    WorkspaceNotification.dedupe_key == dedupe_key,
                )
            )
        ).scalars().first()
        if existing is not None:
            # Leave the row alone — the dashboard already shows it (or
            # the user already dismissed it and re-showing would be
            # annoying). We deliberately DO NOT resurrect dismissed rows
            # here; "merged twice" (force-push + re-merge) is a degenerate
            # case we tolerate by staying quiet.
            return None

    row = WorkspaceNotification(
        workspace_id=workspace_id,
        kind=kind,
        title=title[:512],
        body=(body or None),
        href=(href[:1024] if href else None),
        payload=payload or {},
        dedupe_key=dedupe_key,
    )
    session.add(row)
    await session.flush()
    return row


async def record_pr_merged_notification(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pr_external_id: int,
    pr_number: int,
    repo_full_name: str,
    title: str,
    html_url: str,
    author: str | None,
) -> WorkspaceNotification | None:
    """Mint the A4 "Welcome back — your PR merged" banner.

    Keyed on ``pr_merged:<external_id>`` so a GitHub webhook replay
    doesn't stack two banners for the same merge.
    """
    body_bits = [f"{repo_full_name} · #{pr_number}"]
    if author:
        body_bits.append(f"by {author}")
    body_bits.append("— Ship noticed and is ready whenever you are.")
    body = " ".join(body_bits)
    return await _upsert_notification(
        session,
        workspace_id=workspace_id,
        kind="pr_merged",
        title=f"PR merged: {title[:400]}",
        body=body,
        href=html_url,
        payload={
            "pr_external_id": pr_external_id,
            "pr_number": pr_number,
            "repo_full_name": repo_full_name,
        },
        dedupe_key=f"pr_merged:{pr_external_id}",
    )


async def record_self_heal_notification(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    failed_run_external_id: int,
    repo_full_name: str,
    failed_workflow_name: str,
    failed_run_url: str | None,
    healing_run_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> WorkspaceNotification | None:
    """Mint the A5 "self-heal dispatched / skipped" banner.

    ``kind`` must be one of ``self_heal_dispatched`` or
    ``self_heal_skipped``. The dedupe key is the *failed* run id so
    we only ever surface one banner per failing run — even if the
    user toggles the self-heal pipeline off and on mid-flight and
    the webhook replays.
    """
    assert kind in {"self_heal_dispatched", "self_heal_skipped"}, kind
    if kind == "self_heal_dispatched":
        title = f"Self-heal launched for {repo_full_name}"
        body = (
            f"{failed_workflow_name} failed — Ship auto-dispatched "
            "the self-heal lane. Watch the pipeline card for the "
            "outcome."
        )
    else:
        title = f"Self-heal skipped for {repo_full_name}"
        body = (
            f"{failed_workflow_name} failed but self-heal didn't run"
            + (f": {reason}" if reason else ".")
            + " Enable the self_heal pipeline (and install its "
            "workflow if needed) so Ship can step in next time."
        )
    href = failed_run_url
    payload: dict[str, Any] = {
        "failed_run_external_id": failed_run_external_id,
        "failed_workflow_name": failed_workflow_name,
        "repo_full_name": repo_full_name,
    }
    if healing_run_id is not None:
        payload["healing_run_id"] = str(healing_run_id)
    if reason:
        payload["reason"] = reason
    return await _upsert_notification(
        session,
        workspace_id=workspace_id,
        kind=kind,
        title=title,
        body=body,
        href=href,
        payload=payload,
        dedupe_key=f"self_heal:{failed_run_external_id}",
    )


__all__ = [
    "record_pr_merged_notification",
    "record_self_heal_notification",
]
