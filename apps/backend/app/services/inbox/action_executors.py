"""Real-action executors for inbox letter ``action_items``.

The legacy ``kind=choice`` semantics ("operator picked X → server
posts a Linear comment with X's label") was fine for clarification
flows where the answer IS just a comment. Self-heal blocker letters
(``runner_fail_loop`` / ``dev_not_converging`` / ``blocked_cascade_
exhausted``) need real side effects: cancel the ticket, force-merge
the PR, re-fire the dev stage with an operator-provided hint, snooze
the row. Each of those collapses to a single executor here.

Operator feedback driving the design (2026-05-20): "press button →
Ship does it" instead of "press button → comment posted, now go
finish the work in Linear / GitHub by hand."

Each executor:
  - receives the ``InboxItem`` + workspace id + optional freeform
    note from the operator,
  - performs the side effect via the workspace's bound tracker /
    GitHub App install,
  - returns a JSON-safe dict the ``/decide`` endpoint records in
    ``side_effects[]`` for the audit trail.

Failures log + return ``result="error"`` so a single broken
executor doesn't 500 the whole disposition; the operator sees the
error in the row's decided payload and can retry or escalate.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.integrations import GitHubInstallation
from backend.app.db.models.pipelines import PullRequest
from backend.app.integrations.gateway.tracker import TicketRef
from backend.app.services.tracker_resolver import resolve_for_workspace


log = logging.getLogger(__name__)


_PR_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")


async def run_action_executor(
    session: AsyncSession,
    *,
    workspace_id,
    item: InboxItem,
    executor: str,
    freeform: str | None,
) -> dict[str, Any]:
    """Dispatch to the named executor.

    Unknown executors return ``{"result": "unknown_executor"}`` —
    forward-compatible with future executors emitted by newer
    agents.
    """
    handler = _EXECUTORS.get(executor)
    if handler is None:
        return {"result": "unknown_executor", "executor": executor}
    try:
        return await handler(session, workspace_id, item, freeform)
    except Exception as exc:  # noqa: BLE001 — single executor never 500s
        log.warning(
            "inbox action executor %s failed item=%s err=%s",
            executor, item.id, exc,
        )
        return {"result": "error", "error": str(exc)[:200]}


async def _snooze_24h(
    session: AsyncSession, workspace_id, item: InboxItem, freeform: str | None
) -> dict[str, Any]:
    """Set ``snoozed_until = NOW() + 24h`` so the row drops out of
    "needs attention" until tomorrow. fsm_self_heal also honours
    the inbox letter cooldown (see fsm_self_heal.py runner-fail
    cooldown) so a snooze on the letter also pauses re-dispatching
    of the underlying ticket."""
    item.snoozed_until = datetime.now(timezone.utc) + timedelta(hours=24)
    return {"result": "snoozed_24h"}


async def _cancel_ticket(
    session: AsyncSession, workspace_id, item: InboxItem, freeform: str | None
) -> dict[str, Any]:
    """Operator says "this work isn't happening". Move the Linear
    ticket to ``Canceled``, comment-post the operator's optional
    note (if any), close the associated PR if we can find one."""
    ticket_ref = (item.payload or {}).get("ticket_ref")
    if not ticket_ref:
        return {"result": "skipped_no_ticket"}
    settings = get_settings()
    resolved = await resolve_for_workspace(
        session=session, settings=settings, workspace_id=workspace_id,
    )
    if resolved is None:
        return {"result": "skipped_no_tracker"}
    ref = TicketRef(
        kind=resolved.kind, workspace_hint=None, id=str(ticket_ref),
    )
    actions: list[str] = []
    if freeform:
        try:
            await resolved.gateway.comment(
                ref,
                body=(
                    f"**[Operator cancelled]** {freeform}\n\n"
                    f"_Ticket moved to Canceled via Inbox._"
                ),
            )
            actions.append("comment")
        except Exception as exc:  # noqa: BLE001
            log.info(
                "cancel_ticket: comment failed ws=%s ticket=%s: %s",
                workspace_id, ticket_ref, exc,
            )
    try:
        await resolved.gateway.transition(ref, to_state="Canceled")
        actions.append("transition:Canceled")
    except Exception as exc:  # noqa: BLE001
        log.info(
            "cancel_ticket: transition failed ws=%s ticket=%s: %s",
            workspace_id, ticket_ref, exc,
        )
        return {"result": "error", "error": f"transition: {exc}", "actions": actions}

    # Best-effort PR close.
    closed = await _close_pr_for_ticket(
        session, workspace_id, ticket_ref,
        comment=(
            f"Operator cancelled ticket {ticket_ref}. "
            f"Closing this PR — see Linear for context."
            + (f"\n\nNote: {freeform}" if freeform else "")
        ),
    )
    if closed:
        actions.append(f"pr_closed:#{closed}")
    return {"result": "cancelled", "actions": actions}


async def _force_merge(
    session: AsyncSession, workspace_id, item: InboxItem, freeform: str | None
) -> dict[str, Any]:
    """Operator overrides the reviewer — squash-merge the PR. Uses
    the workspace's GitHub App token (same path the auto-merger
    uses). The operator's freeform note (if any) lands as a PR
    comment first so the audit trail records the override
    rationale."""
    pr_info = await _resolve_pr_for_item(session, workspace_id, item)
    if pr_info is None:
        return {"result": "skipped_no_pr"}
    owner, repo, number = pr_info

    install = await _resolve_install_for_repo(
        session, workspace_id, owner, repo,
    )
    if install is None:
        return {"result": "skipped_no_install"}

    from backend.app.integrations.github.app_auth import (
        get_installation_token,
    )
    import httpx
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        token = await get_installation_token(
            install.installation_id, settings=settings, client=client,
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        # Operator's note → PR comment.
        if freeform:
            await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments",
                headers=headers,
                json={
                    "body": (
                        f"**[Operator override — force-merge]** {freeform}\n\n"
                        f"_Reviewer's blockers acknowledged but overridden via Ship inbox._"
                    ),
                },
            )
        # Squash-merge.
        merge_resp = await client.put(
            f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/merge",
            headers=headers,
            json={"merge_method": "squash"},
        )
        if merge_resp.status_code >= 400:
            return {
                "result": "error",
                "error": f"merge http {merge_resp.status_code}: "
                         f"{merge_resp.text[:200]}",
                "owner": owner, "repo": repo, "number": number,
            }
    # Move Linear ticket to Done if we have one.
    ticket_ref = (item.payload or {}).get("ticket_ref")
    if ticket_ref:
        resolved = await resolve_for_workspace(
            session=session, settings=settings, workspace_id=workspace_id,
        )
        if resolved is not None:
            try:
                await resolved.gateway.transition(
                    TicketRef(
                        kind=resolved.kind, workspace_hint=None, id=str(ticket_ref),
                    ),
                    to_state="Done",
                )
            except Exception:  # noqa: BLE001
                pass
    return {
        "result": "merged",
        "owner": owner, "repo": repo, "number": number,
    }


async def _redispatch_dev(
    session: AsyncSession, workspace_id, item: InboxItem, freeform: str | None
) -> dict[str, Any]:
    """Operator's hint → Linear comment + bump the ticket back to
    ``dev_implementation`` so the next cron tick re-fires dev with
    the hint as fresh context. We don't fire ``workflow_dispatch``
    directly — we let the picker do it on the next tick to keep the
    audit trail consistent with normal cascades."""
    ticket_ref = (item.payload or {}).get("ticket_ref")
    if not ticket_ref:
        return {"result": "skipped_no_ticket"}
    settings = get_settings()
    resolved = await resolve_for_workspace(
        session=session, settings=settings, workspace_id=workspace_id,
    )
    if resolved is None:
        return {"result": "skipped_no_tracker"}
    ref = TicketRef(
        kind=resolved.kind, workspace_hint=None, id=str(ticket_ref),
    )
    if freeform:
        try:
            await resolved.gateway.comment(
                ref,
                body=(
                    f"**[Operator hint to developer]** {freeform}\n\n"
                    f"_Re-dispatching dev_implementation via Ship inbox. "
                    f"Use the hint above to land the reviewer's blocker._"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            log.info(
                "redispatch_dev: comment failed ws=%s ticket=%s: %s",
                workspace_id, ticket_ref, exc,
            )
    # Transition to dev_implementation breadcrumb. The tracker's
    # ``transition`` knows the FSM map.
    try:
        await resolved.gateway.transition(ref, to_state="dev_implementation")
        return {"result": "redispatched", "stage": "dev_implementation"}
    except Exception as exc:  # noqa: BLE001
        return {"result": "error", "error": f"transition: {exc}"}


_EXECUTORS = {
    "snooze_24h": _snooze_24h,
    "cancel_ticket": _cancel_ticket,
    "force_merge": _force_merge,
    "redispatch_dev": _redispatch_dev,
}


async def _resolve_pr_for_item(
    session: AsyncSession,
    workspace_id,
    item: InboxItem,
) -> tuple[str, str, int] | None:
    """Find (owner, repo, number) for the PR linked to this inbox
    row. Checks payload.pr_url first, then payload.html_url, then
    falls back to looking up by ticket_ref in pull_requests."""
    payload = item.payload or {}
    for key in ("pr_url", "html_url"):
        val = payload.get(key)
        if isinstance(val, str):
            m = _PR_URL_RE.search(val)
            if m:
                return m.group(1), m.group(2), int(m.group(3))
    ticket_ref = payload.get("ticket_ref")
    if not ticket_ref:
        return None
    # Look up open PR whose title contains this ticket ref.
    pr = (
        await session.execute(
            select(PullRequest).where(
                PullRequest.workspace_id == workspace_id,
                PullRequest.state == "open",
                PullRequest.title.ilike(f"%{ticket_ref}%"),
            ).order_by(PullRequest.updated_at_external.desc()).limit(1)
        )
    ).scalars().first()
    if pr is None:
        return None
    if "/" not in (pr.repo_full_name or ""):
        return None
    owner, repo = pr.repo_full_name.split("/", 1)
    return owner, repo, int(pr.number)


async def _resolve_install_for_repo(
    session: AsyncSession,
    workspace_id,
    owner: str,
    repo: str,
) -> GitHubInstallation | None:
    """Find the active GH App install for this workspace. The repo
    pair is passed for future per-account filtering but today every
    Ship workspace has at most one install."""
    return (
        await session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.workspace_id == workspace_id,
                GitHubInstallation.suspended_at.is_(None),
            ).limit(1)
        )
    ).scalar_one_or_none()


async def _close_pr_for_ticket(
    session: AsyncSession,
    workspace_id,
    ticket_ref: str,
    *,
    comment: str,
) -> int | None:
    """Best-effort: close the open PR linked to ``ticket_ref`` with
    a comment. Returns the PR number on success, None when no PR
    found or the API call failed (logged)."""
    pr = (
        await session.execute(
            select(PullRequest).where(
                PullRequest.workspace_id == workspace_id,
                PullRequest.state == "open",
                PullRequest.title.ilike(f"%{ticket_ref}%"),
            ).order_by(PullRequest.updated_at_external.desc()).limit(1)
        )
    ).scalars().first()
    if pr is None or "/" not in (pr.repo_full_name or ""):
        return None
    owner, repo = pr.repo_full_name.split("/", 1)
    install = (
        await session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.workspace_id == workspace_id,
                GitHubInstallation.suspended_at.is_(None),
            ).limit(1)
        )
    ).scalar_one_or_none()
    if install is None:
        return None
    from backend.app.integrations.github.app_auth import (
        get_installation_token,
    )
    import httpx
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            token = await get_installation_token(
                install.installation_id, settings=settings, client=client,
            )
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            }
            await client.post(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{pr.number}/comments",
                headers=headers,
                json={"body": comment},
            )
            await client.patch(
                f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr.number}",
                headers=headers,
                json={"state": "closed"},
            )
        return int(pr.number)
    except Exception as exc:  # noqa: BLE001
        log.info(
            "close_pr_for_ticket: failed ws=%s ticket=%s: %s",
            workspace_id, ticket_ref, exc,
        )
        return None


__all__ = ["run_action_executor"]
