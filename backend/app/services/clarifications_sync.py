"""Tracker → Ship clarifications projection (D13).

Ship does NOT own the lifecycle of clarifications. The customer's
tracker does — an agent running in the customer's CI posts a comment
with the ``@ship clarification:`` marker and stamps the ticket with
the ``ship:needs-clarification`` label, and this module projects the
result into Ship's read-model so the console can render a single
inbox across repos / trackers / runs.

Why projection (and not a write-only log like :class:`AuditLog`):

* Humans answer from the Ship console (that's the whole point of the
  inbox). When they do, we write the answer back to the tracker and
  strip the label — the tracker stays authoritative.
* Ship needs a dedup surface. The cron runs every N minutes; a
  webhook path may race with it. The ``(workspace, provider,
  comment_id)`` partial unique index + ``tracker_synced_at`` bookkeep
  keep repeated runs idempotent.
* The projection row carries everything the UI needs (question,
  context, tracker link) without rehydrating from the tracker on
  every page view, which would bust rate limits on any workspace
  with more than a handful of open questions.

Scope in this module:

- Constants (label + marker strings — single source of truth).
- :func:`parse_clarification_body` / :func:`parse_answer_body` — the
  text-marker extractors. Tiny on purpose; the agent's job is to
  format cleanly, Ship just isolates the question text.
- :func:`sync_workspace` — the cron / route entry point. Fans out
  across every tracker integration the workspace has, upserts
  ``source='tracker'`` rows, and marks previously-open rows
  ``stale`` when the label is gone from the ticket.
- :func:`writeback_answer` — the complement: post the human's
  answer back to the tracker and strip the label.

Notion is deliberately excluded in the pilot: Notion doesn't have
first-class labels (only per-database multi-select properties) and
wiring "which property counts as a clarification marker" needs
per-database schema awareness we don't have yet. The projection
skips ``kind=notion`` integrations silently.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.agent_surface import Clarification
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.tenancy import Integration, Workspace
from backend.app.integrations.gateway.tracker import (
    CommentRef,
    ListedIssue,
    TicketRef,
    TrackerGateway,
)
from backend.app.integrations.github.issues_tracker import GitHubIssuesTracker
from backend.app.integrations.linear.tracker_adapter import LinearTracker
from backend.app.services.inbox.dual_write import (
    mirror_clarification_create,
    mirror_clarification_resolve,
)


if TYPE_CHECKING:
    # ``decrypt`` lives under ``backend.app.api.v1.routes.integrations`` in
    # the current code base; kept behind TYPE_CHECKING so the service
    # module doesn't import from the routes package at import time.
    pass


log = logging.getLogger("ship.clarifications_sync")


# Single source of truth ------------------------------------------------

CLARIFICATION_LABEL = "ship:needs-clarification"

# Text markers inside a tracker comment. Deliberately kebab/emoji-free
# so we can eyeball them in Linear / GitHub without depending on rich
# rendering. The marker is matched case-insensitively; whitespace /
# blockquote ``>`` / bold ``**...**`` decoration is tolerated so the
# agent can format the comment for human readability without breaking
# the projection.
QUESTION_MARKER_RE = re.compile(
    r"""
    (?:^|\n)            # marker starts on its own line
    \s* >? \s*          # optional blockquote / whitespace
    (?:\*\*)?           # optional bold
    @ship\ clarification:
    (?:\*\*)?
    \s*
    (?P<body>.*?)        # capture everything up to...
    (?=                 # stop at the first of:
        \n\s*>?\s*(?:\*\*)?@ship\ answer: |   # a later @ship answer
        \Z                                   # or end of string
    )
    """,
    flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
)

ANSWER_MARKER_RE = re.compile(
    r"""
    (?:^|\n)
    \s* >? \s*
    (?:\*\*)?
    @ship\ answer:
    (?:\*\*)?
    \s*
    (?P<body>.*?)
    \Z
    """,
    flags=re.IGNORECASE | re.DOTALL | re.VERBOSE,
)


# Public shapes ---------------------------------------------------------


@dataclass
class TrackerBinding:
    """A configured tracker for the workspace — gateway + metadata."""

    provider: str  # ``linear`` / ``github_issues``
    gateway: TrackerGateway
    # For ``github_issues`` we need to know which repo scope the
    # gateway is bound to; for ``linear`` it's workspace-wide. Stored
    # so the sync log can attribute stale rows correctly.
    scope_hint: str | None = None


@dataclass
class SyncReport:
    """What the projection did on one workspace (used by tests & UI)."""

    workspace_id: uuid.UUID
    ingested: int = 0
    updated: int = 0
    stale_marked: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "workspace_id": str(self.workspace_id),
            "ingested": self.ingested,
            "updated": self.updated,
            "stale_marked": self.stale_marked,
            "errors": list(self.errors),
        }


# Marker parsing --------------------------------------------------------


def parse_clarification_body(comment_body: str) -> str | None:
    """Return the question text if ``comment_body`` carries the marker.

    The first ``@ship clarification:`` marker wins — a single comment
    containing multiple markers is degenerate and we pick the earliest
    one. ``None`` means "this isn't a clarification comment"; the
    projection skips it silently.
    """
    if not comment_body:
        return None
    match = QUESTION_MARKER_RE.search(comment_body)
    if not match:
        return None
    text = (match.group("body") or "").strip()
    # Strip per-line blockquote prefixes the agent may have added for
    # prettiness ("> "). Leaves the actual question content.
    lines = [_strip_blockquote(line) for line in text.splitlines()]
    joined = "\n".join(lines).strip()
    return joined or None


def parse_answer_body(comment_body: str) -> str | None:
    """Return the answer text if ``comment_body`` carries the marker."""
    if not comment_body:
        return None
    match = ANSWER_MARKER_RE.search(comment_body)
    if not match:
        return None
    text = (match.group("body") or "").strip()
    lines = [_strip_blockquote(line) for line in text.splitlines()]
    joined = "\n".join(lines).strip()
    return joined or None


def render_answer_comment(answer_text: str) -> str:
    """Compose the comment Ship posts when a human answers in-console.

    The agent parses tracker comments by the ``@ship answer:`` marker;
    the blockquote wrapper is purely for human readability on GitHub /
    Linear.
    """
    body = (answer_text or "").strip() or "(empty answer)"
    # Quote every line so the whole thing renders as one blockquote.
    quoted = "\n".join(f"> {line}" if line else ">" for line in body.splitlines())
    return f"> **@ship answer:**\n{quoted}\n"


def _strip_blockquote(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith(">"):
        return stripped[1:].lstrip()
    return line


# Tracker resolution ----------------------------------------------------


async def resolve_tracker_bindings(
    session: AsyncSession,
    *,
    settings: Settings,
    workspace_id: uuid.UUID,
) -> list[TrackerBinding]:
    """Build a :class:`TrackerGateway` for every configured tracker.

    Mirrors (but does NOT share code with) the agent toolbox's
    ``_resolve_tracker`` — the toolbox picks *one* tracker for a given
    ``create_ticket`` call; the projection needs *all* of them because
    a workspace may have, say, Linear for planning and GitHub Issues
    for ops-style tickets.

    ``github_issues`` is repo-scoped: we return one binding per
    activated repo so the projection can list labelled issues in
    each. The scope_hint carries ``owner/repo`` for logs.
    """
    from backend.app.api.v1.routes.integrations import decrypt  # lazy

    out: list[TrackerBinding] = []

    integrations = (
        await session.execute(
            select(Integration).where(Integration.workspace_id == workspace_id)
        )
    ).scalars().all()
    for row in integrations:
        if row.kind == "linear" and row.secret_ciphertext:
            try:
                token = decrypt(row.secret_ciphertext)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "linear token unreadable for workspace=%s: %s",
                    workspace_id,
                    exc,
                )
                continue
            out.append(
                TrackerBinding(
                    provider="linear",
                    gateway=LinearTracker(token),
                    scope_hint=None,
                )
            )
        # Notion is intentionally skipped — see module docstring.

    # GitHub Issues: one binding per activated repo.
    repos = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.installation_id.is_not(None),
            )
        )
    ).scalars().all()
    for repo in repos:
        install = await session.get(GitHubInstallation, repo.installation_id)
        if install is None or install.suspended_at is not None:
            continue
        owner, _, repo_name = (repo.full_name or "").partition("/")
        if not owner or not repo_name:
            continue
        out.append(
            TrackerBinding(
                provider="github_issues",
                gateway=GitHubIssuesTracker(
                    installation_id=install.installation_id,
                    owner=owner,
                    repo=repo_name,
                    settings=settings,
                ),
                scope_hint=repo.full_name,
            )
        )

    return out


# Projection entry point ------------------------------------------------


async def sync_workspace(
    session: AsyncSession,
    *,
    settings: Settings,
    workspace_id: uuid.UUID,
    bindings: list[TrackerBinding] | None = None,
) -> SyncReport:
    """Project the state of every tracker's labelled tickets into Ship.

    Caller supplies ``bindings`` only in tests; production uses the
    resolver. We keep the split because constructing gateways needs
    ``Settings`` + DB access but the projection logic itself is
    pure-data and easier to test against mocks.
    """
    report = SyncReport(workspace_id=workspace_id)
    if bindings is None:
        bindings = await resolve_tracker_bindings(
            session, settings=settings, workspace_id=workspace_id
        )
    if not bindings:
        return report

    # Snapshot existing tracker-sourced rows once; we use it to detect
    # rows whose label was stripped (→ ``stale``) and to skip rows the
    # agent already answered.
    existing_rows = (
        await session.execute(
            select(Clarification).where(
                Clarification.workspace_id == workspace_id,
                Clarification.source == "tracker",
            )
        )
    ).scalars().all()
    by_comment: dict[tuple[str, str], Clarification] = {
        (row.tracker_provider or "", row.tracker_comment_id or ""): row
        for row in existing_rows
        if row.tracker_comment_id
    }
    live_comment_keys: set[tuple[str, str]] = set()

    now = datetime.now(timezone.utc)

    for binding in bindings:
        try:
            issues = await binding.gateway.list_issues_with_label(
                CLARIFICATION_LABEL
            )
        except NotImplementedError:
            continue  # Tracker opted out of the projection.
        except Exception as exc:  # noqa: BLE001
            # One bad tracker must not sink the whole sync — record and
            # keep going. Sentry already captured via the route wrapper.
            report.errors.append(
                f"{binding.provider}"
                f"{'/' + binding.scope_hint if binding.scope_hint else ''}: "
                f"list_issues_with_label failed: {exc}"
            )
            continue

        for issue in issues:
            try:
                comments = await binding.gateway.list_comments(issue.ref)
            except NotImplementedError:
                continue
            except Exception as exc:  # noqa: BLE001
                report.errors.append(
                    f"{binding.provider} {issue.display_id}: "
                    f"list_comments failed: {exc}"
                )
                continue

            await _project_issue(
                session=session,
                workspace_id=workspace_id,
                binding=binding,
                issue=issue,
                comments=comments,
                by_comment=by_comment,
                live_comment_keys=live_comment_keys,
                report=report,
                now=now,
            )

    # Anything we had in DB as ``open`` but the label is gone → stale.
    # We only mark rows whose comment id didn't show up this cycle;
    # this tolerates transient label flips without churning the UI.
    for key, row in by_comment.items():
        if key in live_comment_keys:
            continue
        if row.status != "open":
            continue
        row.status = "stale"
        row.tracker_synced_at = now
        report.stale_marked += 1
        # Best-effort: mirror the stale → dismissed transition into
        # the inbox. Tracker projection runs without an HTTP user
        # context, so the audit event is stamped ``actor_kind='system'``.
        await mirror_clarification_resolve(
            session,
            clarification=row,
            actor_user_id=None,
            actor_kind="system",
        )

    return report


async def _project_issue(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    binding: TrackerBinding,
    issue: ListedIssue,
    comments: list[CommentRef],
    by_comment: dict[tuple[str, str], Clarification],
    live_comment_keys: set[tuple[str, str]],
    report: SyncReport,
    now: datetime,
) -> None:
    """Walk one issue's comments and upsert any matching rows.

    Invariants:

    * One tracker comment → one ``Clarification`` row, keyed by
      ``(provider, comment_id)``.
    * An ``@ship answer:`` comment closes the most recent preceding
      ``@ship clarification:`` on the same issue (if still open).
    * Already-``answered`` rows are left alone; humans may edit answers
      in the tracker but Ship's UI is the write authority once the row
      is closed.
    """
    issue_key = issue.display_id
    issue_url = issue.url or _fallback_issue_url(binding, issue.ref, comments)
    # Stash the vendor's ref id (Linear UUID, GitHub number, Notion id)
    # in the row's context bag so write-back can reconstruct a
    # :class:`TicketRef` without a second round-trip to the tracker.
    vendor_ref_id = issue.ref.id
    vendor_workspace_hint = issue.ref.workspace_hint

    open_rows_on_issue: list[Clarification] = []

    for comment in comments:
        question = parse_clarification_body(comment.body)
        if question is not None:
            key = (binding.provider, comment.id)
            live_comment_keys.add(key)
            row = by_comment.get(key)
            if row is None:
                row = Clarification(
                    workspace_id=workspace_id,
                    question=question,
                    status="open",
                    source="tracker",
                    tracker_provider=binding.provider,
                    tracker_issue_key=issue_key,
                    tracker_issue_url=issue_url,
                    tracker_comment_id=comment.id,
                    tracker_synced_at=now,
                    ticket_ref=issue_key,
                    context={
                        "tracker": binding.provider,
                        "issue_key": issue_key,
                        "issue_url": issue_url,
                        "comment_url": comment.url,
                        "author": comment.author,
                        # Used by write-back to rebuild a ``TicketRef``
                        # without another round-trip to the tracker.
                        "tracker_ref_id": vendor_ref_id,
                        "tracker_workspace_hint": vendor_workspace_hint,
                    },
                )
                session.add(row)
                by_comment[key] = row
                report.ingested += 1
                # Flush so the row carries an id before the dual-write
                # mirrors it; the tracker projection has no HTTP user
                # context, so the create event will be stamped
                # ``actor_kind='system'`` by intake.emit_legacy_record.
                await session.flush()
                await mirror_clarification_create(
                    session,
                    clarification=row,
                    actor_user_id=None,
                )
            else:
                # Refresh mutable metadata without touching lifecycle
                # fields (status / answer). Humans may have moved the
                # ticket — pick up the new URL / issue key so our UI
                # stays linked to the right place.
                changed = False
                if row.question != question:
                    row.question = question
                    changed = True
                if row.tracker_issue_key != issue_key:
                    row.tracker_issue_key = issue_key
                    changed = True
                if row.tracker_issue_url != issue_url:
                    row.tracker_issue_url = issue_url
                    changed = True
                row.tracker_synced_at = now
                if changed:
                    report.updated += 1

            if row.status == "open":
                open_rows_on_issue.append(row)
            continue

        answer = parse_answer_body(comment.body)
        if answer is not None and open_rows_on_issue:
            # Close the most recent open clarification on this issue
            # that isn't already answered. We only auto-close from the
            # tracker side when a human answered directly in the
            # tracker UI (not when Ship's PATCH posted an answer — that
            # path updates the row locally first).
            latest = open_rows_on_issue.pop()
            if latest.status == "open":
                latest.status = "answered"
                latest.answer = answer
                latest.answered_at = now
                latest.tracker_synced_at = now
                report.updated += 1
                # Auto-close came from the tracker UI (not a Ship
                # PATCH); mirror the resolution with system actor so
                # the audit timeline reflects the source of truth.
                await mirror_clarification_resolve(
                    session,
                    clarification=latest,
                    actor_user_id=None,
                    actor_kind="system",
                )


def _fallback_issue_url(
    binding: TrackerBinding,
    ticket: TicketRef,
    comments: list[CommentRef],
) -> str | None:
    """Issue URL when :attr:`ListedIssue.url` wasn't populated.

    Older Linear / partial responses occasionally omit the issue URL;
    we derive one from a comment URL (stripping the ``#<anchor>``
    fragment) or synthesise for GitHub.
    """
    if binding.provider == "github_issues" and binding.scope_hint:
        return f"https://github.com/{binding.scope_hint}/issues/{ticket.id}"
    for comment in comments:
        if comment.url and "#" in comment.url:
            return comment.url.split("#", 1)[0]
        if comment.url:
            return comment.url
    return None


# Write-back ------------------------------------------------------------


async def writeback_answer(
    *,
    session: AsyncSession,
    settings: Settings,
    row: Clarification,
    answer_text: str,
) -> None:
    """Post the human's answer to the tracker and strip the label.

    Called from the clarifications PATCH handler when a ``tracker``-
    sourced row is answered. We do this *after* the DB mutation so
    a failed write-back leaves the Ship row mutation rolled back
    (the route layer manages the transaction). If the tracker call
    fails, the route surfaces a 502 and the admin retries.
    """
    if row.source != "tracker":
        return  # manual / pipeline rows stay Ship-local.
    if not row.tracker_provider or not row.tracker_issue_key:
        return

    bindings = await resolve_tracker_bindings(
        session, settings=settings, workspace_id=row.workspace_id
    )
    binding = _pick_binding_for(row, bindings)
    if binding is None:
        raise LookupError(
            f"no active {row.tracker_provider} binding for this workspace"
        )
    ticket = _ticket_ref_for_row(row)
    comment_body = render_answer_comment(answer_text)
    await binding.gateway.comment(ticket, body=comment_body)
    try:
        await binding.gateway.remove_label(ticket, CLARIFICATION_LABEL)
    except NotImplementedError:
        # Adapter can't strip labels — comment is enough to signal
        # the human answered. Sentry won't catch this because it's
        # an opt-out, not an error.
        log.info(
            "tracker %s cannot remove_label; leaving label in place",
            row.tracker_provider,
        )


def _pick_binding_for(
    row: Clarification, bindings: list[TrackerBinding]
) -> TrackerBinding | None:
    """Find the binding that owns ``row``'s tracker scope.

    For ``linear`` there's one binding per workspace. For
    ``github_issues`` there's one per repo; we match on
    ``scope_hint == owner/repo`` prefix of the issue key.
    """
    for binding in bindings:
        if binding.provider != row.tracker_provider:
            continue
        if binding.provider == "github_issues":
            if binding.scope_hint and row.tracker_issue_key and (
                row.tracker_issue_key.startswith(f"{binding.scope_hint}#")
            ):
                return binding
            continue
        return binding
    return None


def _ticket_ref_for_row(row: Clarification) -> TicketRef:
    """Reconstruct the vendor :class:`TicketRef` from our projection row.

    We stash the vendor's raw ``TicketRef.id`` + ``workspace_hint``
    in ``context`` on ingest (see :func:`_project_issue`). Falling
    back to the display ``tracker_issue_key`` keeps old rows from
    earlier syncs usable — GitHub Issues numbers can be parsed out of
    ``owner/repo#123``; Linear's identifier is not a valid substitute
    for the UUID so those rows need a re-sync (acceptable trade-off
    for a pilot upgrade path).
    """
    ctx = row.context or {}
    raw_ref_id = ctx.get("tracker_ref_id")
    raw_hint = ctx.get("tracker_workspace_hint")

    if row.tracker_provider == "github_issues":
        issue_key = row.tracker_issue_key or ""
        _, _, number_from_key = issue_key.rpartition("#")
        return TicketRef(
            kind="github_issues",
            workspace_hint=(
                raw_hint
                or (issue_key.split("#", 1)[0] if "#" in issue_key else None)
            ),
            id=str(raw_ref_id or number_from_key or issue_key),
        )
    if row.tracker_provider == "linear":
        return TicketRef(
            kind="linear",
            workspace_hint=raw_hint,
            id=str(raw_ref_id or row.tracker_issue_key or ""),
        )
    raise ValueError(
        f"cannot reconstruct ticket ref for provider={row.tracker_provider!r}"
    )


# Cron fan-out ----------------------------------------------------------


async def sync_all_workspaces(
    session: AsyncSession, *, settings: Settings
) -> list[SyncReport]:
    """Run :func:`sync_workspace` for every workspace with a tracker.

    Called by the arq cron. We scope to workspaces that actually have
    an Integration row of a supported tracker kind OR at least one
    activated GitHub install so we don't waste a network round-trip
    on dormant tenants.
    """
    workspace_ids = set()
    rows = (
        await session.execute(
            select(Integration.workspace_id).where(
                Integration.kind == "linear",
                Integration.secret_ciphertext.is_not(None),
            )
        )
    ).scalars().all()
    workspace_ids.update(rows)
    repo_rows = (
        await session.execute(
            select(WorkspaceRepo.workspace_id).where(
                WorkspaceRepo.installation_id.is_not(None),
            )
        )
    ).scalars().all()
    workspace_ids.update(repo_rows)

    reports: list[SyncReport] = []
    for workspace_id in workspace_ids:
        ws = await session.get(Workspace, workspace_id)
        if ws is None:
            continue
        try:
            report = await sync_workspace(
                session, settings=settings, workspace_id=workspace_id
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "clarifications sync failed for workspace=%s: %s",
                workspace_id,
                exc,
            )
            reports.append(
                SyncReport(
                    workspace_id=workspace_id,
                    errors=[f"sync failed: {exc}"],
                )
            )
            continue
        reports.append(report)
    return reports


__all__ = [
    "CLARIFICATION_LABEL",
    "CommentRef",
    "SyncReport",
    "TrackerBinding",
    "parse_answer_body",
    "parse_clarification_body",
    "render_answer_comment",
    "resolve_tracker_bindings",
    "sync_all_workspaces",
    "sync_workspace",
    "writeback_answer",
]
