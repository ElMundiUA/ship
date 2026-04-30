"""E14 — server-side routine dispatch.

Handler that owns the routine-run loop end-to-end. Customer-side cron
(``ship-trigger-schedule.yml``) only wakes this up; the server figures
out which pattern is due, picks a ticket from the bound tracker, packs
the prompt, and hands the work off to the agent runtime (Cursor Cloud
today; pluggable later).

Tonight's MVP scope:

- Single endpoint ``POST /v1/workspaces/{ws}/repos/{repo_id}/routines/{routine_id}/dispatch``
- Reads the pattern body from ``artifacts/patterns/<id>/ARTIFACT.md`` on
  the server image (no DB cache yet — the bundle ships with the image).
- For tracker-driven roles (``role-intake``, ``role-ba``,
  ``role-developer``) — picks the next open GitHub issue that doesn't
  already have a ``[Ship SDLC:<role>]`` comment. Quick-and-dirty FSM:
  intake = no labels; ba = ``stage:intake`` label; developer =
  ``ready:developer`` label.
- For context-free flows (``flow-daily-retro``) — no ticket pull; just
  dispatch with a workspace-level digest prompt.
- Hands off to Cursor Cloud via :mod:`backend.app.services.cursor_cloud`.
- Returns ``{ status, agent_id, ticket_id?, branch_name?, ... }``.

What this is **not** yet:

- No FSM transitions / labels written by the server. The agent does that
  via ``gh`` CLI from inside its run.
- No ``output_schema`` validation on the way back. Cursor doesn't post
  a structured callback today; the agent's effect is the GitHub state
  it leaves behind.
- No idempotency layer on top of "did this routine already fire in this
  window?". Caller (the cron tick handler) is responsible for that.
"""

from __future__ import annotations

import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.github.issues_tracker import GitHubIssuesTracker
from backend.app.services.cursor_cloud import (
    CursorCloudError,
    LaunchedAgent,
    launch_agent,
)
from backend.app.services.routine_run_token import RoutineRunClaims, mint as mint_run_token


router = APIRouter(
    prefix="/workspaces/{workspace_id}/repos/{repo_id}/routines",
    tags=["routines"],
)

logger = logging.getLogger(__name__)


# ``artifacts/patterns/`` lives at the repo root. The Bunny image copies
# the artifacts tree alongside ``backend/`` so resolving from ``__file__``
# walks up enough parents to land on the same root. Pinned here so a
# single rename doesn't silently degrade dispatch to "pattern not found"
# at runtime.
_PATTERNS_DIR = Path(__file__).resolve().parents[5] / "artifacts" / "patterns"


# Quick-and-dirty FSM mapping. The full version (declared in pattern
# frontmatter as ``fsm_stage`` + ``output_schema``) lands in T02; this
# table is the bridge until then so dispatch works tonight.
_ROLE_PICK: dict[str, dict[str, Any]] = {
    "role-intake": {
        "label": None,  # any open issue
        "comment_marker": "[Ship SDLC:role-intake]",
    },
    "role-ba": {
        "label": "stage:intake",
        "comment_marker": "[Ship SDLC:role-ba]",
    },
    "role-developer": {
        "label": "ready:developer",
        "comment_marker": "[Ship SDLC:role-developer]",
    },
}

