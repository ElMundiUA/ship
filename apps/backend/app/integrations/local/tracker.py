"""Memory tracker — TrackerGateway over Ship's own Postgres.

Backs the laptop-offline profile. Stores tickets / projects /
comments in ``memory_tracker_*`` tables (workspace-scoped) and
speaks the same :class:`TrackerGateway` protocol as
``LinearTracker`` / ``JiraTracker`` / ``GitHubIssuesTracker``, so
the orchestrator can't tell which is wired underneath.

Design notes:

- Workspace-scoped — every read/write filters by ``workspace_id``
  so a single Postgres instance can host many laptop workspaces
  without bleeding state between them.
- FSM mapping — ``list_tickets(state="task_intake")`` returns
  tickets whose row has ``state IN ('Todo', 'Backlog')`` AND
  carries label ``stage:task_intake``. This mirrors how the
  Linear adapter resolves Ship's FSM stages onto Linear workflow
  states + ``stage:*`` labels.
- Display ids: ``MEM-1``, ``MEM-2``, … minted per workspace via
  ``MAX(serial) + 1`` at insert. Cheap and predictable for
  fixtures.
- Comments + tickets + projects all live in the same DB the
  backend already reads, so a request handler can call into the
  adapter using its own ``AsyncSession`` without spinning up a
  second pool.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.memory_adapters import (
    MemoryTrackerComment,
    MemoryTrackerProject,
    MemoryTrackerTicket,
)
from backend.app.integrations.gateway.tracker import (
    CommentRef,
    CreatedTicket,
    ListedIssue,
    TicketRef,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

# States considered "still actionable" — what the picker means by
# ``state="open"``. Mirrors Linear's non-terminal type bucket.
_OPEN_STATES: tuple[str, ...] = ("Backlog", "Todo", "In Progress", "In Review", "Blocked")
_CLOSED_STATES: tuple[str, ...] = ("Done", "Cancelled")


# ---------------------------------------------------------------------------
# MemoryTracker
# ---------------------------------------------------------------------------


class MemoryTracker:
    """In-Postgres TrackerGateway. Constructed per-request with a session."""

    # Discriminator surfaced into ``TicketRef.kind``. We deliberately
    # reuse ``"linear"`` so downstream code-paths that switch on the
    # kind don't need a third arm — the memory adapter is a behavioural
    # replacement for Linear during laptop dev, not a new tracker
    # type the agent has to learn. Callers that care about the actual
    # backend can read ``settings.use_memory_adapters``.
    _REF_KIND: Literal["linear"] = "linear"

    def __init__(
        self,
        *,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        console_origin: str = "http://localhost:3001",
    ) -> None:
        self._session = session
        self._workspace_id = workspace_id
        self._origin = console_origin.rstrip("/")

    # ------------------------------------------------------------------
    # Tickets
    # ------------------------------------------------------------------

    async def list_tickets(
        self,
        *,
        limit: int = 10,
        state: str | None = None,
        assignee_me: bool = False,
        query: str | None = None,
        assignee: str | None = None,
    ) -> list[dict[str, Any]]:
        del assignee_me  # no per-user assignment in the offline profile
        stmt = select(MemoryTrackerTicket).where(
            MemoryTrackerTicket.workspace_id == self._workspace_id
        )

        raw_state = (state or "all").strip().lower()
        if raw_state == "open":
            stmt = stmt.where(MemoryTrackerTicket.state.in_(_OPEN_STATES))
        elif raw_state in {"closed", "done", "completed"}:
            stmt = stmt.where(MemoryTrackerTicket.state.in_(_CLOSED_STATES))
        elif raw_state in {"all", ""}:
            pass  # no state filter
        else:
            # FSM-stage name — look for ``stage:<name>`` label, only on
            # open tickets (the picker should never resurrect a Done
            # ticket).
            label = f"stage:{raw_state}"
            stmt = stmt.where(
                MemoryTrackerTicket.state.in_(_OPEN_STATES),
                MemoryTrackerTicket.labels.contains([label]),
            )

        if query and query.strip():
            stmt = stmt.where(
                MemoryTrackerTicket.title.ilike(f"%{query.strip()}%")
            )

        if assignee and assignee.strip():
            stmt = stmt.where(
                MemoryTrackerTicket.assignee_email == assignee.strip()
            )

        stmt = stmt.order_by(MemoryTrackerTicket.serial.asc()).limit(
            max(1, min(limit, 50))
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._ticket_row_to_dict(r) for r in rows]

    async def transition(
        self,
        ticket: TicketRef,
        *,
        to_state: str,
        from_state: str | None = None,
    ) -> None:
        # ``from_state`` is the dispatcher's optimistic check — Linear's
        # GraphQL transition mutation accepts it as a guard. Memory mode
        # always succeeds and just swaps the label, so we accept the
        # kwarg and ignore it.
        del from_state
        row = await self._fetch_ticket_for_ref(ticket)
        if row is None:
            return  # mirror Linear behaviour: silently no-op on missing
        # FSM-stage transitions update the stage label and (optionally)
        # the row's state. "to_state" follows the picker's vocabulary:
        # an explicit display-state ("Todo" / "In Progress" / "Done") or
        # an FSM-stage label ("ba_requirements", …). We accept both.
        if to_state in _OPEN_STATES or to_state in _CLOSED_STATES:
            row.state = to_state
        else:
            # FSM stage — swap the existing ``stage:*`` label for the new
            # one, leave the display state alone (the picker filters by
            # state ∈ open AND stage label, so dropping the old label
            # is what removes the ticket from the prior stage's queue).
            labels = list(row.labels or [])
            labels = [l for l in labels if not l.startswith("stage:")]
            labels.append(f"stage:{to_state}")
            row.labels = labels
        row.updated_at = _utcnow()

    async def add_signal_label(self, ticket: TicketRef, *, key: str) -> None:
        """Memory parity for ``LinearTracker.add_signal_label``.

        The finish handler's ``needs_clarification`` path calls this
        to tag the ticket with ``needs:clarification`` so the picker
        stops re-claiming it until the operator answers. Without the
        method, finish returns 501 ``needs_clarification_unsupported``
        for any memory-mode workspace.

        ``key`` matches Linear's ``SIGNAL_LABELS`` keys — currently
        only ``needs_clarification``; the label name is
        ``needs:<key>``.
        """
        row = await self._fetch_ticket_for_ref(ticket)
        if row is None:
            return
        label = f"needs:{key.replace('_', '-')}"
        labels = list(row.labels or [])
        if label not in labels:
            labels.append(label)
            row.labels = labels
            row.updated_at = _utcnow()

    async def list_project_tickets(
        self,
        *,
        project_id: str,
        open_only: bool = True,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Memory parity for ``LinearTracker.list_project_tickets``.

        Reviewer routines + the e2e pipeline read this to inspect
        children of a project. ``identifier`` keeps the Linear-shape
        contract so callers can use the same dedup key
        (``display_id``).
        """
        proj_uuid = _safe_uuid(project_id)
        if proj_uuid is None:
            return []
        stmt = select(MemoryTrackerTicket).where(
            MemoryTrackerTicket.workspace_id == self._workspace_id,
            MemoryTrackerTicket.project_id == proj_uuid,
        )
        if open_only:
            stmt = stmt.where(MemoryTrackerTicket.state.in_(_OPEN_STATES))
        stmt = stmt.order_by(MemoryTrackerTicket.serial.asc()).limit(
            max(1, min(limit, 250))
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            {
                "id": row.display_id,
                "identifier": row.display_id,
                "title": row.title,
                "url": self._ticket_url(row.display_id),
                "state": row.state,
                "labels": list(row.labels or []),
            }
            for row in rows
        ]

    async def get_ticket_snapshot(
        self, ticket: TicketRef
    ) -> dict[str, Any] | None:
        """Cheap snapshot mirroring ``LinearTracker.get_ticket_snapshot``.

        Read by the decomposition completion hook to walk
        ``ticket_ref → project_id → priorities row`` and flip it to
        ``parked``. Without this the hook bails on
        ``snapshot_fn is None`` and the project never leaves Drafts.
        """
        row = await self._fetch_ticket_for_ref(ticket)
        if row is None:
            return None
        return {
            "ticket_ref": row.display_id,
            "title": row.title,
            "description": row.body,
            "url": self._ticket_url(row.display_id),
            "state": row.state,
            "labels": list(row.labels or []),
            "project_id": str(row.project_id) if row.project_id else None,
        }

    async def set_description(self, ticket: TicketRef, *, body: str) -> None:
        """Replace the ticket body. Used by the finish handler to splice
        the planner / BA / intake markdown into the issue description
        (Linear-shape adapters update the GraphQL ``description`` field;
        memory mode just overwrites the row's ``body``)."""
        row = await self._fetch_ticket_for_ref(ticket)
        if row is None:
            return
        row.body = body
        row.updated_at = _utcnow()

    async def comment(self, ticket: TicketRef, *, body: str) -> None:
        row = await self._fetch_ticket_for_ref(ticket)
        if row is None:
            return
        self._session.add(
            MemoryTrackerComment(
                ticket_id=row.id,
                body=body,
                author="ship",
            )
        )
        row.updated_at = _utcnow()

    async def create_ticket(
        self,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
        project_hint: str | None = None,
        project_id: str | None = None,
        ticket_type: Literal["bug", "feature", "task"] | None = None,
        priority: int | None = None,
    ) -> CreatedTicket:
        # Linear-shaped adapters accept ``priority`` (0-4). The memory
        # adapter has no priority column today; drop the value rather
        # than 422 the caller so the route's ``priority=payload.priority``
        # default-None pass-through doesn't break memory-mode workspaces.
        del project_hint, priority
        merged_labels = list(labels or [])
        if ticket_type and not any(
            l.startswith("type:") for l in merged_labels
        ):
            merged_labels.append(f"type:{ticket_type}")

        serial = await self._next_serial()
        display_id = f"MEM-{serial}"

        project_uuid: uuid.UUID | None = None
        if project_id:
            try:
                project_uuid = uuid.UUID(project_id)
            except ValueError:
                project_uuid = None

        row = MemoryTrackerTicket(
            workspace_id=self._workspace_id,
            project_id=project_uuid,
            serial=serial,
            display_id=display_id,
            title=title,
            body=body,
            ticket_type=ticket_type,
            labels=merged_labels,
        )
        self._session.add(row)
        await self._session.flush()

        url = self._ticket_url(display_id)
        return CreatedTicket(
            ref=TicketRef(
                kind=self._REF_KIND,
                workspace_hint=str(self._workspace_id),
                id=display_id,
            ),
            url=url,
            display_id=display_id,
        )

    # ------------------------------------------------------------------
    # Projects (epics)
    # ------------------------------------------------------------------

    async def list_projects(
        self,
        *,
        limit: int = 50,
        state: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        stmt = select(MemoryTrackerProject).where(
            MemoryTrackerProject.workspace_id == self._workspace_id
        )
        if state:
            stmt = stmt.where(MemoryTrackerProject.state == state)
        if query and query.strip():
            stmt = stmt.where(
                MemoryTrackerProject.name.ilike(f"%{query.strip()}%")
            )
        stmt = stmt.order_by(
            MemoryTrackerProject.updated_at.desc()
        ).limit(max(1, min(limit, 100)))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._project_row_to_dict(r) for r in rows]

    async def get_project(
        self, project_id: str, *, issues_limit: int = 25
    ) -> dict[str, Any]:
        proj_uuid = _safe_uuid(project_id)
        if proj_uuid is None:
            raise ValueError(f"invalid project id: {project_id!r}")
        proj = (
            await self._session.execute(
                select(MemoryTrackerProject).where(
                    MemoryTrackerProject.id == proj_uuid,
                    MemoryTrackerProject.workspace_id == self._workspace_id,
                )
            )
        ).scalar_one_or_none()
        if proj is None:
            raise ValueError(f"project not found: {project_id}")

        issues = (
            (
                await self._session.execute(
                    select(MemoryTrackerTicket)
                    .where(
                        MemoryTrackerTicket.project_id == proj_uuid,
                        MemoryTrackerTicket.workspace_id == self._workspace_id,
                    )
                    .order_by(MemoryTrackerTicket.serial.asc())
                    .limit(max(1, min(issues_limit, 100)))
                )
            )
            .scalars()
            .all()
        )

        return {
            **self._project_row_to_dict(proj),
            "description": proj.description,
            "content": proj.body,
            "issues": [
                {
                    "id": r.display_id,
                    "display_id": r.display_id,
                    "title": r.title,
                    "url": self._ticket_url(r.display_id),
                    "state": r.state,
                }
                for r in issues
            ],
        }

    async def create_project(
        self,
        *,
        name: str,
        body: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        slug = _slugify(name) or f"proj-{uuid.uuid4().hex[:8]}"
        # Ensure uniqueness within workspace by suffixing if needed.
        slug = await self._unique_project_slug(slug)
        row = MemoryTrackerProject(
            workspace_id=self._workspace_id,
            slug=slug,
            name=name,
            description=description,
            body=body,
        )
        self._session.add(row)
        await self._session.flush()
        return {
            "id": str(row.id),
            "url": self._project_url(row.slug),
            "name": row.name,
            "slug": row.slug,
        }

    async def append_project_description(
        self, project_id: str, *, body: str
    ) -> None:
        proj = await self._fetch_project(project_id)
        if proj is None:
            return
        proj.body = (proj.body or "").rstrip() + "\n\n" + body.strip() + "\n"
        proj.updated_at = _utcnow()

    async def upsert_project_section(
        self, project_id: str, *, section: str, body: str
    ) -> None:
        proj = await self._fetch_project(project_id)
        if proj is None:
            return
        proj.body = _replace_or_append_section(
            proj.body or "", section=section, section_body=body
        )
        proj.updated_at = _utcnow()

    async def create_planning_anchor(
        self,
        project_id: str,
        *,
        title: str,
        body: str,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        proj = await self._fetch_project(project_id)
        if proj is None:
            raise ValueError(f"project not found: {project_id}")
        existing = await self.get_planning_anchor(project_id)
        if existing is not None:
            return existing
        ticket = await self.create_ticket(
            title=title,
            body=body,
            labels=["planning:anchor", *(labels or [])],
            project_id=str(proj.id),
        )
        return {
            "id": ticket.ref.id,
            "identifier": ticket.display_id,
            "url": ticket.url,
        }

    async def get_planning_anchor(
        self, project_id: str
    ) -> dict[str, Any] | None:
        proj_uuid = _safe_uuid(project_id)
        if proj_uuid is None:
            return None
        row = (
            await self._session.execute(
                select(MemoryTrackerTicket).where(
                    MemoryTrackerTicket.workspace_id == self._workspace_id,
                    MemoryTrackerTicket.project_id == proj_uuid,
                    MemoryTrackerTicket.labels.contains(["planning:anchor"]),
                )
                .order_by(MemoryTrackerTicket.serial.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "id": row.display_id,
            "identifier": row.display_id,
            "url": self._ticket_url(row.display_id),
            "state": row.state,
        }

    # ------------------------------------------------------------------
    # Clarifications projection
    # ------------------------------------------------------------------

    async def list_issues_with_label(
        self, label: str, *, limit: int = 100
    ) -> list[ListedIssue]:
        rows = (
            (
                await self._session.execute(
                    select(MemoryTrackerTicket).where(
                        MemoryTrackerTicket.workspace_id == self._workspace_id,
                        MemoryTrackerTicket.state.in_(_OPEN_STATES),
                        MemoryTrackerTicket.labels.contains([label]),
                    )
                    .order_by(MemoryTrackerTicket.updated_at.desc())
                    .limit(max(1, min(limit, 200)))
                )
            )
            .scalars()
            .all()
        )
        return [
            ListedIssue(
                ref=TicketRef(
                    kind=self._REF_KIND,
                    workspace_hint=str(self._workspace_id),
                    id=r.display_id,
                ),
                display_id=r.display_id,
                url=self._ticket_url(r.display_id),
            )
            for r in rows
        ]

    async def list_comments(self, ticket: TicketRef) -> list[CommentRef]:
        row = await self._fetch_ticket_for_ref(ticket)
        if row is None:
            return []
        comments = (
            (
                await self._session.execute(
                    select(MemoryTrackerComment)
                    .where(MemoryTrackerComment.ticket_id == row.id)
                    .order_by(MemoryTrackerComment.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return [
            CommentRef(
                id=str(c.id),
                body=c.body,
                author=c.author,
                created_at=c.created_at,
                url=self._ticket_url(row.display_id),
            )
            for c in comments
        ]

    async def remove_label(self, ticket: TicketRef, label: str) -> None:
        row = await self._fetch_ticket_for_ref(ticket)
        if row is None:
            return
        if not row.labels:
            return
        new_labels = [l for l in row.labels if l != label]
        if len(new_labels) != len(row.labels):
            row.labels = new_labels
            row.updated_at = _utcnow()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_ticket_for_ref(
        self, ticket: TicketRef
    ) -> MemoryTrackerTicket | None:
        # The ref ``id`` may be either the display id (``MEM-7``) or
        # the row UUID — accept both for robustness.
        ref_id = (ticket.id or "").strip()
        if not ref_id:
            return None
        stmt = select(MemoryTrackerTicket).where(
            MemoryTrackerTicket.workspace_id == self._workspace_id,
        )
        as_uuid = _safe_uuid(ref_id)
        if as_uuid is not None:
            stmt = stmt.where(MemoryTrackerTicket.id == as_uuid)
        else:
            stmt = stmt.where(MemoryTrackerTicket.display_id == ref_id)
        return (await self._session.execute(stmt.limit(1))).scalar_one_or_none()

    async def _fetch_project(
        self, project_id: str
    ) -> MemoryTrackerProject | None:
        as_uuid = _safe_uuid(project_id)
        if as_uuid is None:
            return None
        return (
            await self._session.execute(
                select(MemoryTrackerProject).where(
                    MemoryTrackerProject.workspace_id == self._workspace_id,
                    MemoryTrackerProject.id == as_uuid,
                )
            )
        ).scalar_one_or_none()

    async def _next_serial(self) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(MemoryTrackerTicket.serial), 0)).where(
                MemoryTrackerTicket.workspace_id == self._workspace_id,
            )
        )
        return int(result.scalar_one() or 0) + 1

    async def _unique_project_slug(self, base: str) -> str:
        slug = base
        for n in range(1, 50):
            exists = (
                await self._session.execute(
                    select(MemoryTrackerProject.id).where(
                        MemoryTrackerProject.workspace_id == self._workspace_id,
                        MemoryTrackerProject.slug == slug,
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                return slug
            slug = f"{base}-{n}"
        return f"{base}-{uuid.uuid4().hex[:6]}"

    def _ticket_row_to_dict(
        self, row: MemoryTrackerTicket
    ) -> dict[str, Any]:
        return {
            "id": row.display_id,
            "title": row.title,
            "body": row.body,
            "url": self._ticket_url(row.display_id),
            "status": row.state,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "project_id": str(row.project_id) if row.project_id else None,
            "labels": list(row.labels or []),
        }

    def _project_row_to_dict(
        self, row: MemoryTrackerProject
    ) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "name": row.name,
            "slug": row.slug,
            "state": row.state,
            "url": self._project_url(row.slug),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "lead_name": None,
        }

    def _ticket_url(self, display_id: str) -> str:
        return f"{self._origin}/local-tracker/tickets/{display_id}"

    def _project_url(self, slug: str) -> str:
        return f"{self._origin}/local-tracker/projects/{slug}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_uuid(raw: str | uuid.UUID) -> uuid.UUID | None:
    if isinstance(raw, uuid.UUID):
        return raw
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError, AttributeError):
        return None


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")[:48]


def _replace_or_append_section(
    document: str, *, section: str, section_body: str
) -> str:
    """Replace ``## {section}`` block in ``document`` or append at end.

    Match is case-sensitive on the heading line — same contract the
    Linear adapter promises so decomposition agents see identical
    behaviour on either backend. The section spans from its ``##``
    heading until the next ``##`` heading or end-of-document.
    """
    section_re = re.compile(
        r"(?ms)^##\s+" + re.escape(section) + r"\s*$.*?(?=^##\s|\Z)"
    )
    replacement_block = f"## {section}\n\n{section_body.strip()}\n\n"
    if section_re.search(document):
        return section_re.sub(replacement_block, document, count=1)
    sep = "\n\n" if document.strip() else ""
    return document.rstrip() + sep + replacement_block.rstrip() + "\n"
