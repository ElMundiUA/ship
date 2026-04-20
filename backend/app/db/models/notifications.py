"""Workspace notifications — dismissible dashboard banners (A4 + A5).

These are *user-facing* banners that the console shows above the
dashboard feed. Unlike :class:`AuditLog` (append-only, compliance-
oriented) this table is explicitly UX: rows can be dismissed, they
expire from view the moment an operator clicks "Got it", and each
row is keyed so a replayed webhook doesn't stack identical banners.

Two kinds land in the pilot:

- ``pr_merged`` — A4 "Return-to-Ship after PR merge". When a PR
  the workspace cares about closes with ``merged=true``, we drop a
  friendly "welcome back" banner pointing at the PR so the user
  knows Ship noticed.

- ``self_heal_dispatched`` — A5 "Self-heal auto-trigger on
  ``workflow_run.failure``". When a CI run for an activated repo
  fails, we auto-dispatch the ``self_heal`` lane (if enabled) and
  post a banner so the user can open the healing run with one
  click instead of digging through the Pipelines page.

A third kind (``self_heal_skipped``) records *why* we didn't auto-
dispatch (pipeline off, workflow YAML missing) so the user gets a
hint rather than silence.

Future banners (billing nudges, invite reminders, C11 preset
announcements) can reuse the same table — add a new ``kind`` string
and a matching renderer on the frontend.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701  — shared helper, same package.
    _ts_created,  # noqa: PLC2701
)


class WorkspaceNotification(Base):
    """One dismissible dashboard banner for a workspace.

    ``dedupe_key`` is what keeps the inbox sane across webhook
    replays: for ``pr_merged`` we use ``pr_merged:<pr_external_id>``,
    for ``self_heal_dispatched`` we use
    ``self_heal:<failed_run_external_id>``. A partial unique index
    (see the migration) makes the database refuse duplicates at the
    write site rather than relying on the caller to remember.

    ``href`` is the deep-link the console renders on the banner's
    "Open" button — usually a PR URL (A4) or an internal
    ``/pipelines`` anchor (A5). Kept as free text so we can point at
    external systems (Linear ticket, Sentry event, …) later without
    a schema change.
    """

    __tablename__ = "workspace_notifications"
    __table_args__ = (
        # Workhorse index for "newest open banners, newest first". The
        # partial predicate (`WHERE dismissed_at IS NULL`) keeps it tiny
        # since dismissed rows dominate cumulative volume.
        Index(
            "ix_workspace_notifications_open",
            "workspace_id",
            "created_at",
            postgresql_where=text("dismissed_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    body: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    href: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # Short de-dupe token, unique per (workspace, kind-namespace). See
    # the partial unique index in migration 0011 — we let the DB enforce
    # it so a replayed webhook can't sneak a second row through.
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = _ts_created()


__all__ = ["WorkspaceNotification"]
