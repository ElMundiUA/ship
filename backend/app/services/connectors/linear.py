"""Linear connector — fetch an issue into markdown for the Distiller.

Second real fetcher in the Phase 7c family. Scope kept deliberately
narrow, mirroring :mod:`backend.app.services.connectors.notion`:

- ``resource_ref = {"issue_id": "<linear id or ENG-123>"}`` — we
  query Linear's GraphQL ``issue(id: ...)`` and render it as
  markdown. The GraphQL endpoint accepts either the UUID form
  (``c2d4…``) or the human identifier (``ENG-123``), which is what
  operators paste from Linear URLs like ``linear.app/elmundi/issue/
  ENG-123/…`` — so we don't normalise or split.
- Anything else raises :class:`ConnectorUnsupported` so the
  dispatcher falls back to the Phase 7b stub body. Multi-issue sync
  (``{"team_key": "ENG"}`` → mirror the whole team) is a natural
  next step but it needs the multi-page response shape, which
  belongs to a later phase.

Why GraphQL (not REST): Linear only ships GraphQL. Payload is
tiny — one query per sync — so no Dataloader / retry jazz is
warranted here. When we start doing team-wide mirrors we'll lift
the real client logic out of
:class:`backend.app.integrations.linear.tracker_adapter.LinearTracker`.

Markdown output shape
=====================

Each issue renders as a single page whose body includes:

- H1 header with identifier + title (``# ENG-123 · Rebuild retriever``).
- One "callout" paragraph with Open-in-Linear link + state + assignee.
- Verbatim ``description`` block (already markdown in Linear).
- Trailing metadata block with priority, last-update, team, labels.

This is deterministic enough that re-syncing a page with no changes
collapses to ``skip`` via ``content_sha`` — the same dedup path Notion
rides.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

import httpx

from backend.app.db.models.tenancy import Integration
from backend.app.security.encryption import safe_decrypt

from . import ConnectorConfigError, ConnectorPage, ConnectorUnsupported, register


logger = logging.getLogger(__name__)


LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

# One query, all the fields the renderer needs. Keep this short —
# we're not paginating yet, and over-fetching costs Linear's
# complexity budget.
_ISSUE_QUERY = """
query IssuePage($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    url
    updatedAt
    state { name type }
    assignee { name displayName }
    creator { name displayName }
    team { key name }
    priority
    priorityLabel
    labels { nodes { name } }
  }
}
"""


async def _gql(
    client: httpx.AsyncClient,
    token: str,
    query: str,
    variables: Mapping[str, Any],
) -> dict[str, Any]:
    """Execute a Linear GraphQL query and return the ``data`` block.

    Raises :class:`ConnectorConfigError` for the three most common
    operator-actionable failures: 401 (revoked token), 403 (scope
    missing), and GraphQL ``errors[0].extensions.code == "FORBIDDEN"``
    (integration not invited to the team). Anything else bubbles as
    a plain ``httpx.HTTPStatusError`` / ``ConnectorError`` for the
    caller to turn into a 502.
    """

    response = await client.post(
        LINEAR_GRAPHQL_URL,
        json={"query": query, "variables": dict(variables)},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    if response.status_code in (401, 403):
        raise ConnectorConfigError(
            f"linear returned HTTP {response.status_code} — "
            "reconnect the integration or grant read access"
        )
    response.raise_for_status()
    body = response.json()
    errors = body.get("errors") or []
    if errors:
        first = errors[0]
        code = ((first.get("extensions") or {}).get("code") or "").upper()
        msg = first.get("message") or str(first)
        if code in {"FORBIDDEN", "AUTHENTICATION_ERROR"}:
            raise ConnectorConfigError(
                f"linear denied the query: {msg} — is the integration "
                "shared with the issue's team?"
            )
        # Non-auth GraphQL error — surface it as a generic connector
        # error so the endpoint 502s. We do NOT raise ConfigError
        # here because retries won't help; the operator needs to
        # look at the Linear side.
        raise RuntimeError(f"linear GraphQL error: {msg}")
    return body.get("data") or {}


def _priority_label(issue: dict[str, Any]) -> str:
    """Human label for Linear's integer priority (0..4)."""
    label = issue.get("priorityLabel")
    if isinstance(label, str) and label.strip():
        return label
    raw = issue.get("priority")
    mapping = {
        0: "No priority",
        1: "Urgent",
        2: "High",
        3: "Medium",
        4: "Low",
    }
    if isinstance(raw, int):
        return mapping.get(raw, f"Priority {raw}")
    return "No priority"


