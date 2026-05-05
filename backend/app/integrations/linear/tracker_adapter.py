"""GraphQL implementation of :class:`TrackerGateway` against Linear.

Linear's API is GraphQL-only. We keep the surface tiny — just the verbs
the pilot pipelines actually call (``list_tickets``, ``transition``,
``comment``). When a future pipeline needs more (subscribe to changes,
attach files), we add a method here rather than on the protocol so we
don't force every other tracker adapter to implement it.

The adapter is constructed *per request* with an already-decrypted
access token (the route layer pulls it from ``Integration
.secret_ciphertext``). We do **not** cache tokens here; integration
secrets rotate via a re-OAuth dance, and the request scope is short
enough that re-fetching the token costs nothing.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any


logger = logging.getLogger(__name__)

import httpx

from backend.app.integrations.gateway.tracker import (
    CommentRef,
    CreatedTicket,
    ListedIssue,
    TicketRef,
)


LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"


class LinearTracker:
    """Per-token adapter implementing :class:`TrackerGateway`.

    Optional ``team_id`` + Ship-FSM maps (set at OAuth time, see
    :mod:`backend.app.services.linear_provisioner`) let the adapter
    accept Ship FSM stage names (``task_intake`` etc.) directly in
    ``list_tickets`` and ``transition``. Without the maps the adapter
    falls back to coarse ``open|closed|all`` semantics.
    """

    def __init__(
        self,
        access_token: str,
        *,
        client: httpx.AsyncClient | None = None,
        team_id: str | None = None,
        team_key: str | None = None,
        label_id_by_stage: dict[str, str] | None = None,
        state_id_by_name: dict[str, str] | None = None,
        fsm_to_linear_state: dict[str, str] | None = None,
        signal_label_ids: dict[str, str] | None = None,
    ) -> None:
        self._token = access_token
        self._client = client
        self._team_id = team_id
        self._team_key = team_key
        self._label_id_by_stage = dict(label_id_by_stage or {})
        self._state_id_by_name = dict(state_id_by_name or {})
        self._fsm_to_linear_state = dict(fsm_to_linear_state or {})
        self._signal_label_ids = dict(signal_label_ids or {})

    def _auth_header(self) -> str:
        """Linear accepts two token shapes with different header
        prefixes: OAuth2 access tokens use ``Bearer <token>``, while
        Personal API keys (``lin_api_*``) ship raw without the
        ``Bearer`` prefix. Sending the wrong shape always 401s — and
        is invisible to legacy callers because the OAuth callback
        writes proper bearer tokens, but a manual / migrated row can
        end up with a PAT and surprise the dashboard. Pick the format
        based on the token's own prefix.
        """
        token = self._token or ""
        if token.startswith("lin_api_"):
            return token
        return f"Bearer {token}"

    async def _gql(
        self, query: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        owns_client = self._client is None
        http = self._client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        try:
            response = await http.post(
                LINEAR_GRAPHQL_URL,
                json={"query": query, "variables": variables or {}},
                headers={
                    "Authorization": self._auth_header(),
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        finally:
            if owns_client:
                await http.aclose()
        if response.status_code >= 400:
            # ``response.raise_for_status()`` swallows the body, which
            # is exactly where Linear puts the *reason* on a 401
            # ("token revoked", "scope insufficient", "expired"). Pull
            # the body in so the operator-facing error explains what
            # to do instead of just "401 Unauthorized".
            snippet = response.text[:300] if response.text else ""
            raise RuntimeError(
                f"Linear HTTP {response.status_code}: {snippet}".strip()
            )
        body = response.json()
        if body.get("errors"):
            # Surface the first GraphQL error verbatim — the route
            # layer already sanitises before sending to the client.
            first = body["errors"][0]
            raise RuntimeError(
                f"Linear GraphQL error: {first.get('message', first)}"
            )
        return body.get("data") or {}

    async def list_tickets(
        self,
        *,
        limit: int = 10,
        state: str | None = None,
        assignee_me: bool = False,
        query: str | None = None,
        assignee: str | None = None,
    ) -> list[dict[str, Any]]:
        """Most-recently-updated issues for the authorised workspace."""
        del assignee  # Linear assignee-by-login not supported in pilot
        first = max(1, min(limit, 50))
        issue_filter: dict[str, Any] | None = None
        parts: list[dict[str, Any]] = []

        raw_state = (state or "all").lower()
        if raw_state == "open":
            parts.append(
                {"state": {"type": {"nin": ["completed", "canceled"]}}}
            )
        elif raw_state in {"closed", "done", "completed"}:
            parts.append(
                {"state": {"type": {"in": ["completed", "canceled"]}}}
            )
        elif raw_state in self._fsm_to_linear_state:
            parts.extend(self._fsm_filter(raw_state))

        # Scope to the configured team — workspace-level OAuth tokens
        # see every team the user is a member of, so without this
        # filter the adapter would happily return tickets from other
        # teams in the same Linear workspace.
        if self._team_id:
            parts.append({"team": {"id": {"eq": self._team_id}}})

        if query and query.strip():
            parts.append(
                {"title": {"containsIgnoreCase": query.strip()}}
            )

        if assignee_me:
            vdata = await self._gql("query { viewer { id } }", {})
            vid = (vdata.get("viewer") or {}).get("id")
            if vid:
                parts.append({"assignee": {"id": {"eq": vid}}})

        if len(parts) == 1:
            issue_filter = parts[0]
        elif len(parts) > 1:
            issue_filter = {"and": parts}
        else:
            issue_filter = None

        nodes: list[dict[str, Any]]
        if issue_filter is None:
            gql = """
            query ShipListIssues($first: Int!) {
              issues(first: $first, orderBy: updatedAt) {
                nodes {
                  id
                  identifier
                  title
                  url
                  state { name type }
                  updatedAt
                }
              }
            }
            """
            data = await self._gql(gql, {"first": first})
            nodes = (data.get("issues") or {}).get("nodes") or []
        else:
            gql = """
            query ShipListIssues($first: Int!, $filter: IssueFilter) {
              issues(first: $first, orderBy: updatedAt, filter: $filter) {
                nodes {
                  id
                  identifier
                  title
                  url
                  state { name type }
                  updatedAt
                }
              }
            }
            """
            data = await self._gql(
                gql, {"first": first, "filter": issue_filter}
            )
            nodes = (data.get("issues") or {}).get("nodes") or []
        nodes = (data.get("issues") or {}).get("nodes") or []
        out: list[dict[str, Any]] = []
        for n in nodes:
            state_name = (n.get("state") or {}).get("name")
            out.append(
                {
                    "id": n.get("identifier") or n.get("id"),
                    "title": n.get("title"),
                    "url": n.get("url"),
                    "status": state_name,
                    "updated_at": n.get("updatedAt"),
                }
            )
        return out

    async def fetch_workflow_states(
        self, *, team_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Return the team's workflow states with id/name/type/color.

        Drives the canonical→native mapping resolver: pairing each of the
        seven Ship canonical states (backlog/planning/executing/reviewing/
        awaiting_input/blocked/closed) with whichever Linear state best
        fits. ``type`` follows Linear's enum (``backlog``/``unstarted``/
        ``started``/``completed``/``canceled``) — the deterministic pass
        keys off it before falling back to the LLM resolver.
        """
        tid = team_id or self._team_id
        if not tid:
            raise ValueError("LinearTracker.fetch_workflow_states needs a team_id")
        data = await self._gql(
            """query ShipFetchStates($teamId: String!) {
              team(id: $teamId) {
                states(first: 50) {
                  nodes { id name type color position }
                }
              }
            }""",
            {"teamId": tid},
        )
        nodes = (
            (((data.get("team") or {}).get("states")) or {}).get("nodes") or []
        )
        return [
            {
                "id": str(n.get("id") or ""),
                "name": str(n.get("name") or ""),
                "type": str(n.get("type") or ""),
                "color": str(n.get("color") or ""),
                "position": n.get("position"),
            }
            for n in nodes
            if n.get("id") and n.get("name")
        ]

    async def fetch_sample_titles_per_state(
        self,
        *,
        team_id: str | None = None,
        per_state_limit: int = 3,
    ) -> dict[str, list[str]]:
        """Pull a few recent issue titles per workflow state.

        Used to disambiguate states whose ``type`` alone doesn't tell us
        which canonical bucket they belong to (e.g. a team with multiple
        ``started`` states like "In Progress" / "Code Review" / "QA"). The
        LLM resolver gets the titles as evidence so it can pick the right
        canonical match. Capped at three per state to keep the prompt
        lean — full backlog scans aren't useful for the resolver.
        """
        tid = team_id or self._team_id
        if not tid:
            raise ValueError(
                "LinearTracker.fetch_sample_titles_per_state needs a team_id"
            )
        first = max(1, min(per_state_limit, 10))
        data = await self._gql(
            """query ShipSampleTitles($teamId: String!, $first: Int!) {
              team(id: $teamId) {
                states(first: 50) {
                  nodes {
                    id
                    issues(first: $first, orderBy: updatedAt) {
                      nodes { title }
                    }
                  }
                }
              }
            }""",
            {"teamId": tid, "first": first},
        )
        nodes = (
            (((data.get("team") or {}).get("states")) or {}).get("nodes") or []
        )
        out: dict[str, list[str]] = {}
        for state in nodes:
            sid = str(state.get("id") or "")
            if not sid:
                continue
            issue_nodes = (
                (state.get("issues") or {}).get("nodes") or []
            )
            titles: list[str] = []
            for issue in issue_nodes:
                t = (issue.get("title") or "").strip()
                if t:
                    titles.append(t[:200])
            out[sid] = titles
        return out

    def _fsm_filter(self, stage: str) -> list[dict[str, Any]]:
        """Translate a Ship FSM stage name into Linear filter parts.

        Convention (one label namespace, ``stage:<fsm>``):

        * ``stage:<X>`` is added when role X has finished its work on the
          ticket. The label is "this role is done", not "this role is
          assigned".
        * To pick a ticket for stage X:
            - Linear state matches ``FSM_TO_LINEAR_STATE[X]``.
            - The ``stage:<previous>`` label is present (previous role
              has produced its output) — except for the entry stage,
              which has no previous.
            - The ``stage:<X>`` label is **not** present (this role
              hasn't run on this ticket yet) — guarantees idempotency.

        ``self_heal`` is intentionally not in ``FSM_STAGE_ORDER``; it
        runs out-of-band against any open ticket and falls back to the
        coarse "open" filter via ``list_tickets(state="open")``.
        """
        from backend.app.services.linear_provisioner import previous_stage

        parts: list[dict[str, Any]] = []
        target_state_name = self._fsm_to_linear_state.get(stage)
        if target_state_name and target_state_name in self._state_id_by_name:
            parts.append(
                {"state": {"id": {"eq": self._state_id_by_name[target_state_name]}}}
            )

        # "this role hasn't finished yet" — never re-pick.
        own_label = self._label_id_by_stage.get(stage)
        if own_label:
            parts.append({"labels": {"id": {"nin": [own_label]}}})

        # "previous role is done" — for non-entry stages.
        prev = previous_stage(stage)
        if prev:
            prev_label = self._label_id_by_stage.get(prev)
            if prev_label:
                parts.append(
                    {"labels": {"some": {"id": {"eq": prev_label}}}}
                )

        # ``needs:clarification`` is a hold signal — the agent posted a
        # question for a human. Skip these tickets at every stage so we
        # don't repeatedly comment on a ticket that's waiting on a human
        # answer. The label gets cleared by the human (Linear UI) when
        # they reply.
        clar = self._signal_label_ids.get("needs_clarification")
        if clar:
            parts.append({"labels": {"id": {"nin": [clar]}}})
        return parts

    async def add_signal_label(self, ticket: TicketRef, *, key: str) -> None:
        """Tag ``ticket`` with the signal label identified by ``key``.

        ``key`` is one of :data:`SIGNAL_LABELS` (``needs_clarification``).
        Uses ``addedLabelIds`` so unrelated labels (priority, area, the
        FSM stage label) aren't clobbered.
        """
        if ticket.kind != "linear":
            raise ValueError(f"LinearTracker can't tag kind={ticket.kind}")
        label_id = self._signal_label_ids.get(key)
        if not label_id:
            raise ValueError(
                f"Signal label {key!r} is not provisioned for this team. "
                "Re-run OAuth or call the provisioner."
            )
        await self._gql(
            """mutation ShipAddSignal($id: String!, $input: IssueUpdateInput!) {
              issueUpdate(id: $id, input: $input) { success }
            }""",
            {
                "id": ticket.id,
                "input": {"addedLabelIds": [label_id]},
            },
        )

    async def transition(self, ticket: TicketRef, *, to_state: str) -> None:
        """Move ``ticket`` to a Ship FSM stage (or a Linear state name).

        When ``to_state`` matches a configured FSM stage:
          - swaps the ``stage:<fsm>`` label (drops every other Ship
            stage label, adds the target's),
          - moves the Linear workflow state per
            ``fsm_to_linear_state``.

        When ``to_state`` is a literal Linear state name (e.g. ``Done``,
        ``Canceled``), the legacy "find state by name on the issue's
        team" path is used. Agents must NOT pass ``Done`` — only humans
        move tickets out of Review. The adapter does not enforce this;
        the route layer does.
        """
        if ticket.kind != "linear":
            raise ValueError(f"LinearTracker can't transition kind={ticket.kind}")

        # FSM-aware path.
        if to_state in self._fsm_to_linear_state:
            target_state_name = self._fsm_to_linear_state[to_state]
            target_state_id = self._state_id_by_name.get(target_state_name)
            target_label_id = self._label_id_by_stage.get(to_state)
            if not target_state_id:
                raise ValueError(
                    f"Linear state {target_state_name!r} not provisioned for this team"
                )
            # Build the new label set: drop all ``stage:*`` labels we
            # know about, then add the target's. This makes the
            # transition idempotent on re-runs.
            other_label_ids = list(
                set(self._label_id_by_stage.values())
                - ({target_label_id} if target_label_id else set())
            )
            await self._gql(
                """mutation ShipFsmTransition($id: String!, $input: IssueUpdateInput!) {
                  issueUpdate(id: $id, input: $input) { success }
                }""",
                {
                    "id": ticket.id,
                    "input": {
                        "stateId": target_state_id,
                        **(
                            {
                                "labelIds": [target_label_id]
                                if target_label_id
                                else []
                            }
                            if target_label_id
                            else {}
                        ),
                    },
                },
            )
            # Linear's ``labelIds`` is a SET operation when present —
            # passing the target replaces all labels with that one set,
            # which is what we want for stage labels but loses any
            # other labels (priority, area). Drop the simplification:
            # use ``addedLabelIds`` + ``removedLabelIds`` if they exist
            # in the schema. (Linear's ``IssueUpdateInput`` exposes
            # ``addedLabelIds`` / ``removedLabelIds`` since 2023-Q3.)
            # We re-issue here with the surgical add/remove so we don't
            # clobber unrelated labels. The first call already moved
            # the workflow state; this second call only adjusts labels.
            mut = await self._gql(
                """mutation ShipFsmLabels($id: String!, $input: IssueUpdateInput!) {
                  issueUpdate(id: $id, input: $input) { success }
                }""",
                {
                    "id": ticket.id,
                    "input": {
                        **(
                            {"removedLabelIds": other_label_ids}
                            if other_label_ids
                            else {}
                        ),
                        **(
                            {"addedLabelIds": [target_label_id]}
                            if target_label_id
                            else {}
                        ),
                    },
                },
            )
            del mut
            return

        # Legacy path (Linear state name).
        resolve = """
        query ShipResolveState($id: String!, $name: String!) {
          issue(id: $id) { id team { id states(filter: {name: {eq: $name}}) {
            nodes { id }
          } } }
        }
        """
        data = await self._gql(resolve, {"id": ticket.id, "name": to_state})
        states = (
            ((data.get("issue") or {}).get("team") or {})
            .get("states")
            or {}
        ).get("nodes") or []
        if not states:
            raise ValueError(
                f"Linear state {to_state!r} not found on issue {ticket.id}"
            )
        state_id = states[0]["id"]
        mutation = """
        mutation ShipTransition($id: String!, $stateId: String!) {
          issueUpdate(id: $id, input: { stateId: $stateId }) { success }
        }
        """
        await self._gql(mutation, {"id": ticket.id, "stateId": state_id})

    async def set_description(self, ticket: TicketRef, *, body: str) -> None:
        """Replace the issue's description (markdown body).

        Used by stage agents that *shape the ticket itself* (intake,
        BA) instead of appending comments. The previous body is not
        preserved here — Linear keeps issue history server-side, so
        the operator can always see what changed via the activity feed.
        """
        if ticket.kind != "linear":
            raise ValueError(
                f"LinearTracker can't set_description for kind={ticket.kind}"
            )
        await self._gql(
            """mutation ShipSetDescription($id: String!, $body: String!) {
              issueUpdate(id: $id, input: { description: $body }) { success }
            }""",
            {"id": ticket.id, "body": body},
        )

    async def comment(self, ticket: TicketRef, *, body: str) -> None:
        if ticket.kind != "linear":
            raise ValueError(f"LinearTracker can't comment on kind={ticket.kind}")
        mutation = """
        mutation ShipComment($id: String!, $body: String!) {
          commentCreate(input: { issueId: $id, body: $body }) { success }
        }
        """
        await self._gql(mutation, {"id": ticket.id, "body": body})

    async def create_ticket(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        project_hint: str | None = None,
        project_id: str | None = None,
    ) -> CreatedTicket:
        """Create a Linear issue under the requested team.

        ``project_hint`` accepts either a team UUID or a team key
        (``ENG``) — we resolve to UUID up front because the
        ``issueCreate`` mutation requires a UUID. When the user
        omits a hint and the workspace has exactly one team, we
        pick it; otherwise we raise so the caller surfaces a
        "which team?" prompt rather than silently landing the
        ticket in the wrong inbox.

        ``labels`` are resolved by name within the team's label
        set. Unknown labels are dropped (not auto-created) — we
        don't want the agent polluting a customer's Linear with
        freshly-invented tag names.

        ``project_id`` (Linear project UUID) attaches the new ticket
        to an epic so child tickets can stay short and pull motivation
        / scope / decisions from the project body.
        """
        team_id = await self._resolve_team_id(project_hint)
        label_ids = (
            await self._resolve_label_ids(team_id, labels) if labels else []
        )

        mutation = """
        mutation ShipCreateIssue($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier url }
          }
        }
        """
        input_payload: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "description": body,
        }
        if label_ids:
            input_payload["labelIds"] = label_ids
        if project_id:
            input_payload["projectId"] = project_id

        data = await self._gql(mutation, {"input": input_payload})
        issue = ((data.get("issueCreate") or {}).get("issue")) or {}
        if not (data.get("issueCreate") or {}).get("success") or not issue:
            raise ValueError("Linear refused issueCreate (no issue returned).")

        return CreatedTicket(
            ref=TicketRef(
                kind="linear",
                workspace_hint=team_id,
                id=str(issue["id"]),
            ),
            url=str(issue.get("url") or ""),
            display_id=str(issue.get("identifier") or issue["id"]),
        )

    # -----------------------------------------------------------------
    # Project surface (epics)
    #
    # PO ideas / scope / motivation / decisions live in the project
    # body so child tickets stay short and pull context from the epic.
    # Linear's ``projectCreate`` returns ``description`` capped at 255
    # chars; the markdown body lives on ``content``.
    # -----------------------------------------------------------------

    async def list_projects(
        self,
        *,
        limit: int = 50,
        state: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        """Active projects on the connected team.

        ``state`` filters Linear's project state (``backlog``,
        ``planned``, ``started``, ``paused``, ``completed``,
        ``canceled``). ``query`` matches project name (case-insensitive
        contains). Returns ``{"id", "name", "slug", "state", "url",
        "updated_at", "lead_name", "progress", "scope", "color"}`` per
        project — the last three are dashboard-only enrichment that
        Linear may reject on certain workspace tiers; we fall back to
        the basic shape (``progress``/``scope``/``color`` = ``None``)
        rather than fail the call when they error out, so a connected
        Linear that can't serve the rich fields still renders the
        prioritizer list.

        ``progress`` is the 0-1 fraction Linear reports; ``scope`` is the
        total magnitude (issue count when no estimates are set, otherwise
        weighted). The dashboard prioritizer renders ``round(progress *
        scope) / round(scope)`` as a fraction.
        """
        # Only narrow to a specific team when one is actually
        # configured. Calling ``_resolve_team_id(None)`` blindly used
        # to fire a ``teams(first: 2)`` probe against Linear — fine
        # for tokens with the ``read`` scope but a 401 with
        # ``Authentication required`` for narrower OAuth scopes that
        # don't grant team-listing access. The dashboard prioritizer
        # is fine showing every project the OAuth user can see, so
        # we drop the filter when no default team is bound.
        filter_clauses: dict[str, Any] = {}
        if self._team_id:
            filter_clauses["accessibleTeams"] = {
                "some": {"id": {"eq": self._team_id}}
            }
        if state:
            filter_clauses["state"] = {"eq": state}
        if query:
            filter_clauses["name"] = {"containsIgnoreCase": query}

        rich_query = """
        query ShipListProjects($filter: ProjectFilter, $first: Int!) {
          projects(filter: $filter, first: $first, orderBy: updatedAt) {
            nodes {
              id
              name
              slugId
              state
              url
              updatedAt
              color
              progress
              scope
              lead { name }
            }
          }
        }
        """
        basic_query = """
        query ShipListProjects($filter: ProjectFilter, $first: Int!) {
          projects(filter: $filter, first: $first, orderBy: updatedAt) {
            nodes {
              id
              name
              slugId
              state
              url
              updatedAt
              lead { name }
            }
          }
        }
        """
        variables = {
            # GraphQL accepts a null filter when no narrowing is
            # needed; sending ``{}`` works in current Linear builds
            # but ``null`` is the canonical "no filter" shape.
            "filter": filter_clauses or None,
            "first": min(limit, 100),
        }
        try:
            data = await self._gql(rich_query, variables)
        except RuntimeError as exc:
            # Linear rejected one of the enrichment fields (the only
            # way ``_gql`` raises here is a ``errors[0].message`` from
            # the GraphQL response). Re-issue the call with the basic
            # shape and let the dashboard render without bars.
            logger.warning(
                "linear list_projects rich query rejected, falling back: %s",
                exc,
            )
            data = await self._gql(basic_query, variables)

        nodes = ((data.get("projects") or {}).get("nodes")) or []
        return [
            {
                "id": str(node.get("id") or ""),
                "name": str(node.get("name") or ""),
                "slug": str(node.get("slugId") or ""),
                "state": str(node.get("state") or ""),
                "url": str(node.get("url") or ""),
                "updated_at": node.get("updatedAt"),
                "lead_name": ((node.get("lead") or {}).get("name")) or None,
                "progress": (
                    float(node["progress"])
                    if node.get("progress") is not None
                    else None
                ),
                "scope": (
                    float(node["scope"])
                    if node.get("scope") is not None
                    else None
                ),
                "color": str(node.get("color") or "") or None,
            }
            for node in nodes
        ]

    async def get_project(
        self, project_id: str, *, issues_limit: int = 25
    ) -> dict[str, Any]:
        """Project body + linked tickets.

        Returns ``{"id", "name", "slug", "state", "url", "description"
        (short blurb), "content" (markdown body), "lead_name",
        "issues": [{...display_id, title, state, url}]}``. Use this
        when filing a child ticket so the agent can verify the project
        is the right epic before linking.
        """
        gql_query = """
        query ShipGetProject($id: String!, $issuesFirst: Int!) {
          project(id: $id) {
            id
            name
            slugId
            state
            url
            description
            content
            lead { name }
            issues(first: $issuesFirst, orderBy: updatedAt) {
              nodes {
                id
                identifier
                title
                url
                state { name }
              }
            }
          }
        }
        """
        data = await self._gql(
            gql_query, {"id": project_id, "issuesFirst": min(issues_limit, 50)}
        )
        node = data.get("project") or {}
        if not node:
            raise ValueError(f"Linear project not found: {project_id}")
        issues = [
            {
                "id": str(issue.get("id") or ""),
                "display_id": str(issue.get("identifier") or ""),
                "title": str(issue.get("title") or ""),
                "url": str(issue.get("url") or ""),
                "state": ((issue.get("state") or {}).get("name")) or "",
            }
            for issue in ((node.get("issues") or {}).get("nodes") or [])
        ]
        return {
            "id": str(node.get("id") or ""),
            "name": str(node.get("name") or ""),
            "slug": str(node.get("slugId") or ""),
            "state": str(node.get("state") or ""),
            "url": str(node.get("url") or ""),
            "description": str(node.get("description") or ""),
            "content": str(node.get("content") or ""),
            "lead_name": ((node.get("lead") or {}).get("name")) or None,
            "issues": issues,
        }

    async def create_project(
        self,
        *,
        name: str,
        body: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a Linear project (epic) under the connected team.

        ``body`` is the markdown content (full epic body). Linear's
        ``description`` field is a 255-char one-liner — we derive it
        from the first body line when the caller omits it. Returns
        ``{"id", "url", "name", "slug"}``.
        """
        team_id = await self._resolve_team_id(None)
        short = (description or body.splitlines()[0] if body else "")[:240]
        mutation = """
        mutation ShipCreateProject($input: ProjectCreateInput!) {
          projectCreate(input: $input) {
            success
            project { id name slugId url }
          }
        }
        """
        data = await self._gql(
            mutation,
            {
                "input": {
                    "name": name,
                    "teamIds": [team_id],
                    "description": short,
                    "content": body,
                }
            },
        )
        result = data.get("projectCreate") or {}
        project = result.get("project") or {}
        if not result.get("success") or not project:
            raise ValueError("Linear refused projectCreate (no project returned).")
        return {
            "id": str(project.get("id") or ""),
            "name": str(project.get("name") or name),
            "slug": str(project.get("slugId") or ""),
            "url": str(project.get("url") or ""),
        }

    async def append_project_description(
        self, project_id: str, *, body: str
    ) -> None:
        """Append ``body`` to the project's markdown content.

        We fetch the current content first so each append accumulates
        instead of replacing — PO ideas should pile up over the life
        of the epic, not overwrite each other. Empty current body
        becomes ``body`` verbatim; otherwise we add a blank line and
        then ``body``.
        """
        existing = (await self.get_project(project_id)).get("content") or ""
        new_content = (existing + "\n\n" + body).lstrip() if existing else body
        mutation = """
        mutation ShipUpdateProject($id: String!, $content: String!) {
          projectUpdate(id: $id, input: { content: $content }) { success }
        }
        """
        await self._gql(mutation, {"id": project_id, "content": new_content})

    # -----------------------------------------------------------------
    # Clarifications projection surface (D13)
    # -----------------------------------------------------------------

    async def list_issues_with_label(
        self, label: str, *, limit: int = 100
    ) -> list[ListedIssue]:
        """Open issues carrying ``label``.

        We filter on Linear's ``labels.name`` server-side so the
        projection cron doesn't pull the full backlog and filter
        client-side. ``state.type != 'completed'`` because a
        done/cancelled ticket with the ``ship:needs-clarification``
        label is almost certainly a stale marker — the cron will
        mark the matching Ship row ``stale`` without erroring.
        """
        query = """
        query ShipLabelledIssues($label: String!, $first: Int!) {
          issues(
            first: $first,
            filter: {
              labels: { name: { eq: $label } },
              state: { type: { nin: ["completed","canceled"] } }
            },
            orderBy: updatedAt
          ) {
            nodes {
              id
              identifier
              url
              team { id }
            }
          }
        }
        """
        data = await self._gql(
            query, {"label": label, "first": max(1, min(limit, 100))}
        )
        nodes = (data.get("issues") or {}).get("nodes") or []
        out: list[ListedIssue] = []
        for node in nodes:
            if not node.get("id"):
                continue
            out.append(
                ListedIssue(
                    ref=TicketRef(
                        kind="linear",
                        workspace_hint=((node.get("team") or {}).get("id")),
                        id=str(node["id"]),
                    ),
                    display_id=str(node.get("identifier") or node["id"]),
                    url=node.get("url"),
                )
            )
        return out

    async def get_ticket_snapshot(
        self, ticket: TicketRef
    ) -> dict[str, Any] | None:
        """Cheap snapshot of an issue's display fields.

        Used by the agent-finish handler to attach a source-ticket
        preview to inbox rows (clarifications, blockers) so the
        operator doesn't have to flip to Linear to remember which
        ticket the agent's question is about. Returns ``None`` if
        the issue can't be resolved.
        """
        if ticket.kind != "linear":
            return None
        query = """
        query ShipTicketSnapshot($id: String!) {
          issue(id: $id) {
            id
            identifier
            title
            description
            url
            state { name }
            labels { nodes { name } }
          }
        }
        """
        try:
            data = await self._gql(query, {"id": ticket.id})
        except Exception:
            return None
        issue = data.get("issue") or None
        if not issue:
            return None
        return {
            "ticket_ref": issue.get("identifier") or ticket.id,
            "title": issue.get("title"),
            "description": issue.get("description"),
            "url": issue.get("url"),
            "state": (issue.get("state") or {}).get("name"),
            "labels": [
                lbl.get("name")
                for lbl in (issue.get("labels") or {}).get("nodes") or []
                if lbl.get("name")
            ],
        }

    async def list_comments(self, ticket: TicketRef) -> list[CommentRef]:
        """All comments on the issue, oldest first.

        Linear's ``issue(id:).comments`` paginates by 50; for the
        clarifications path we cap at 100 because the projection
        only cares about recent exchange (and a Linear issue with
        200 comments is unusual). If that ever bites, add a cursor
        loop — the sync service doesn't care about the mechanics.
        """
        if ticket.kind != "linear":
            raise ValueError(
                f"LinearTracker can't list_comments for kind={ticket.kind}"
            )
        query = """
        query ShipComments($id: String!) {
          issue(id: $id) {
            comments(first: 100) {
              nodes {
                id
                body
                createdAt
                url
                user { displayName email }
              }
            }
          }
        }
        """
        data = await self._gql(query, {"id": ticket.id})
        nodes = (
            ((data.get("issue") or {}).get("comments") or {}).get("nodes") or []
        )
        out: list[CommentRef] = []
        for node in nodes:
            created_raw = node.get("createdAt")
            try:
                created_at = _parse_iso8601(created_raw) if created_raw else datetime.min
            except ValueError:
                created_at = datetime.min
            user = node.get("user") or {}
            out.append(
                CommentRef(
                    id=str(node.get("id") or ""),
                    body=str(node.get("body") or ""),
                    author=(user.get("displayName") or user.get("email")),
                    created_at=created_at,
                    url=node.get("url"),
                )
            )
        out.sort(key=lambda c: c.created_at)
        return out

    async def remove_label(self, ticket: TicketRef, label: str) -> None:
        """Strip ``label`` from the issue (no-op if not present)."""
        if ticket.kind != "linear":
            raise ValueError(
                f"LinearTracker can't remove_label for kind={ticket.kind}"
            )
        # Linear removes labels via ``issueUpdate`` with the residual
        # label id set (no dedicated removeLabel mutation). Fetch the
        # current list, strip the match, push it back.
        resolve = """
        query ShipIssueLabels($id: String!) {
          issue(id: $id) {
            labels { nodes { id name } }
          }
        }
        """
        data = await self._gql(resolve, {"id": ticket.id})
        nodes = (
            ((data.get("issue") or {}).get("labels") or {}).get("nodes") or []
        )
        wanted = label.lower()
        remaining = [
            str(n["id"]) for n in nodes
            if str(n.get("name", "")).lower() != wanted
        ]
        if len(remaining) == len(nodes):
            return  # Label wasn't attached; nothing to do.
        mutation = """
        mutation ShipStripLabel($id: String!, $labelIds: [String!]!) {
          issueUpdate(id: $id, input: { labelIds: $labelIds }) { success }
        }
        """
        await self._gql(mutation, {"id": ticket.id, "labelIds": remaining})

    # -----------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------

    async def _resolve_team_id(self, hint: str | None) -> str:
        """Accept ``None`` / UUID / team key, return a team UUID.

        Resolution order when no ``hint`` is given:

        1. ``self._team_id`` — workspace-level default stored on the
           ``Integration.config`` row (set during OAuth probe). This
           is the load-bearing case for the Navigator: the operator
           configured Linear once, picked the team, and now expects
           every ``create_ticket`` call to land there. Pre-fix the
           resolver dropped through to the "first team" lookup and
           crashed if the workspace had more than one team — even
           though the right answer was sitting on the adapter.
        2. ``self._team_key`` — same source, used as a fallback if
           ``team_id`` happens to be missing but ``team_key`` is set
           (older integration rows; cheap to keep the cushion).
        3. "Single team" auto-pick. Only safe when the Linear
           workspace literally has one team.
        """
        if hint:
            # If it already looks like a UUID, trust it; Linear IDs
            # are 36-char UUIDs, team keys are uppercase short codes.
            if len(hint) >= 32 and "-" in hint:
                return hint
            lookup = """
            query ShipResolveTeam($key: String!) {
              teams(filter: {key: {eq: $key}}) { nodes { id } }
            }
            """
            data = await self._gql(lookup, {"key": hint})
            nodes = (data.get("teams") or {}).get("nodes") or []
            if not nodes:
                raise ValueError(f"Linear team {hint!r} not found.")
            return str(nodes[0]["id"])
        # No hint — prefer the workspace-level default the integration
        # row already stores.
        if self._team_id:
            return self._team_id
        if self._team_key:
            lookup = """
            query ShipResolveTeam($key: String!) {
              teams(filter: {key: {eq: $key}}) { nodes { id } }
            }
            """
            data = await self._gql(lookup, {"key": self._team_key})
            nodes = (data.get("teams") or {}).get("nodes") or []
            if nodes:
                return str(nodes[0]["id"])
        # Last resort — single-team workspaces.
        first = """
        query ShipFirstTeam { teams(first: 2) { nodes { id key } } }
        """
        data = await self._gql(first)
        nodes = (data.get("teams") or {}).get("nodes") or []
        if len(nodes) != 1:
            raise ValueError(
                "Linear workspace has multiple teams and no default is "
                "configured; pass project_hint=<team-key or id>, or set "
                "the default team on the workspace's Linear integration."
            )
        return str(nodes[0]["id"])

    async def _resolve_label_ids(
        self, team_id: str, labels: list[str]
    ) -> list[str]:
        if not labels:
            return []
        query = """
        query ShipLabels($teamId: String!) {
          team(id: $teamId) { labels { nodes { id name } } }
        }
        """
        data = await self._gql(query, {"teamId": team_id})
        nodes = (
            ((data.get("team") or {}).get("labels") or {}).get("nodes") or []
        )
        want = {lbl.lower() for lbl in labels}
        return [str(n["id"]) for n in nodes if str(n.get("name", "")).lower() in want]


def _parse_iso8601(raw: str) -> datetime:
    """Parse Linear's ``createdAt`` (ISO-8601 with ``Z`` suffix).

    ``datetime.fromisoformat`` only accepts ``+00:00`` before 3.11;
    we're on 3.12 but Linear still ships the ``Z``. Normalise it
    before handing off to keep the adapter portable.
    """
    cleaned = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    return datetime.fromisoformat(cleaned)


__all__ = ["LinearTracker", "LINEAR_GRAPHQL_URL"]
