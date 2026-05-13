"""GitHub Issues implementation of :class:`TrackerGateway`.

Backed by a :class:`GitHubInstallation` token, not by a user-supplied
PAT, so the agent never needs an extra OAuth dance — if the Ship App
is installed against the repo it can already open issues.

Scope is deliberately narrow for C12: ``create_ticket`` is the only
verb the real-agent toolbox needs out of the box. ``list_tickets`` /
``transition`` / ``comment`` exist as stubs raising
:class:`NotImplementedError` so any code that tries to use this as a
full tracker fails loudly instead of silently doing nothing — we'll
flesh those out when the daily-standup pipeline gets a
GitHub-Issues-native surface (Day-5+).

Unlike the Linear/Notion adapters, this one needs the *repo* to post
against (Linear / Notion connect at the workspace / database level).
That's wired in at construction time so the call sites don't have to
re-resolve it per request.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import httpx

from backend.app.core.config import Settings
from backend.app.integrations.gateway.tracker import (
    CommentRef,
    CreatedTicket,
    ListedIssue,
    TicketRef,
)
from backend.app.integrations.github.app_auth import (
    GITHUB_API_BASE,
    fetch_installation_token,
)


class GitHubIssuesTracker:
    """Per-(installation, repo) adapter implementing :class:`TrackerGateway`.

    Construct one per repo. ``owner`` + ``repo`` are the natural
    GitHub identifiers; ``installation_id`` points at the Ship App's
    numeric install id (GitHub's side, not our UUID).
    """

    def __init__(
        self,
        *,
        installation_id: int,
        owner: str,
        repo: str,
        settings: Settings,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._installation_id = installation_id
        self._owner = owner
        self._repo = repo
        self._settings = settings
        self._client = client

    async def _headers(self) -> dict[str, str]:
        token = await fetch_installation_token(
            self._installation_id,
            settings=self._settings,
            client=self._client,
        )
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        headers = await self._headers()
        owns_client = self._client is None
        http = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        try:
            response = await http.request(
                method,
                f"{GITHUB_API_BASE}{path}",
                headers=headers,
                json=json,
                params=params,
            )
        finally:
            if owns_client:
                await http.aclose()
        response.raise_for_status()
        return response

    async def list_tickets(
        self,
        *,
        limit: int = 10,
        state: str | None = None,
        assignee_me: bool = False,
        query: str | None = None,
        assignee: str | None = None,
    ) -> list[dict[str, Any]]:
        """Issues in ``owner/repo`` ordered by most-recently-updated.

        Uses the REST list endpoint when only state/assignee filters are
        needed; falls back to ``/search/issues`` when ``query`` is set
        (full-text in GitHub's search syntax).
        """
        del assignee_me  # installation tokens have no human "me"
        raw_state = (state or "open").lower()
        if raw_state == "all":
            gh_state = "all"
        elif raw_state in {"closed", "done", "completed"}:
            gh_state = "closed"
        else:
            gh_state = "open"

        per_page = max(1, min(limit, 50))
        payload: list[Any]

        if query and query.strip():
            q_parts = [
                f"repo:{self._owner}/{self._repo}",
                "is:issue",
            ]
            if gh_state == "open":
                q_parts.append("is:open")
            elif gh_state == "closed":
                q_parts.append("is:closed")
            if assignee:
                q_parts.append(f"assignee:{assignee}")
            q_parts.append(query.strip())
            q_str = " ".join(q_parts)
            response = await self._request(
                "GET",
                "/search/issues",
                params={"q": q_str, "per_page": min(per_page, 100)},
            )
            body = response.json() or {}
            payload = body.get("items") or []
        else:
            params: dict[str, Any] = {
                "state": gh_state,
                "sort": "updated",
                "direction": "desc",
                "per_page": per_page,
            }
            if assignee:
                params["assignee"] = assignee
            response = await self._request(
                "GET",
                f"/repos/{self._owner}/{self._repo}/issues",
                params=params,
            )
            payload = response.json() or []

        out: list[dict[str, Any]] = []
        for item in payload:
            if item.get("pull_request") is not None:
                continue
            num = item.get("number")
            out.append(
                {
                    "id": f"{self._owner}/{self._repo}#{num}",
                    "title": item.get("title"),
                    "url": item.get("html_url"),
                    "status": item.get("state"),
                    "updated_at": item.get("updated_at"),
                }
            )
        return out

    async def transition(self, ticket: TicketRef, *, to_state: str) -> None:
        """Open / close a GitHub Issue.

        GitHub's state machine is binary (open / closed); any
        ``to_state`` other than those two raises :class:`ValueError`
        so the agent sees a clear error instead of silently nothing.
        """
        if ticket.kind != "github_issues":
            raise ValueError(
                f"GitHubIssuesTracker can't transition kind={ticket.kind}"
            )
        normalized = to_state.lower()
        if normalized not in {"open", "closed"}:
            raise ValueError(
                f"GitHub issues support only open/closed, got {to_state!r}"
            )
        number = _issue_number_from_ref(ticket)
        await self._request(
            "PATCH",
            f"/repos/{self._owner}/{self._repo}/issues/{number}",
            json={"state": normalized},
        )

    async def comment(self, ticket: TicketRef, *, body: str) -> None:
        if ticket.kind != "github_issues":
            raise ValueError(
                f"GitHubIssuesTracker can't comment kind={ticket.kind}"
            )
        number = _issue_number_from_ref(ticket)
        await self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/issues/{number}/comments",
            json={"body": body},
        )

    async def create_ticket(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        project_hint: str | None = None,
        project_id: str | None = None,
        ticket_type: Literal["bug", "feature", "task"] | None = None,
    ) -> CreatedTicket:
        """Open an issue in ``owner/repo``.

        ``project_hint`` is ignored — the repo is decided at
        construction time. ``project_id`` is accepted for protocol
        parity but not applied (GitHub Project v2 attachment is a
        separate GraphQL surface). ``labels`` are passed straight
        through; GitHub silently ignores labels that don't exist on
        the repo, which is the opposite of Linear's strictness and is
        fine here because GitHub treats labels as free-form.

        ``ticket_type`` (``bug`` / ``feature`` / ``task``) renders as
        a ``type:<value>`` label prepended to ``labels``. GitHub
        Issues has no native issue-type field today, so the label is
        the only mechanism — same string convention as the Linear
        fallback so a downstream dashboard filters on one key.
        """
        del project_id  # accepted for protocol parity; not applied here
        body_payload: dict[str, Any] = {"title": title, "body": body}
        if ticket_type is not None:
            type_label = f"type:{ticket_type}"
            merged: list[str] = [type_label]
            for raw in labels or []:
                if raw.lower() != type_label:
                    merged.append(raw)
            body_payload["labels"] = merged
        elif labels:
            body_payload["labels"] = labels
        response = await self._request(
            "POST",
            f"/repos/{self._owner}/{self._repo}/issues",
            json=body_payload,
        )
        payload = response.json() or {}
        number = int(payload.get("number") or 0)
        if number == 0:
            raise ValueError("GitHub refused issue creation (no number).")
        display_id = f"{self._owner}/{self._repo}#{number}"
        return CreatedTicket(
            ref=TicketRef(
                kind="github_issues",
                workspace_hint=f"{self._owner}/{self._repo}",
                id=str(number),
            ),
            url=str(payload.get("html_url") or ""),
            display_id=display_id,
        )


    # -----------------------------------------------------------------
    # Clarifications projection surface (D13)
    # -----------------------------------------------------------------

    async def list_issues_with_label(
        self, label: str, *, limit: int = 100
    ) -> list[ListedIssue]:
        """Open issues in the wired repo that carry ``label``.

        GitHub's ``GET /issues?labels=X`` returns both issues and
        PRs (PRs are issues with a ``pull_request`` key); we filter
        PRs out so a clarification label accidentally landing on a
        PR doesn't spam the projection.
        """
        response = await self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/issues",
            params={
                "state": "open",
                "labels": label,
                "per_page": max(1, min(limit, 100)),
            },
        )
        payload = response.json() or []
        out: list[ListedIssue] = []
        for item in payload:
            if item.get("pull_request") is not None:
                continue
            number = item.get("number")
            if not isinstance(number, int):
                continue
            display_id = f"{self._owner}/{self._repo}#{number}"
            out.append(
                ListedIssue(
                    ref=TicketRef(
                        kind="github_issues",
                        workspace_hint=f"{self._owner}/{self._repo}",
                        id=str(number),
                    ),
                    display_id=display_id,
                    url=item.get("html_url"),
                )
            )
        return out

    async def list_comments(self, ticket: TicketRef) -> list[CommentRef]:
        """All comments on the issue, oldest first.

        GitHub defaults to ascending order (``sort=created,
        direction=asc``) — we set it explicitly because the
        projection depends on chronological order for the
        "answer that came after a clarification" pairing.
        """
        if ticket.kind != "github_issues":
            raise ValueError(
                f"GitHubIssuesTracker can't list_comments for kind={ticket.kind}"
            )
        number = _issue_number_from_ref(ticket)
        response = await self._request(
            "GET",
            f"/repos/{self._owner}/{self._repo}/issues/{number}/comments",
            params={
                "sort": "created",
                "direction": "asc",
                "per_page": 100,
            },
        )
        payload = response.json() or []
        out: list[CommentRef] = []
        for item in payload:
            created_raw = item.get("created_at")
            try:
                created_at = _parse_iso8601(created_raw) if created_raw else datetime.min
            except ValueError:
                created_at = datetime.min
            user = item.get("user") or {}
            out.append(
                CommentRef(
                    id=str(item.get("id") or ""),
                    body=str(item.get("body") or ""),
                    author=user.get("login"),
                    created_at=created_at,
                    url=item.get("html_url"),
                )
            )
        return out

    async def remove_label(self, ticket: TicketRef, label: str) -> None:
        """Strip ``label`` from the issue.

        GitHub's ``DELETE /issues/{n}/labels/{name}`` returns 404
        when the label isn't attached; we swallow that so the
        caller can treat the call as idempotent.
        """
        if ticket.kind != "github_issues":
            raise ValueError(
                f"GitHubIssuesTracker can't remove_label for kind={ticket.kind}"
            )
        number = _issue_number_from_ref(ticket)
        headers = await self._headers()
        owns_client = self._client is None
        http = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        try:
            response = await http.request(
                "DELETE",
                f"{GITHUB_API_BASE}/repos/{self._owner}/{self._repo}/issues/"
                f"{number}/labels/{label}",
                headers=headers,
            )
        finally:
            if owns_client:
                await http.aclose()
        if response.status_code == 404:
            return  # Label wasn't attached; idempotent no-op.
        response.raise_for_status()


def _parse_iso8601(raw: str) -> datetime:
    """GitHub ships ``created_at`` as ``2026-01-02T03:04:05Z`` — normalise."""
    cleaned = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    return datetime.fromisoformat(cleaned)


def _issue_number_from_ref(ticket: TicketRef) -> int:
    """Extract the issue number from a ``github_issues`` :class:`TicketRef`.

    ``TicketRef.id`` carries the bare number as a string (that's what
    we stamp in :meth:`create_ticket`). If a caller somehow hands us
    the ``owner/repo#N`` shape we still cope — but we refuse silently
    bad inputs like floats or empty strings so the caller sees a
    clean error.
    """
    raw = (ticket.id or "").strip()
    if "#" in raw:
        raw = raw.rsplit("#", 1)[-1]
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"TicketRef.id {ticket.id!r} is not a GitHub issue number"
        ) from exc


__all__ = ["GitHubIssuesTracker"]