def _person_name(person: Any) -> str:
    """Pick the displayName if set, else name, else ``unassigned``."""
    if not isinstance(person, Mapping):
        return "unassigned"
    return (
        (person.get("displayName") or person.get("name") or "").strip()
        or "unassigned"
    )


def _render_issue_markdown(issue: dict[str, Any]) -> str:
    identifier = (issue.get("identifier") or "").strip() or "ISSUE"
    title = (issue.get("title") or "Untitled").strip()
    description = (issue.get("description") or "").strip()
    url = (issue.get("url") or "").strip()
    state_name = ((issue.get("state") or {}).get("name") or "").strip() or "Unknown"
    assignee_name = _person_name(issue.get("assignee"))
    team = issue.get("team") or {}
    team_key = (team.get("key") or "").strip()
    team_name = (team.get("name") or "").strip()
    priority = _priority_label(issue)
    updated_at = (issue.get("updatedAt") or "").strip()
    labels = [
        (node.get("name") or "").strip()
        for node in ((issue.get("labels") or {}).get("nodes") or [])
        if isinstance(node, Mapping) and node.get("name")
    ]

    header = f"# {identifier} · {title}" if identifier else f"# {title}"

    # Summary callout line — one compact line so re-syncs with only
    # status/assignee churn still churn deterministically on the
    # same text, instead of scattering state mentions through body.
    summary_bits: list[str] = []
    if url:
        summary_bits.append(f"[Open in Linear]({url})")
    summary_bits.append(f"state: **{state_name}**")
    summary_bits.append(f"assignee: {assignee_name}")

    sections = [header, "> " + " · ".join(summary_bits)]

    if description:
        sections.append(description)
    else:
        sections.append("_(no description)_")

    # Footer block — priority / team / labels / last-update. Kept
    # consistent so content_sha is stable.
    meta: list[str] = []
    meta.append(f"- Priority: {priority}")
    if team_name or team_key:
        meta.append(
            f"- Team: {team_name}" + (f" ({team_key})" if team_key else "")
        )
    if labels:
        meta.append("- Labels: " + ", ".join(sorted(labels)))
    if updated_at:
        meta.append(f"- Last updated: {updated_at}")
    if meta:
        sections.append("\n".join(meta))

    return "\n\n".join(sections).strip() + "\n"


@register("linear")
async def fetch_linear_pages(
    integration: Integration,
    resource_ref: Mapping[str, Any],
    http_client: httpx.AsyncClient | None,
) -> list[ConnectorPage]:
    """Fetcher entry point registered for ``Integration.kind='linear'``.

    v1 shape: ``{"issue_id": "<id or identifier>"}`` only.
    """

    if not isinstance(resource_ref, Mapping):
        raise ConnectorUnsupported("resource_ref must be an object")

    raw_id = resource_ref.get("issue_id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ConnectorUnsupported(
            "linear v1 only supports resource_ref.issue_id (a Linear issue "
            "uuid or identifier like ENG-123)"
        )

    issue_id = raw_id.strip()

    token = safe_decrypt(integration.secret_ciphertext)
    if not token:
        raise ConnectorConfigError(
            "linear integration has no readable access token — "
            "reconnect the integration"
        )

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        data = await _gql(client, token, _ISSUE_QUERY, {"id": issue_id})
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status == 404:
            raise ConnectorConfigError(
                f"linear returned 404 for issue {issue_id} — is the id correct?"
            ) from exc
        raise
    finally:
        if owns_client:
            await client.aclose()

    issue = data.get("issue")
    if not issue:
        # Linear returns ``{"issue": null}`` (with a 200) when the
        # issue either doesn't exist or the token can't see it.
        # Treat as ConfigError so the operator gets a pointer to
        # the share-settings instead of a silent stub.
        raise ConnectorConfigError(
            f"linear issue {issue_id} not visible to the integration — "
            "is the token scoped to the right team?"
        )

    body_md = _render_issue_markdown(issue)
    identifier = (issue.get("identifier") or issue_id).strip()
    title = (issue.get("title") or "Linear issue").strip()
    url = (issue.get("url") or "").strip()

    return [
        ConnectorPage(
            slug=identifier,
            title=f"{identifier} · {title}" if identifier else title,
            body_md=body_md,
            page_ref={
                "issue_id": issue.get("id") or issue_id,
                "identifier": identifier,
                "url": url,
                "updated_at": issue.get("updatedAt") or "",
            },
        )
    ]


__all__ = ["fetch_linear_pages"]
