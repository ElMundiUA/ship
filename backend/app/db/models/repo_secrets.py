"""Per-repo Ship-managed secrets (B10).

Problem shape
=============

Real pipeline executors (D13) need tenant-supplied credentials —
``ANTHROPIC_API_KEY`` for the agent lane, ``LINEAR_API_KEY`` for the
ticket-creation hook, ``SENTRY_AUTH_TOKEN`` for the release-notes
lane, etc. The workflow YAML reads them as ``${{ secrets.X }}``, so
they have to exist as **GitHub Actions secrets** on the tenant's
repo. Asking users to SSH into github.com Settings → Secrets by hand
defeats the "one-click onboarding" we pitch — B10 is the UI that
lets them do it from the Ship console.

Design choices worth the ink
============================

1. **Two copies, different purposes.** We keep a Fernet-encrypted
   copy in our DB *and* mirror the plaintext into GitHub Actions
   secrets via the Installation's ``actions:write`` permission. The
   DB copy is what survives GitHub "lost the secret" accidents (App
   uninstall + reinstall wipes them) and what powers the console UI
   ("when was this rotated, by whom"). The GitHub copy is what
   ``schedule:``-, ``push:``-, and ``workflow_dispatch:``-triggered
   runs actually read — i.e. the **only** way cron-triggered runs
   can see the secret without us calling ``workflow_dispatch`` first.

2. **Per-repo, not per-workspace.** Customers who operate multiple
   repos from one workspace routinely use different credentials
   per repo (prod vs. staging Linear API keys, different Sentry
   DSNs). A ``repo_id`` FK keeps the model honest; workspace-level
   sharing can be layered on later as a "fan out to all repos"
   helper rather than a schema split.

3. **``masked_hint`` stored plainly.** The last 4 plaintext
   characters are fine to keep in the clear — they're what the UI
   shows after the secret is saved ("•••••••abcd") and they don't
   shorten brute force materially. Saves one decryption per list
   render.

4. **Sync status is a first-class field.** "Is this secret actually
   live on GitHub right now?" is a question the operator asks every
   time a pipeline fails with a credentials error. We record
   ``sync_status`` + ``sync_error`` + ``last_synced_at`` so the
   console can say "stale — click to re-sync" instead of forcing
   the user to dig into Actions logs. The DB write is the source of
   truth for "what we intend"; ``sync_status`` tells them whether
   we pulled it off.

5. **Unique per ``(repo, name)``.** GitHub enforces the same
   constraint on its side; matching it locally means we can
   ``PUT /repos/.../actions/secrets/{name}`` as an upsert (GitHub
   replaces on duplicate name) without needing a compound key that
   includes the ciphertext or a version counter.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base
from backend.app.db.models.tenancy import (
    _pk,  # noqa: PLC2701 — shared helper, same package.
    _ts_created,  # noqa: PLC2701
    _ts_updated,  # noqa: PLC2701
)


# Valid values for the ``sync_status`` column. Kept in a constant
# the services layer imports so nothing downstream invents an
# untracked state string.
SYNC_STATUS_PENDING = "pending"
SYNC_STATUS_SYNCED = "synced"
SYNC_STATUS_STALE = "stale"
SYNC_STATUS_ERROR = "error"

SYNC_STATUSES = frozenset(
    {
        SYNC_STATUS_PENDING,
        SYNC_STATUS_SYNCED,
        SYNC_STATUS_STALE,
        SYNC_STATUS_ERROR,
    }
)


class RepoSecret(Base):
    """One Ship-managed secret attached to a :class:`WorkspaceRepo`.

    The name follows GitHub Actions secret naming rules — uppercase
    letters, digits, underscores, first char non-digit, ≤ 245 chars.
    We let the service layer enforce that rather than baking it
    into a DB ``CHECK`` so the error messages stay actionable ("use
    only A-Z, 0-9, _; must start with a letter") rather than a raw
    Postgres constraint violation.
    """

    __tablename__ = "repo_secrets"
    __table_args__ = (
        # GitHub enforces a one-slot-per-name rule; we mirror it so
        # upsert-on-name semantics fall out for free on our side.
        UniqueConstraint(
            "repo_id", "name", name="uq_repo_secrets_repo_id_name"
        ),
        # Hot path for the console's per-repo secrets tab: "list
        # everything for this repo, newest first". The partial
        # predicate on (workspace_id, repo_id) is already covered by
        # the unique index; this adds the ordering clause.
        Index(
            "ix_repo_secrets_repo_id_created_at",
            "repo_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()

    # Denormalised for cheap auth checks — every request carries a
    # ``workspace_id`` in the URL, so letting the DB reject
    # cross-workspace access ("is this secret actually in my
    # workspace?") with a composite predicate is faster than
    # resolving ``repo → workspace`` every time.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    repo_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspace_repos.id", ondelete="CASCADE"),
        nullable=False,
    )

    # Actions secret name, uppercase convention (GitHub's docs).
    # The service layer normalises to upper on write.
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Fernet-encrypted plaintext. Fernet tokens embed their own MAC
    # + IV + version byte so we don't need a separate column for
    # "which ENCRYPTION_KEY generation did this row use".
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # Last 4 plaintext characters, for UI "•••••••abcd" display.
    # Deliberately stored clear: it's ~13 bits of entropy, not a
    # security boundary, and fetching it requires decrypting
    # otherwise.
    masked_hint: Mapped[str | None] = mapped_column(String(8), nullable=True)

    # Free-form operator note ("Claude key for the review bot").
    # Shown in the list UI; not surfaced to workflows.
    description: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # GitHub-side bookkeeping. ``github_key_id`` is the public key
    # id we last encrypted against; we re-fetch the key on every
    # write but the column is useful for debugging ("did GitHub
    # rotate the repo key out from under us?").
    github_key_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    sync_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=text(f"'{SYNC_STATUS_PENDING}'"),
    )
    # Human-readable reason for ``sync_status != 'synced'``. Truncated
    # aggressively because GitHub error bodies can be 4KB+ and the
    # useful bit is always the first sentence.
    sync_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Who put it there. ``SET NULL`` on user delete mirrors the
    # convention in AuditLog: the row stays so operators can still
    # see "this secret exists on GitHub", but the actor vanishes.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = _ts_created()
    updated_at: Mapped[datetime] = _ts_updated()

    repo: Mapped["RepoSecret"] = relationship(
        "WorkspaceRepo", foreign_keys=[repo_id], lazy="joined"
    )


__all__ = [
    "RepoSecret",
    "SYNC_STATUS_PENDING",
    "SYNC_STATUS_SYNCED",
    "SYNC_STATUS_STALE",
    "SYNC_STATUS_ERROR",
    "SYNC_STATUSES",
]