# Context-free patterns: no ticket pull, just one prompt per dispatch.
_CONTEXT_FREE: frozenset[str] = frozenset(
    {"flow-daily-retro", "flow-learning-capture"}
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DispatchIn(BaseModel):
    """Override knobs for the dispatch — most callers post ``{}``."""

    # Which pattern to render. If absent the server will try to look up
    # the routine in the repo's ``.ship/config.yml`` (a future cache
    # column on ``workspace_repos``). For tonight: the caller passes the
    # pattern id explicitly so the route doesn't need that cache yet.
    pattern: str | None = None
    # Specific issue number to target (skip the pick step). Useful for
    # the manual-launch path, e.g. operator clicks "run on this ticket".
    issue_number: int | None = None
    # Override Cursor branch name; default derives from role + issue.
    branch_name: str | None = None
    # Force ``autoCreatePr`` on the Cursor target. Developers usually
    # leave it false because the prompt opens a deliberate PR.
    auto_create_pr: bool = False
    # Override the GitHub ref the agent checks out. Defaults to the
    # repo's tracked default branch.
    ref: str | None = None


class DispatchOut(BaseModel):
    """Result of a single dispatch — one agent kicked off, or a no-op."""

    status: str  # ``dispatched`` | ``noop`` | ``error``
    routine_id: str
    pattern: str
    role: str
    reason: str | None = None
    agent_id: str | None = None
    branch_name: str | None = None
    cursor_url: str | None = None
    ticket: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_pattern_body(pattern_id: str) -> str:
    """Read the body (post-frontmatter) of an ``artifacts/patterns/`` entry."""
    p = _PATTERNS_DIR / pattern_id / "ARTIFACT.md"
    if not p.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "pattern_not_found",
                "message": f"No pattern at {p.relative_to(_PATTERNS_DIR.parent.parent)}",
            },
        )
    raw = p.read_text(encoding="utf-8")
    # Body starts after the second ``---`` line. Defensive: if the file
    # has no frontmatter, treat the whole thing as body.
    if raw.startswith("---"):
        # find the closing ``---`` after the opening one
        m = re.search(r"^---\s*$", raw[4:], flags=re.MULTILINE)
        if m:
            return raw[4 + m.end() :].lstrip("\n").rstrip()
    return raw.rstrip()


def _read_base() -> str:
    return _read_pattern_body("common-base")


def _render_prompt(
    *,
    pattern_body: str,
    role: str,
    owner: str,
    repo: str,
    issue: dict[str, Any] | None,
    run_token: str | None = None,
    callbacks_base: str = "",
) -> str:
    base_body = _read_base()
    base = (
        base_body
        .replace("{{SKILLS_CONTEXT}}", "(skills directory unavailable in this run)")
        .replace("{{ROLE}}", role)
        .replace(
            "{{ISSUE}}",
            f"#{issue.get('number')}" if issue else "(no ticket)",
        )
    )
    body = pattern_body.replace("{{BASE}}", base)
    if issue:
        body = (
            body
            .replace("{{ISSUE}}", f"#{issue.get('number')}")
            .replace("{{TITLE}}", str(issue.get("title") or "")[:500])
            .replace("{{DESCRIPTION}}", str(issue.get("body") or "")[:8000])
        )
    else:
        body = (
            body
            .replace("{{ISSUE}}", "(no ticket)")
            .replace("{{TITLE}}", "")
            .replace("{{DESCRIPTION}}", "")
        )

    issue_ref = (
        f"#{issue.get('number')} on {owner}/{repo}" if issue else "(context-free run)"
    )
    issue_n = issue.get("number") if issue else None
    ticket_id_for_callback = (
        f"{owner}/{repo}#{issue_n}" if issue_n is not None else ""
    )

    # Cursor agents do not hold Linear / GitHub credentials. The Ship server
    # owns the workspace's OAuth tokens and exposes a tiny set of callbacks the
    # agent hits with the per-run JWT below. The token is short-lived (1h) and
    # bound to this workspace + repo + ticket, so leaks are bounded.
    callback_block = ""
    if run_token and callbacks_base:
        comment_curl = (
            f"curl -sS -X POST '{callbacks_base}/v1/routine-callbacks/comment' \\\n"
            f"  -H 'Authorization: Bearer {run_token}' \\\n"
            "  -H 'Content-Type: application/json' \\\n"
            "  -d '{\"body\":\"Your comment text here. [Ship SDLC:" + role + "]\"}'"
        )
        inbox_curl = (
            f"curl -sS -X POST '{callbacks_base}/v1/routine-callbacks/inbox-item' \\\n"
            f"  -H 'Authorization: Bearer {run_token}' \\\n"
            "  -H 'Content-Type: application/json' \\\n"
            "  -d '{\"type\":\"improvement\",\"title\":\"...\",\"summary\":\"...\"}'"
        )
        transition_curl = (
            f"curl -sS -X POST '{callbacks_base}/v1/routine-callbacks/transition' \\\n"
            f"  -H 'Authorization: Bearer {run_token}' \\\n"
            "  -H 'Content-Type: application/json' \\\n"
            "  -d '{\"to_state\":\"in_progress\"}'"
        )
        ticket_line = (
            f"This run is bound to ticket `{ticket_id_for_callback}` — the callbacks below"
            " act on that ticket by default.\n\n"
            if ticket_id_for_callback
            else "This run is context-free (no specific ticket bound).\n\n"
        )
        callback_block = (
            "## How to write back into Ship\n\n"
            "You DO NOT have direct Linear / GitHub credentials. Ship server owns those.\n"
            "Instead, hit Ship's per-run callback endpoints with the bearer token below.\n\n"
            f"Bearer token (for this run only, expires in 1h):\n```\n{run_token}\n```\n\n"
            f"{ticket_line}"
            "### Comment on the bound ticket\n\n```bash\n"
            + comment_curl + "\n```\n\n"
            "### Drop a free-form item in the workspace inbox\n\n```bash\n"
            + inbox_curl + "\n```\n\n"
            "### Transition the FSM stage of the bound ticket\n\n```bash\n"
            + transition_curl + "\n```\n\n"
            f"End every Ship-side comment with the marker `[Ship SDLC:{role}]`.\n"
            f"If a comment with `[Ship SDLC:{role}]` already reflects the current state, "
            "exit without re-commenting.\n\n"
            "Do NOT use `gh issue comment`, `linear-cli`, or any other direct API. "
            "All tracker writes must go through the curl callbacks above so Ship server "
            "stays the single source of audit truth.\n\n"
        )
    return callback_block + body


