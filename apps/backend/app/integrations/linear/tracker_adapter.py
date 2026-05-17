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
from typing import Any, Literal


logger = logging.getLogger(__name__)

import httpx

from backend.app.integrations.gateway.tracker import (
    CommentRef,
    CreatedTicket,
    ListedIssue,
    TicketRef,
)


LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"


def _identifier_sort_key(row: dict[str, Any]) -> tuple[int, str, int, str]:
    """Sort key for ``list_tickets`` rows by team + numeric suffix.

    ``ELS-11`` → ``(0, "ELS", 11, "ELS-11")``; ``ELS-12`` ranks after.
    ``ELS-101`` correctly ranks after ``ELS-99`` because the middle
    element is parsed as an int.

    The leading rank slot pushes garbage rows (missing / non-string
    id) to the end of the list so real tickets are served first; a
    None id would otherwise sort to position zero and starve the
    queue. Within the "garbage" bucket order is left to the natural
    tuple comparison so it remains deterministic.
    """
    ident = row.get("id")
    if not isinstance(ident, str) or not ident:
        return (1, "", 0, "")
    if "-" not in ident:
        return (0, ident, 0, ident)
    team, suffix = ident.rsplit("-", 1)
    try:
        num = int(suffix)
    except ValueError:
        return (0, team, 0, ident)
    return (0, team, num, ident)


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
        # ``team.issueTypes`` is feature-gated on Linear's plan tier;
        # the probe can succeed-but-empty, succeed-with-results, or
        # fail with a GraphQL schema-level error on tokens whose
        # workspace doesn't expose the field at all. We cache per-team
        # so each ``create_ticket`` call doesn't re-pay the round-trip;
        # ``None`` is the "probe failed (use label fallback)" sentinel.
        self._issue_types_by_team: dict[
            str, dict[str, str] | None
        ] = {}

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
        """Most-recently-updated batch, returned sorted by identifier.

        We ask Linear for the most-recently-updated slice (Linear's
        ``orderBy`` is descending-only on the indexed fields it
        supports) and then sort the returned rows by identifier
        ascending — ``ELS-11`` ahead of ``ELS-12`` ahead of
        ``ELS-101`` etc. The picker walks the result in order and
        stops at the first eligible ticket; FIFO-by-identifier gives
        a deterministic "older filings get worked first" feel that
        matches operator intuition (and the way Linear's own UI
        renders Backlog).

        We deliberately fetch up to ~5× the caller's ``limit`` (capped
        at 50) before sorting: picking the lowest identifier among the
        10 most-recently-updated tickets is not the same as picking
        the lowest identifier from the eligible queue, and over-fetch
        keeps the sort meaningful when the most-recent slice is
        skewed toward a busy ticket.
        """
        del assignee  # Linear assignee-by-login not supported in pilot
        fetch_size = min(max(limit * 5, 20), 50)
        first = max(1, fetch_size)
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
                  description
                  url
                  state { name type }
                  project { id }
                  labels { nodes { name } }
                  updatedAt
                }
              }
            }
            """
            data = await self._gql(gql, {"first": first})
            nodes = (data.get("issues") or {}).get("nodes") or []
        else:
            # ``description`` MUST stay in the projection. The picker
            # always reaches this filtered branch in production (team_id
            # gate alone makes ``issue_filter`` non-empty), and a
            # missing ``description`` field here means every SDLC
            # agent receives ``body=None`` in its prompt — observed as
            # intake / BA opening ``needs_clarification`` saying "the
            # ticket body is empty" on tickets that actually had a
            # populated description in Linear. The unfiltered branch
            # above carries it; if you edit one projection, edit both.
            gql = """
            query ShipListIssues($first: Int!, $filter: IssueFilter) {
              issues(first: $first, orderBy: updatedAt, filter: $filter) {
                nodes {
                  id
                  identifier
                  title
                  description
                  url
                  state { name type }
                  project { id }
                  labels { nodes { name } }
                  updatedAt
                }
              }
            }
            """
            data = await self._gql(
                gql, {"first": first, "filter": issue_filter}
            )
            nodes = (data.get("issues") or {}).get("nodes") or []
        out: list[dict[str, Any]] = []
        for n in nodes:
            state_name = (n.get("state") or {}).get("name")
            project_id = (n.get("project") or {}).get("id")
            label_nodes = (n.get("labels") or {}).get("nodes") or []
            labels = [
                str(item.get("name") or "") for item in label_nodes
                if isinstance(item, dict) and item.get("name")
            ]
            out.append(
                {
                    "id": n.get("identifier") or n.get("id"),
                    "title": n.get("title"),
                    # Agent prompts render the ticket body as
                    # ``{{DESCRIPTION}}``; without this the prompt sees
                    # ``null`` and the intake/BA roles fall back to
                    # title-only heuristics or ``needs_clarification``
                    # — even when the operator already wrote a full
                    # description in Linear.
                    "body": n.get("description"),
                    "url": n.get("url"),
                    "status": state_name,
                    "updated_at": n.get("updatedAt"),
                    # ELS-83: surface ``project_id`` so the agent picker
                    # can reject orphans (tickets created outside the
                    # dashboard flow that have no project attached).
                    # ``None`` when the issue isn't part of any Linear
                    # project — the picker treats that as "skip".
                    "project_id": project_id,
                    # Labels surface ELS-84 (clarification/blocked overlays)
                    # filtering one layer up; see ``signal_labels`` keys
                    # in linear_provisioner.py.
                    "labels": labels,
                }
            )
        # Sort by identifier ascending (FIFO by Linear filing order)
        # then truncate to the caller's requested ``limit``. The
        # over-fetch above ensures we're sorting from a candidate
        # pool larger than ``limit`` so the first eligible row is
        # truly the lowest-numbered one and not just the lowest-
        # numbered among the freshly touched.
        out.sort(key=_identifier_sort_key)
        return out[:limit]

    async def list_project_tickets_in_state(
        self,
        *,
        project_id: str,
        linear_state_name: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List tickets attached to ``project_id`` whose Linear workflow
        state matches ``linear_state_name`` (case-sensitive match against
        the team's state names — ``Backlog`` / ``Todo`` / ``In Progress`` /
        ``Review`` / ``Done`` / etc.).

        Used by the project-state ↔ ticket-state sync helper (Linear
        ELS-91) to bulk-move children when a project flips Active ↔
        Parked. We deliberately enumerate by **Linear state name**
        instead of FSM stage, because the sync moves between Backlog
        and Todo regardless of which FSM stage the ticket is in
        (any stage assigned to ``Todo`` should park together).

        Returns a flat list of ``{"id", "identifier", "title", "url",
        "state_name"}`` dicts. ``id`` is the Linear UUID (suitable for
        ``transition``); ``identifier`` is the human ref (``ELS-99``).

        Hard-capped at ``limit`` (default 100). Pagination not exposed
        — projects with >100 children in a single Linear state would be
        unusual; if it ever comes up we add a cursor loop.
        """
        if not project_id:
            return []
        target_state_id = self._state_id_by_name.get(linear_state_name)
        if not target_state_id:
            # The team may not have provisioned this state name; bail
            # quietly so the sync degrades to a no-op rather than 5xx.
            return []
        gql = """
        query ShipProjectTicketsInState(
            $first: Int!,
            $filter: IssueFilter
        ) {
          issues(first: $first, orderBy: updatedAt, filter: $filter) {
            nodes {
              id
              identifier
              title
              url
              state { name }
            }
          }
        }
        """
        issue_filter: dict[str, Any] = {
            "and": [
                {"project": {"id": {"eq": project_id}}},
                {"state": {"id": {"eq": target_state_id}}},
            ]
        }
        if self._team_id:
            issue_filter["and"].append({"team": {"id": {"eq": self._team_id}}})
        first = max(1, min(limit, 250))
        data = await self._gql(gql, {"first": first, "filter": issue_filter})
        out: list[dict[str, Any]] = []
        for n in (data.get("issues") or {}).get("nodes") or []:
            out.append(
                {
                    "id": str(n.get("id") or ""),
                    "identifier": str(n.get("identifier") or ""),
                    "title": str(n.get("title") or ""),
                    "url": str(n.get("url") or ""),
                    "state_name": str((n.get("state") or {}).get("name") or ""),
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
              has produced its output) — except for the entry stages
              (``task_intake`` / ``bug_triage``), which have no
              previous.
            - The ``stage:<X>`` label is **not** present (this role
              hasn't run on this ticket yet) — guarantees idempotency.

        Entry-stage asymmetry (ELS-90 follow-up): ``task_intake`` is
        the **default** entry — it picks any Todo ticket that hasn't
        been classified yet, including Linear-native tickets the
        operator filed directly with no Ship labels at all. The
        previous-stage check is skipped for entry stages, so the
        filter becomes purely "Todo + own-stage label not present".
        ``bug_triage`` is the **explicit** entry — operator labels a
        ticket ``stage:bug_triage`` themselves to route it through bug
        intake; the standard idempotency rule applies.

        Empty-collection trap (ELS-90): Linear's
        ``IssueLabelCollectionFilter`` interprets a bare
        ``{"labels": {"id": {"nin": [...]}}}`` as ``some`` semantics —
        a label exists whose id is not in the list. On a ticket with
        **no labels at all**, that's vacuously false, so the
        unlabeled ticket is excluded.

        We previously used the ``none`` operator to encode "doesn't
        have this label", but Linear removed ``none`` from
        ``IssueLabelCollectionFilter`` (the API now rejects it as an
        invalid field with "Did you mean 'name' or 'some'?"). The
        equivalent shape is ``every: {id: {neq: X}}`` — *every*
        label's id differs from X, which is vacuously true on an empty
        collection (every element of {} satisfies any predicate). So
        the unlabeled ticket still matches, and the labelled-with-X
        ticket is correctly excluded.

        ``self_heal`` is intentionally not in ``FSM_STAGE_ORDER``; it
        runs out-of-band against any open ticket and falls back to the
        coarse "open" filter via ``list_tickets(state="open")``.
        """
        from backend.app.services.linear_provisioner import previous_stage

        parts: list[dict[str, Any]] = []
        target_state_name = self._fsm_to_linear_state.get(stage)
        if not target_state_name or target_state_name not in self._state_id_by_name:
            # Stage not bound to a Linear workflow state — runs out-of-
            # band (``self_heal`` etc). The caller falls back to a
            # coarse ``state="open"`` filter for these; return an
            # empty parts list so we don't accidentally start filtering.
            return parts
        parts.append(
            {"state": {"id": {"eq": self._state_id_by_name[target_state_name]}}}
        )

        # "this role hasn't finished yet" — never re-pick. ``every``
        # matches the empty label collection too, so unlabeled
        # Linear-native tickets at the entry stage still come through.
        #
        # Fail-safe: when the workspace's ``label_id_by_stage`` map
        # doesn't carry an id for this stage (provisioner predates the
        # stage, FSM extended without re-provisioning, manually
        # patched integration row, …) the previous behavior silently
        # dropped this clause and the picker matched *every* ticket in
        # the target state. Observed in production as one routine
        # looping on the same ticket every 30 min: the post-finish
        # transition tries to add the same ``stage:<X>`` breadcrumb,
        # ``label_id_by_stage.get`` returns ``None``, the mutation
        # adds no label, the next picker tick sees the same ticket as
        # eligible — agent re-runs, tokens burn. Surface the gap by
        # returning a sentinel filter that matches nothing — the cron
        # tick noops cleanly, the operator sees the missing-provision
        # gap via the warning log instead of a token bill.
        own_label = self._label_id_by_stage.get(stage)
        if own_label:
            parts.append({"labels": {"every": {"id": {"neq": own_label}}}})
        else:
            logger.warning(
                "linear adapter: stage=%s has no label_id provisioned for "
                "team=%s; refusing to pick (operator should re-run the "
                "Linear FSM provisioner)",
                stage,
                self._team_id,
            )
            # ``some: {id: {eq: <impossible>}}`` matches nothing for any
            # ticket including unlabeled ones (vacuously false on
            # empty), so the picker returns zero rows for this stage on
            # this workspace until provisioning is repaired.
            parts.append(
                {"labels": {"some": {"id": {"eq": "00000000-0000-0000-0000-000000000000"}}}}
            )

        # "previous role is done" — for non-entry stages.
        prev = previous_stage(stage)
        if prev:
            prev_label = self._label_id_by_stage.get(prev)
            if prev_label:
                parts.append(
                    {"labels": {"some": {"id": {"eq": prev_label}}}}
                )

        # ``needs:clarification`` is a *human-facing* marker only — the
        # agent posted a question and the operator hasn't answered yet.
        # We intentionally do NOT gate the picker on it any more:
        #
        #   - The previous design ("filter out clarification tickets at
        #     the GraphQL level") required the operator to manually
        #     strip the label every time they replied, otherwise the
        #     ticket stayed silently dropped from the queue. In practice
        #     nobody remembered to do that, so answered tickets piled up
        #     in Todo with the label stuck on them.
        #   - The portable replacement (mirrors the elmundi pipeline)
        #     pushes the "did the operator actually answer?" decision
        #     into the agent's own reasoning. The role prompts now
        #     instruct every specialist to scan comment history, look
        #     for a non-agent comment / description edit after their
        #     last clarification question, and return ``outcome=noop``
        #     with ``reason=awaiting_clarification`` when nothing new
        #     has appeared. The picker therefore keeps the ticket
        #     eligible; the agent itself quiets the loop.
        #   - This pattern is tracker-agnostic — Jira / GitHub Issues /
        #     Notion all expose comments + ``updated_at`` over the
        #     adapter interface. No tracker-specific webhook, no
        #     polling job, no Ship-side state machine.
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

    async def transition(
        self,
        ticket: TicketRef,
        *,
        to_state: str,
        from_state: str | None = None,
    ) -> None:
        """Move ``ticket`` to a Ship FSM stage (or a Linear state name).

        FSM-aware path (``to_state`` matches a configured FSM stage):
          - moves the Linear workflow state per ``fsm_to_linear_state``;
          - adds the breadcrumb label ``stage:<from_state>`` when
            ``from_state`` is given (the role that just finished). The
            picker for the next stage requires that breadcrumb to be
            present — see ``_stage_filter_parts``'s "previous role is
            done" filter. When ``from_state`` is omitted (legacy
            callers / re-runs without the source role) we fall back to
            adding ``stage:<to_state>`` so existing tickets that walked
            partway through the chain still progress.

        Legacy path (``to_state`` is a literal Linear state name like
        ``Done`` / ``Canceled``): resolves the workflow state by name
        on the issue's team and moves it; no labels touched. Agents
        must NOT pass ``Done`` — only humans move tickets out of
        Review. Enforcement lives at the route layer.
        """
        if ticket.kind != "linear":
            raise ValueError(f"LinearTracker can't transition kind={ticket.kind}")

        # FSM-aware path.
        if to_state in self._fsm_to_linear_state:
            target_state_name = self._fsm_to_linear_state[to_state]
            target_state_id = self._state_id_by_name.get(target_state_name)
            if not target_state_id:
                raise ValueError(
                    f"Linear state {target_state_name!r} not provisioned for this team"
                )
            # Stage labels are an *accumulating* breadcrumb trail: each
            # ``stage:<X>`` is added when role X *finished* its work,
            # so the picker for the next stage can require ``some:
            # {id: eq <previous_label>}`` (see ``_stage_filter_parts``,
            # comment block ~line 415). Add the breadcrumb for the
            # role that just finished. If the caller didn't specify
            # ``from_state``, fall back to ``to_state`` for back-compat
            # — wrong semantically but recoverable, and the runtime
            # logs the case so we can clean it up.
            breadcrumb_stage = from_state or to_state
            breadcrumb_label_id = self._label_id_by_stage.get(breadcrumb_stage)
            if from_state is None:
                logger.warning(
                    "LinearTracker.transition called without from_state "
                    "(to_state=%s); falling back to adding stage:%s as the "
                    "breadcrumb. Picker semantics may misfire.",
                    to_state,
                    breadcrumb_stage,
                )
            # Backwards-cascade label cleanup (askslayer/PAC-11 2026-05-17).
            # When a later role bounces the ticket back (e.g. validation
            # finds defects → ``outcome=blocked, stage_next=
            # dev_implementation``), the picker for the earlier role
            # refuses to re-pick because ``stage:<earlier>`` is already
            # on the ticket from its prior success. The breadcrumb chain
            # is meant to grow forward; on a backwards cascade we have
            # to retract the stages we're walking back through.
            #
            # Detect by FSM index: if ``to_state`` sits earlier in
            # ``FSM_STAGE_ORDER`` than ``from_state``, strip labels for
            # every stage in ``[to_state .. from_state)`` so the
            # earlier-role picker matches again. ``from_state`` itself
            # keeps its label because it just finished — we add it as
            # the new breadcrumb below.
            from backend.app.services.linear_provisioner import (
                FSM_STAGE_ORDER,
            )
            removed_label_ids: list[str] = []
            if from_state and from_state in FSM_STAGE_ORDER and to_state in FSM_STAGE_ORDER:
                i_from = FSM_STAGE_ORDER.index(from_state)
                i_to = FSM_STAGE_ORDER.index(to_state)
                if i_to < i_from:
                    for stage in FSM_STAGE_ORDER[i_to:i_from]:
                        lid = self._label_id_by_stage.get(stage)
                        if lid:
                            removed_label_ids.append(lid)
            mutation_input: dict[str, Any] = {"stateId": target_state_id}
            if breadcrumb_label_id:
                mutation_input["addedLabelIds"] = [breadcrumb_label_id]
            if removed_label_ids:
                mutation_input["removedLabelIds"] = removed_label_ids
                logger.info(
                    "LinearTracker.transition backwards-cascade detected "
                    "from=%s to=%s; removing %d forward-stage label(s) "
                    "so the earlier role can re-pick",
                    from_state, to_state, len(removed_label_ids),
                )
            await self._gql(
                """mutation ShipFsmTransition($id: String!, $input: IssueUpdateInput!) {
                  issueUpdate(id: $id, input: $input) { success }
                }""",
                {"id": ticket.id, "input": mutation_input},
            )
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

    async def relabel_stages(
        self,
        ticket: TicketRef,
        *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
    ) -> dict[str, list[str]]:
        """Surgically add/remove FSM stage labels on a ticket.

        Operator escape hatch for fixing a stuck label state when the
        breadcrumb chain got corrupted by a buggy run (e.g. the old
        ``transition()`` that wrote the wrong stage label). Both args
        accept Ship FSM stage names (e.g. ``task_intake``,
        ``ba_requirements``); we resolve them to Linear label ids via
        ``_label_id_by_stage``.

        Pre-checks the issue's current label set so ``removedLabelIds``
        only carries labels that are actually present — Linear rejects
        the whole mutation with ``Label not on issue`` otherwise.
        Returns ``{"added": [...], "removed": [...]}`` of stage names
        that actually changed.
        """
        if ticket.kind != "linear":
            raise ValueError(
                f"LinearTracker can't relabel_stages for kind={ticket.kind}"
            )

        # Build a unified ``stage_or_signal → label_id`` map so the
        # endpoint accepts both FSM-stage labels (``task_intake``,
        # ``ba_requirements``, …) AND signal labels
        # (``needs_clarification`` and any other key registered on the
        # workspace's Linear OAuth row). Operator wants to clear
        # ``needs:clarification`` after answering an agent's question
        # so the picker stops skipping the ticket — that key isn't a
        # stage but it's exactly the same surgical-add/remove shape.
        known_label_ids: dict[str, str] = dict(self._label_id_by_stage)
        for key, lid in self._signal_label_ids.items():
            # Don't let a signal key collide with a stage name — keep
            # stage entries first; signal entries fill the rest.
            known_label_ids.setdefault(key, lid)

        wanted_add_ids: dict[str, str] = {}
        for stage in (add or []):
            lid = known_label_ids.get(stage)
            if not lid:
                raise ValueError(f"unknown stage/signal label {stage!r}")
            wanted_add_ids[stage] = lid
        wanted_remove_ids: dict[str, str] = {}
        for stage in (remove or []):
            lid = known_label_ids.get(stage)
            if not lid:
                raise ValueError(f"unknown stage/signal label {stage!r}")
            wanted_remove_ids[stage] = lid

        # Read current labels so we can prune ``remove`` to only what's
        # actually on the issue.
        snapshot = await self._gql(
            """query ShipReadLabels($id: String!) {
              issue(id: $id) { labels { nodes { id } } }
            }""",
            {"id": ticket.id},
        )
        present_label_ids = {
            node.get("id")
            for node in (
                ((snapshot.get("issue") or {}).get("labels") or {}).get("nodes")
                or []
            )
            if isinstance(node, dict) and node.get("id")
        }

        actual_remove_ids = [
            lid for stage, lid in wanted_remove_ids.items() if lid in present_label_ids
        ]
        actual_add_ids = [
            lid for stage, lid in wanted_add_ids.items() if lid not in present_label_ids
        ]

        mutation_input: dict[str, Any] = {}
        if actual_remove_ids:
            mutation_input["removedLabelIds"] = actual_remove_ids
        if actual_add_ids:
            mutation_input["addedLabelIds"] = actual_add_ids
        if mutation_input:
            await self._gql(
                """mutation ShipRelabel($id: String!, $input: IssueUpdateInput!) {
                  issueUpdate(id: $id, input: $input) { success }
                }""",
                {"id": ticket.id, "input": mutation_input},
            )

        added_stages = [
            stage for stage, lid in wanted_add_ids.items() if lid in actual_add_ids
        ]
        removed_stages = [
            stage for stage, lid in wanted_remove_ids.items() if lid in actual_remove_ids
        ]
        return {"added": added_stages, "removed": removed_stages}

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

    async def update_ticket(
        self,
        ticket: TicketRef,
        *,
        title: str | None = None,
        body: str | None = None,
        labels: list[str] | None = None,
        project_id: str | None = None,
    ) -> None:
        """Update title / body / labels / project in one ``issueUpdate`` call.

        ``None`` arguments leave the field as-is — pass ``""`` to clear
        a string field, an empty list to clear all labels.

        ``labels`` is a **full replacement set** — Linear's
        ``issueUpdate`` with ``labelIds`` replaces the entire label
        list, so partial add/remove must happen at the caller layer.
        Unknown labels are silently dropped (same strict policy as
        ``create_ticket`` — we don't want the LLM polluting a
        customer's Linear with freshly invented label names).

        ``project_id`` (Linear project UUID) attaches the ticket to a
        project — used by the orphan-tickets admin sweep to re-home
        old standalone tickets that pre-date the
        project-must-be-set picker rule (ELS-83).

        Used by Navigator's ``update_ticket`` tool. State transitions
        are NOT in scope here — call :meth:`transition` separately.
        """
        if ticket.kind != "linear":
            raise ValueError(
                f"LinearTracker can't update_ticket for kind={ticket.kind}"
            )
        input_payload: dict[str, Any] = {}
        if title is not None:
            input_payload["title"] = title
        if body is not None:
            input_payload["description"] = body
        if labels is not None:
            team_id = await self._resolve_team_id(None)
            input_payload["labelIds"] = await self._resolve_label_ids(
                team_id, labels
            )
        if project_id is not None:
            input_payload["projectId"] = project_id
        if not input_payload:
            return
        await self._gql(
            """mutation ShipUpdateIssue($id: String!, $input: IssueUpdateInput!) {
              issueUpdate(id: $id, input: $input) { success }
            }""",
            {"id": ticket.id, "input": input_payload},
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
        priority: int | None = None,
        ticket_type: Literal["bug", "feature", "task"] | None = None,
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

        ``ticket_type`` (``bug`` / ``feature`` / ``task``) classifies
        the work. When the team exposes Linear's native issue-types
        feature and has a type whose name matches case-insensitively,
        we set ``issueTypeId``. Otherwise the value is rendered as a
        ``type:<value>`` label alongside caller-supplied labels (de-
        duped) so downstream dashboards filter on one label key
        across all four trackers.
        """
        team_id = await self._resolve_team_id(project_hint)

        issue_type_id: str | None = None
        effective_labels: list[str] = list(labels or [])
        if ticket_type is not None:
            issue_type_id = await self._resolve_issue_type_id(
                team_id, ticket_type
            )
            if issue_type_id is None:
                # Fall back to a portable ``type:<value>`` label so the
                # classification still surfaces. De-dup against any
                # caller-supplied entry so we don't double-tag.
                fallback_label = f"type:{ticket_type}"
                if not any(
                    existing.lower() == fallback_label
                    for existing in effective_labels
                ):
                    effective_labels.append(fallback_label)

        label_ids = (
            await self._resolve_label_ids(team_id, effective_labels)
            if effective_labels
            else []
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
        if issue_type_id is not None:
            input_payload["issueTypeId"] = issue_type_id
        if priority is not None:
            # Linear's priority is 0..4 (0=No priority, 1=Urgent,
            # 2=High, 3=Medium, 4=Low). Clamp defensively — the
            # security-officer routine maps Snyk severity onto this
            # range and a bad mapping shouldn't crash issueCreate.
            input_payload["priority"] = max(0, min(4, int(priority)))

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

    async def upsert_project_section(
        self, project_id: str, *, section: str, body: str
    ) -> None:
        """Replace-or-append a ``## <section>`` block in the project body.

        Section ownership pins one chunk of the body to one specialist
        (BA owns ``## WBS``, Tech-architect owns ``## Architecture``,
        etc., per the decomposition process). Re-running a stage
        replaces just its section; sections owned by other stages
        stay verbatim.

        Match is case-sensitive on the literal ``## <section>`` line
        — fuzzy matching would let a typo'd heading silently double a
        section, which is harder to debug than a clean append. New
        sections land at the bottom of the body separated by a blank
        line, preserving the chain order naturally (WBS first because
        BA runs first, etc.).
        """
        existing = (await self.get_project(project_id)).get("content") or ""
        heading = f"## {section}"
        block = f"{heading}\n\n{body.strip()}\n"

        if heading not in existing:
            new_content = (
                existing.rstrip() + "\n\n" + block if existing else block
            )
        else:
            # Find the heading line, then walk forward until the next
            # ``## `` heading (or EOF). Replace that range with the new
            # block. Splitting on the heading-prefix is cheaper than a
            # full regex parse and gives us deterministic boundaries.
            lines = existing.splitlines(keepends=False)
            new_lines: list[str] = []
            i = 0
            replaced = False
            while i < len(lines):
                if not replaced and lines[i].rstrip() == heading:
                    new_lines.extend(block.splitlines())
                    i += 1
                    while i < len(lines) and not (
                        lines[i].startswith("## ") and not lines[i].startswith("### ")
                    ):
                        i += 1
                    replaced = True
                    continue
                new_lines.append(lines[i])
                i += 1
            new_content = "\n".join(new_lines).rstrip() + "\n"

        mutation = """
        mutation ShipUpdateProject($id: String!, $content: String!) {
          projectUpdate(id: $id, input: { content: $content }) { success }
        }
        """
        await self._gql(
            mutation, {"id": project_id, "content": new_content}
        )

    # -----------------------------------------------------------------
    # Decomposition anchor (project-first delivery)
    #
    # Each project the PO drafts gets exactly one anchor issue tagged
    # ``planning:anchor``. The decomposition FSM later runs against
    # this issue (Linear projects don't have their own state machine,
    # so we hang one inside). Co-locating the anchor with the project
    # keeps the tracker UI honest: the issue tab on the project page
    # shows the anchor first, body sections below.
    # -----------------------------------------------------------------

    PLANNING_ANCHOR_LABEL = "planning:anchor"

    async def create_planning_anchor(
        self,
        project_id: str,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create the anchor issue for ``project_id``'s decomposition.

        Mints the ``planning:anchor`` label on the team if it doesn't
        exist yet — silently dropping the tag would orphan the
        decomposition FSM's filter. Any caller-supplied ``labels`` are
        ALSO minted-if-missing for the same reason.

        Returns ``{"id", "identifier", "url"}``.
        """
        team_id = await self._resolve_team_id(None)
        all_labels = [self.PLANNING_ANCHOR_LABEL]
        for lbl in labels or []:
            if lbl and lbl != self.PLANNING_ANCHOR_LABEL:
                all_labels.append(lbl)
        label_ids = await self._resolve_or_create_label_ids(
            team_id, all_labels
        )
        mutation = """
        mutation ShipCreateAnchor($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue { id identifier url }
          }
        }
        """
        payload: dict[str, Any] = {
            "teamId": team_id,
            "projectId": project_id,
            "title": title,
            "description": body,
        }
        if label_ids:
            payload["labelIds"] = label_ids
        data = await self._gql(mutation, {"input": payload})
        result = data.get("issueCreate") or {}
        issue = result.get("issue") or {}
        if not result.get("success") or not issue:
            raise ValueError(
                "Linear refused issueCreate for the planning anchor."
            )
        return {
            "id": str(issue.get("id") or ""),
            "identifier": str(issue.get("identifier") or issue.get("id") or ""),
            "url": str(issue.get("url") or ""),
        }

    async def get_planning_anchor(
        self, project_id: str
    ) -> dict[str, Any] | None:
        """Fetch the existing planning anchor for ``project_id``, if any.

        Used by ``_tool_create_project`` to make the anchor-creation
        step idempotent — a re-run of ``create_project`` against an
        existing project must not spawn a second anchor.
        """
        query = """
        query ShipFindAnchor($projectId: ID!, $label: String!) {
          issues(
            filter: {
              project: { id: { eq: $projectId } },
              labels: { name: { eq: $label } }
            },
            first: 1,
            orderBy: createdAt
          ) {
            nodes { id identifier url state { name } }
          }
        }
        """
        data = await self._gql(
            query,
            {
                "projectId": project_id,
                "label": self.PLANNING_ANCHOR_LABEL,
            },
        )
        nodes = ((data.get("issues") or {}).get("nodes")) or []
        if not nodes:
            return None
        node = nodes[0]
        return {
            "id": str(node.get("id") or ""),
            "identifier": str(node.get("identifier") or node.get("id") or ""),
            "url": str(node.get("url") or ""),
            "state": str(((node.get("state") or {}).get("name")) or ""),
        }

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

    async def list_project_tickets(
        self,
        *,
        project_id: str,
        open_only: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List tickets attached to ``project_id`` for dedup loops.

        The reviewer routines (tech-reviewer / qa-reviewer /
        security-officer) call this once per pass to figure out which
        findings already have an open ticket so they don't re-file the
        same finding every cron cycle. ``open_only=True`` excludes
        ``completed`` / ``canceled`` so the dedup window is "what's
        still pending" rather than the whole project history.

        Returns a list of ``{"id", "identifier", "title", "url",
        "state", "labels"}``. ``identifier`` is the human ref
        (``ELS-99``) the agent uses in its dedup key.
        """
        if not project_id:
            return []
        first = max(1, min(limit, 250))
        parts: list[dict[str, Any]] = [
            {"project": {"id": {"eq": project_id}}},
        ]
        if open_only:
            parts.append(
                {"state": {"type": {"nin": ["completed", "canceled"]}}}
            )
        if self._team_id:
            parts.append({"team": {"id": {"eq": self._team_id}}})
        issue_filter: dict[str, Any] = {"and": parts}
        query = """
        query ShipListProjectTickets($first: Int!, $filter: IssueFilter!) {
          issues(first: $first, orderBy: updatedAt, filter: $filter) {
            nodes {
              id
              identifier
              title
              url
              state { name type }
              labels { nodes { name } }
            }
          }
        }
        """
        data = await self._gql(query, {"first": first, "filter": issue_filter})
        nodes = (data.get("issues") or {}).get("nodes") or []
        return [
            {
                "id": node.get("id"),
                "identifier": node.get("identifier") or node.get("id"),
                "title": node.get("title") or "",
                "url": node.get("url"),
                "state": (node.get("state") or {}).get("name"),
                "labels": [
                    lbl.get("name")
                    for lbl in (node.get("labels") or {}).get("nodes") or []
                    if lbl.get("name")
                ],
            }
            for node in nodes
        ]

    async def list_orphan_tickets(
        self, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List open Linear issues that aren't attached to any project.

        Used by the orphan-ticket admin sweep — the audit log only
        captures refs the picker has actively tried (limited by which
        stages the cron has rotated through), so a comprehensive
        cleanup needs the full set straight from the source. Filters
        to the configured team when ``self._team_id`` is set so we
        don't surface other teams' orphans an admin can't act on.

        Linear's ``project: {null: true}`` is the canonical "no
        project" filter on ``IssueFilter`` (NullableProjectFilter
        pattern). Coupled with ``state.type.nin`` we drop already-
        completed / cancelled rows so the operator's cleanup view
        only carries actionable orphans.
        """
        first = max(1, min(limit, 250))
        parts: list[dict[str, Any]] = [
            {"state": {"type": {"nin": ["completed", "canceled"]}}},
            {"project": {"null": True}},
        ]
        if self._team_id:
            parts.append({"team": {"id": {"eq": self._team_id}}})
        issue_filter: dict[str, Any] = {"and": parts}

        query = """
        query ShipListOrphans($first: Int!, $filter: IssueFilter!) {
          issues(first: $first, orderBy: updatedAt, filter: $filter) {
            nodes {
              id
              identifier
              title
              description
              url
              state { name type }
              labels { nodes { name } }
              updatedAt
            }
          }
        }
        """
        try:
            data = await self._gql(query, {"first": first, "filter": issue_filter})
        except RuntimeError:
            # Older Linear schemas without ``NullableProjectFilter``
            # would reject ``{null: true}``. Fall back to listing the
            # team's open issues + filtering client-side. Conservative
            # — costs us a wider read but never misses orphans.
            return await self._list_orphan_tickets_fallback(limit=first)
        nodes = (data.get("issues") or {}).get("nodes") or []
        return [
            {
                "ticket_ref": node.get("identifier") or node.get("id"),
                "title": node.get("title") or "",
                "description": node.get("description"),
                "url": node.get("url"),
                "state": (node.get("state") or {}).get("name"),
                "labels": [
                    lbl.get("name")
                    for lbl in (node.get("labels") or {}).get("nodes") or []
                    if lbl.get("name")
                ],
                "project_id": None,  # by construction
            }
            for node in nodes
        ]

    async def _list_orphan_tickets_fallback(
        self, *, limit: int
    ) -> list[dict[str, Any]]:
        """Fallback path used when Linear rejects ``project: {null: true}``.

        Pulls the team's open issues with ``project { id }`` projected
        and filters client-side. Costs one extra round-trip for every
        ticket the picker would have skipped on the server, but the
        operator only runs this sweep manually so the wider read is
        acceptable.
        """
        parts: list[dict[str, Any]] = [
            {"state": {"type": {"nin": ["completed", "canceled"]}}}
        ]
        if self._team_id:
            parts.append({"team": {"id": {"eq": self._team_id}}})
        issue_filter: dict[str, Any] = {"and": parts}
        query = """
        query ShipListOpen($first: Int!, $filter: IssueFilter!) {
          issues(first: $first, orderBy: updatedAt, filter: $filter) {
            nodes {
              id
              identifier
              title
              description
              url
              state { name type }
              labels { nodes { name } }
              project { id }
            }
          }
        }
        """
        data = await self._gql(query, {"first": limit, "filter": issue_filter})
        nodes = (data.get("issues") or {}).get("nodes") or []
        out: list[dict[str, Any]] = []
        for node in nodes:
            project = node.get("project") or {}
            if project and project.get("id"):
                continue
            out.append(
                {
                    "ticket_ref": node.get("identifier") or node.get("id"),
                    "title": node.get("title") or "",
                    "description": node.get("description"),
                    "url": node.get("url"),
                    "state": (node.get("state") or {}).get("name"),
                    "labels": [
                        lbl.get("name")
                        for lbl in (node.get("labels") or {}).get("nodes") or []
                        if lbl.get("name")
                    ],
                    "project_id": None,
                }
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
            project { id }
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
            # Project containment — read by the decomposition completion
            # hook to find the priorities row keyed on the Linear
            # project's UUID. ``None`` for issues that aren't inside a
            # project (regular standalone tickets).
            "project_id": ((issue.get("project") or {}).get("id")) or None,
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

    async def _resolve_issue_type_id(
        self,
        team_id: str,
        ticket_type: Literal["bug", "feature", "task"],
    ) -> str | None:
        """Return the team-native ``IssueType.id`` matching ``ticket_type``.

        Linear's ``team.issueTypes`` is feature-gated per plan tier:
        tokens whose workspace doesn't expose the field at all raise
        a GraphQL schema-level error rather than returning an empty
        list. We treat *any* failure (schema error, transport error,
        empty result, name mismatch) as "no native type available" —
        the caller then renders a ``type:<value>`` label as a portable
        fallback so the classification still lands.

        Cache hits on ``team_id`` (not ``team_id+ticket_type``) so two
        sequential ``create_ticket`` calls on the same team share one
        probe regardless of which value they pass. ``None`` is the
        "probe failed" sentinel — distinct from "probe returned empty
        list" because both behave the same downstream but we want to
        debug the difference if needed.
        """
        cached = self._issue_types_by_team.get(team_id)
        if cached is None and team_id in self._issue_types_by_team:
            # Probe already ran and failed; don't retry within this
            # adapter instance — the schema-gating is workspace-wide,
            # not per-call.
            return None
        if cached is None:
            cached = await self._probe_team_issue_types(team_id)
            self._issue_types_by_team[team_id] = cached
        if not cached:
            return None
        return cached.get(ticket_type.lower())

    async def _probe_team_issue_types(
        self, team_id: str
    ) -> dict[str, str] | None:
        """Fetch ``team.issueTypes`` once; ``None`` on schema error."""
        query = """
        query ShipIssueTypes($teamId: String!) {
          team(id: $teamId) { issueTypes { nodes { id name } } }
        }
        """
        try:
            data = await self._gql(query, {"teamId": team_id})
        except Exception:  # noqa: BLE001 — schema-error / transport / 4xx
            # Any failure means we can't trust the native field;
            # caller falls through to the ``type:<value>`` label path.
            logger.info(
                "linear issueTypes probe failed for team_id=%s; falling "
                "back to type:<value> label",
                team_id,
            )
            return None
        nodes = (
            ((data.get("team") or {}).get("issueTypes") or {}).get("nodes")
            or []
        )
        mapping: dict[str, str] = {}
        for node in nodes:
            name = str(node.get("name") or "").strip().lower()
            node_id = node.get("id")
            if name and node_id:
                mapping[name] = str(node_id)
        return mapping

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

    async def _resolve_or_create_label_ids(
        self, team_id: str, labels: list[str]
    ) -> list[str]:
        """Like :meth:`_resolve_label_ids` but mints any missing labels.

        Used by infrastructure-level callers (anchor creation) where
        dropping a label silently would orphan a downstream filter (the
        decomposition FSM polls ``label = 'planning:anchor'``). The
        agent-facing ``create_ticket`` keeps the strict resolver — we
        don't want the LLM polluting a customer's Linear with
        freshly-invented label names.
        """
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
        existing = {
            str(n.get("name", "")).lower(): str(n["id"])
            for n in nodes
            if n.get("id")
        }
        out: list[str] = []
        mint = """
        mutation ShipMintLabel($input: IssueLabelCreateInput!) {
          issueLabelCreate(input: $input) {
            success
            issueLabel { id name }
          }
        }
        """
        for lbl in labels:
            key = lbl.lower()
            if key in existing:
                out.append(existing[key])
                continue
            created = await self._gql(
                mint, {"input": {"name": lbl, "teamId": team_id}}
            )
            new = (
                (created.get("issueLabelCreate") or {}).get("issueLabel") or {}
            )
            if not new.get("id"):
                raise RuntimeError(
                    f"Linear refused issueLabelCreate for {lbl!r}"
                )
            out.append(str(new["id"]))
        return out


def _parse_iso8601(raw: str) -> datetime:
    """Parse Linear's ``createdAt`` (ISO-8601 with ``Z`` suffix).

    ``datetime.fromisoformat`` only accepts ``+00:00`` before 3.11;
    we're on 3.12 but Linear still ships the ``Z``. Normalise it
    before handing off to keep the adapter portable.
    """
    cleaned = raw.replace("Z", "+00:00") if raw.endswith("Z") else raw
    return datetime.fromisoformat(cleaned)


__all__ = ["LinearTracker", "LINEAR_GRAPHQL_URL"]
