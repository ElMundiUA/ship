"""Jira Cloud implementation of the tracker gateway."""

from __future__ import annotations

import logging
from typing import Any, Literal

import httpx

from backend.app.integrations.gateway.tracker import CreatedTicket, TicketRef


logger = logging.getLogger(__name__)


_JIRA_ISSUETYPE_FALLBACKS: dict[str, tuple[str, ...]] = {
    # ``feature`` prefers Story (Jira Software default), but a project
    # with Story disabled often still has the legacy ``New Feature``
    # type; if both are absent we degrade to ``Task`` so the ticket
    # actually lands rather than 400-looping.
    "feature": ("Story", "New Feature", "Task"),
    "bug": ("Bug", "Task"),
    "task": ("Task",),
}


class JiraTracker:
    """Per-token Jira adapter using Atlassian API token basic auth."""

    def __init__(
        self,
        *,
        site_url: str,
        email: str,
        api_token: str,
        default_project: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._site_url = site_url.rstrip("/")
        self._email = email
        self._token = api_token
        self._default_project = default_project
        self._client = client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        owns_client = self._client is None
        http = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        try:
            response = await http.request(
                method,
                f"{self._site_url}{path}",
                auth=(self._email, self._token),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                json=json,
                params=params,
            )
        finally:
            if owns_client:
                await http.aclose()
        response.raise_for_status()
        return response.json() if response.content else {}

    async def list_tickets(
        self,
        *,
        limit: int = 10,
        state: str | None = None,
        assignee_me: bool = False,
        query: str | None = None,
        assignee: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        project = self._default_project
        if project:
            clauses.append(f"project = {project}")
        raw_state = (state or "all").lower()
        if raw_state == "open":
            clauses.append("statusCategory != Done")
        elif raw_state in {"closed", "done", "completed"}:
            clauses.append("statusCategory = Done")
        if assignee_me:
            clauses.append("assignee = currentUser()")
        elif assignee:
            clauses.append(f'assignee = "{assignee}"')
        if query and query.strip():
            safe = query.strip().replace('"', '\\"')
            clauses.append(f'summary ~ "{safe}"')

        jql = " AND ".join(clauses) if clauses else "ORDER BY updated DESC"
        if clauses:
            jql = f"{jql} ORDER BY updated DESC"
        body = await self._request(
            "GET",
            "/rest/api/3/search",
            params={
                "jql": jql,
                "maxResults": str(max(1, min(limit, 50))),
                "fields": "summary,status,updated",
            },
        )
        out: list[dict[str, Any]] = []
        for issue in body.get("issues", []) or []:
            fields = issue.get("fields") or {}
            status = fields.get("status") or {}
            out.append(
                {
                    "id": issue.get("key") or issue.get("id"),
                    "title": fields.get("summary"),
                    "url": f"{self._site_url}/browse/{issue.get('key')}",
                    "status": status.get("name"),
                    "updated_at": fields.get("updated"),
                }
            )
        return out

    async def transition(self, ticket: TicketRef, *, to_state: str) -> None:
        if ticket.kind != "jira":
            raise ValueError(f"JiraTracker can't transition kind={ticket.kind}")
        issue_key = _issue_key(ticket.id)
        transitions = await self._request(
            "GET", f"/rest/api/3/issue/{issue_key}/transitions"
        )
        match = None
        for transition in transitions.get("transitions", []) or []:
            if str(transition.get("name", "")).lower() == to_state.lower():
                match = transition
                break
            if str(transition.get("id")) == to_state:
                match = transition
                break
        if match is None:
            raise ValueError(f"Jira transition {to_state!r} not found for {issue_key}")
        await self._request(
            "POST",
            f"/rest/api/3/issue/{issue_key}/transitions",
            json={"transition": {"id": str(match["id"])}},
        )

    async def comment(self, ticket: TicketRef, *, body: str) -> None:
        if ticket.kind != "jira":
            raise ValueError(f"JiraTracker can't comment on kind={ticket.kind}")
        await self._request(
            "POST",
            f"/rest/api/3/issue/{_issue_key(ticket.id)}/comment",
            json={"body": _adf_doc(body)},
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
        del project_id  # Jira attaches via "Epic Link"; not modelled here yet.
        project = (project_hint or self._default_project or "").strip().upper()
        if not project:
            raise ValueError("Jira project is required; pass project_hint='ENG'.")

        # Default keeps today's behaviour (everything → Task) so existing
        # callers see no wire drift.
        candidates = _JIRA_ISSUETYPE_FALLBACKS.get(ticket_type or "task", ("Task",))

        last_error: httpx.HTTPStatusError | None = None
        attempted: list[str] = []
        for issuetype_name in candidates:
            payload: dict[str, Any] = {
                "fields": {
                    "project": {"key": project},
                    "summary": title,
                    "description": _adf_doc(body),
                    "issuetype": {"name": issuetype_name},
                }
            }
            if labels:
                payload["fields"]["labels"] = labels
            try:
                created = await self._request(
                    "POST", "/rest/api/3/issue", json=payload
                )
            except httpx.HTTPStatusError as exc:
                # Only retry on the specific shape Jira uses for "issue
                # type isn't provisioned on this project" (400 +
                # ``errors.issuetype`` populated). Any other 4xx/5xx
                # bubbles up so we don't paper over real failures.
                if not _is_issuetype_400(exc):
                    raise
                last_error = exc
                attempted.append(issuetype_name)
                logger.warning(
                    "jira create_ticket: project=%s rejected issuetype=%r; "
                    "trying next fallback",
                    project,
                    issuetype_name,
                )
                continue
            key = str(created.get("key") or created.get("id") or "")
            if not key:
                raise ValueError(
                    "Jira refused issue creation (no key returned)."
                )
            return CreatedTicket(
                ref=TicketRef(kind="jira", workspace_hint=project, id=key),
                url=f"{self._site_url}/browse/{key}",
                display_id=key,
            )

        # Every candidate failed with an ``errors.issuetype`` 400; surface
        # the last error so the operator sees the real Jira response.
        assert last_error is not None  # candidates is non-empty
        logger.warning(
            "jira create_ticket: project=%s exhausted fallbacks=%r",
            project,
            attempted,
        )
        raise last_error


def _issue_key(raw: str) -> str:
    return raw.rsplit("/", 1)[-1].strip()


def _is_issuetype_400(exc: httpx.HTTPStatusError) -> bool:
    """Return True iff the error is a Jira "issuetype rejected" 400.

    Jira returns ``400`` with a body shaped like
    ``{"errorMessages": [], "errors": {"issuetype": "..."}}`` when the
    requested issue type isn't provisioned on the project. Any other
    400 (validation on summary, missing project, etc.) does NOT carry
    a populated ``errors.issuetype`` and is not retryable here — we
    propagate so the real reason surfaces.
    """
    response = exc.response
    if response.status_code != 400:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    errors = body.get("errors")
    if not isinstance(errors, dict):
        return False
    return bool(errors.get("issuetype"))


def _adf_doc(markdown: str) -> dict[str, Any]:
    """Render plain text into a minimal Atlassian Document Format doc."""

    text = markdown.strip() or " "
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text[:32767]}],
            }
        ],
    }


__all__ = ["JiraTracker"]