async def _pick_issue(
    *,
    tracker: GitHubIssuesTracker,
    role: str,
    owner: str,
    repo: str,
) -> dict[str, Any] | None:
    """Find the next open GitHub issue this role should work on.

    Tonight's heuristic — see ``_ROLE_PICK``. The full version (driven
    by pattern frontmatter ``fsm_stage``) lands in T02.
    """
    spec = _ROLE_PICK.get(role)
    if spec is None:
        return None  # role we don't know — caller decides what to do
    # GitHub /search/issues lets us filter by label and exclude PRs in one query.
    # Build the q string. We can't filter by "no comment with marker X" in the
    # query, so we list candidates and check each.
    q_parts = [f"repo:{owner}/{repo}", "is:issue", "is:open", "sort:updated-asc"]
    if spec["label"]:
        q_parts.append(f'label:"{spec["label"]}"')
    q = " ".join(q_parts)

    candidates = await tracker.list_tickets(state="open", limit=20, query=q)
    if not candidates:
        return None
    marker = spec["comment_marker"]

    # Hydrate each candidate with body+labels (the listing only returns a thin
    # subset). Stop at the first one that hasn't yet had this role's marker.
    for row in candidates:
        # row['id'] looks like 'owner/repo#N'
        n = int(str(row["id"]).rsplit("#", 1)[-1])
        full = await tracker._request(
            "GET",
            f"/repos/{owner}/{repo}/issues/{n}",
            params={"per_page": 100},
        )
        full_body = full.json() or {}
        # Comments (separate endpoint).
        c = await tracker._request(
            "GET",
            f"/repos/{owner}/{repo}/issues/{n}/comments",
            params={"per_page": 100},
        )
        comments = c.json() or []
        already = any(marker in (cm.get("body") or "") for cm in comments)
        if already:
            continue
        return {
            "number": n,
            "title": full_body.get("title"),
            "body": full_body.get("body") or "",
            "url": full_body.get("html_url"),
            "labels": [lab.get("name") for lab in full_body.get("labels") or []],
            "state": full_body.get("state"),
        }
    return None


def _branch_name(role: str, issue_number: int | None) -> str:
    suffix = format(int(time.time() * 1000), "x")
    if role == "role-developer" and issue_number is not None:
        return f"fix/ship-{issue_number}-auto"
    if issue_number is not None:
        return f"cursor/ship-{role}-issue-{issue_number}-{suffix}"
    return f"cursor/ship-{role}-{suffix}"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/{routine_id}/dispatch", response_model=DispatchOut)
async def dispatch_routine(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    routine_id: str,
    payload: DispatchIn = DispatchIn(),
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DispatchOut:
    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="repo not activated"
        )

    install_row = (
        await session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.id == repo_row.installation_id
            )
        )
    ).scalars().first()
    if install_row is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={"code": "github_app_missing"},
        )

    pattern_id = (payload.pattern or routine_id).strip()
    pattern_body = _read_pattern_body(pattern_id)

    # Pull the role from the routine: the pattern id is the role for
    # tonight's MVP. ``role-intake`` → ``role-intake``.
    role = pattern_id

    owner, _, repo_name = repo_row.full_name.partition("/")
    if not owner or not repo_name:
        raise HTTPException(
            status_code=500, detail=f"invalid full_name: {repo_row.full_name!r}"
        )

    issue: dict[str, Any] | None = None
    if pattern_id not in _CONTEXT_FREE:
        tracker = GitHubIssuesTracker(
            installation_id=install_row.installation_id,
            owner=owner,
            repo=repo_name,
            settings=settings,
        )
        if payload.issue_number is not None:
            full = await tracker._request(
                "GET",
                f"/repos/{owner}/{repo_name}/issues/{payload.issue_number}",
            )
            ib = full.json() or {}
            issue = {
                "number": payload.issue_number,
                "title": ib.get("title"),
                "body": ib.get("body") or "",
                "url": ib.get("html_url"),
                "labels": [lab.get("name") for lab in ib.get("labels") or []],
                "state": ib.get("state"),
            }
        else:
            issue = await _pick_issue(
                tracker=tracker, role=role, owner=owner, repo=repo_name
            )
        if issue is None:
            return DispatchOut(
                status="noop",
                routine_id=routine_id,
                pattern=pattern_id,
                role=role,
                reason="no_eligible_ticket",
            )

    # Mint the routine-run JWT before launching so we can embed it in
    # the prompt the agent receives. Bound to (workspace, repo, routine,
    # pattern, ticket) — agent_id is not in the token because we don't
    # know it until Cursor responds (acceptable: token is short-lived
    # and the ticket binding already scopes the blast radius).
    ticket_id_for_token = (
        f"{owner}/{repo_name}#{issue.get('number')}"
        if issue and issue.get("number") is not None
        else None
    )
    run_token = mint_run_token(
        claims=RoutineRunClaims(
            workspace_id=workspace_id,
            repo_id=repo_id,
            routine_id=routine_id,
            pattern=pattern_id,
            ticket_id=ticket_id_for_token,
        ),
        settings=settings,
    )
    callbacks_base = settings.public_url.rstrip("/") if settings.public_url else ""

    prompt = _render_prompt(
        pattern_body=pattern_body,
        role=role,
        owner=owner,
        repo=repo_name,
        issue=issue,
        run_token=run_token,
        callbacks_base=callbacks_base,
    )

    branch_name = payload.branch_name or _branch_name(
        role, issue.get("number") if issue else None
    )
    ref = payload.ref or repo_row.default_branch or "main"
    repo_url = f"https://github.com/{repo_row.full_name}"

    api_key = (os.getenv("CURSOR_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "cursor_api_key_missing",
                "message": "CURSOR_API_KEY is not configured on the Ship server.",
            },
        )

    try:
        launched: LaunchedAgent = await launch_agent(
            api_key=api_key,
            prompt=prompt,
            repo_url=repo_url,
            branch_name=branch_name,
            ref=ref,
            auto_create_pr=payload.auto_create_pr,
        )
    except CursorCloudError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "cursor_launch_failed",
                "upstream_status": exc.status,
                "body": str(exc.body)[:500],
            },
        ) from exc

    cursor_url = (
        launched.raw.get("target", {}).get("url")
        or f"https://cursor.com/agents/{launched.agent_id}"
    )

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="routine.dispatch",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload={
                "routine_id": routine_id,
                "pattern": pattern_id,
                "role": role,
                "issue_number": issue.get("number") if issue else None,
                "branch_name": launched.branch_name,
                "agent_id": launched.agent_id,
                "ref": ref,
            },
        )
    )
    await session.flush()

    return DispatchOut(
        status="dispatched",
        routine_id=routine_id,
        pattern=pattern_id,
        role=role,
        agent_id=launched.agent_id,
        branch_name=launched.branch_name,
        cursor_url=cursor_url,
        ticket=issue,
    )
