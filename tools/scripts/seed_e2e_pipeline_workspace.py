"""Bootstrap the isolated ``e2e-pipeline`` workspace + service users + PATs.

Pairs with the Process e2e suite (apps/backend/app/services/dispatcher
+ shipctl-subprocess Playwright helper). Provisions a dedicated
workspace so pipeline tests don't pollute ``e2e-navigator`` state or
the operator's day-to-day workspaces.

Idempotent:
- workspace / users / org-membership reused if present
- PATs minted only when no non-revoked row exists with the same name
  (pass ``--rotate`` to revoke + re-mint when ``.env`` is wiped or a
  token leaks)

Real GitHub footprint:
- ``WorkspaceRepo`` row points at ``ElMundiUA/ship-e2e-pipeline`` with
  ``provider=github``. ``installation_id`` is left NULL initially —
  the Ship App must be installed on the sandbox repo manually after
  first push. Pass ``--installation-id <internal-uuid>`` on a re-run
  to backfill the FK once the install lands.

Tracker:
- Stays on the in-process ``memory`` adapter so agent writes are
  observable from the test process without a Linear round-trip.
  Test bodies drive ``MemoryTracker.create_ticket`` to plant the
  starting state each run.

Usage:

    DATABASE_URL=... ENCRYPTION_KEY=... JWT_SECRET=... \\
      PYTHONPATH=apps .venv/bin/python \\
      tools/scripts/seed_e2e_pipeline_workspace.py

Output: prints the workspace UUID + ``ship_pat_…`` tokens. Pipe to
``tee`` if you want to capture on disk — secrets are NOT re-printed
on idempotent re-runs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.db.models.integrations import WorkspaceRepo
from backend.app.db.models.tenancy import (
    ApiToken,
    OrgMember,
    User,
    Workspace,
    WorkspaceMember,
)
from backend.app.security.tokens import generate_pat, hash_pat


OPERATOR_EMAIL = os.environ.get("E2E_SETUP_OPERATOR_EMAIL", "denys@bodyman.io")

WORKSPACE_SLUG = "e2e-pipeline"
WORKSPACE_NAME = "E2E — Pipeline suite"

# PO writes tickets/projects via the Navigator chat; PATs scoped to
# this user drive the A0 (PO-drafts-via-chat) flow.
PO_USER_EMAIL = "e2e-pipeline-po@elmundi.dev"
PO_USER_NAME = "E2E Pipeline (PO)"
PO_PAT_NAME = "e2e-pipeline-po-pat"

# Dev user mirrors the GHA service account that the agent presents as
# when opening PRs. shipctl runs as this identity in test-driven
# subprocess mode.
DEV_USER_EMAIL = "e2e-pipeline-dev@elmundi.dev"
DEV_USER_NAME = "E2E Pipeline (dev-bot)"
DEV_PAT_NAME = "e2e-pipeline-dev-pat"

REPO_FULL_NAME = "ElMundiUA/ship-e2e-pipeline"


def _dsn() -> tuple[str, dict]:
    raw = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not raw:
        print("ERROR: DATABASE_URL / DB_URL not set", file=sys.stderr)
        sys.exit(2)
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    parts = urlsplit(raw)
    qs = dict(parse_qsl(parts.query))
    sslmode = qs.pop("sslmode", None)
    qs.pop("channel_binding", None)
    raw = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment)
    )
    return raw, ({"ssl": True} if sslmode and sslmode != "disable" else {})


async def _resolve_org_id(session: AsyncSession) -> uuid.UUID:
    user = (
        await session.execute(select(User).where(User.email == OPERATOR_EMAIL))
    ).scalar_one_or_none()
    if user is None:
        raise SystemExit(
            f"User {OPERATOR_EMAIL} not found in users table; "
            "log in once through Auth0 first."
        )
    member = (
        await session.execute(
            select(OrgMember).where(OrgMember.user_id == user.id)
        )
    ).scalars().first()
    if member is None:
        raise SystemExit(
            f"User {OPERATOR_EMAIL} has no OrgMember row — "
            "personal org missing?"
        )
    return member.org_id


async def _ensure_user(
    session: AsyncSession, *, email: str, display_name: str
) -> User:
    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()
    if user is not None:
        return user
    user = User(
        email=email,
        display_name=display_name,
        external_subject=None,
    )
    session.add(user)
    await session.flush()
    return user


async def _ensure_org_member(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str = "org_admin",
) -> None:
    existing = (
        await session.execute(
            select(OrgMember).where(
                OrgMember.org_id == org_id,
                OrgMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(OrgMember(org_id=org_id, user_id=user_id, role=role))


async def _ensure_workspace(
    session: AsyncSession, *, org_id: uuid.UUID
) -> Workspace:
    existing = (
        await session.execute(
            select(Workspace).where(
                Workspace.org_id == org_id,
                Workspace.slug == WORKSPACE_SLUG,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    ws = Workspace(
        org_id=org_id,
        slug=WORKSPACE_SLUG,
        name=WORKSPACE_NAME,
    )
    session.add(ws)
    await session.flush()
    return ws


async def _ensure_workspace_member(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str,
) -> None:
    existing = (
        await session.execute(
            select(WorkspaceMember).where(
                WorkspaceMember.workspace_id == workspace_id,
                WorkspaceMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            WorkspaceMember(
                workspace_id=workspace_id,
                user_id=user_id,
                role=role,
            )
        )
        return
    if existing.role != role:
        existing.role = role


async def _mint_pat(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
) -> tuple[str | None, ApiToken]:
    existing = (
        await session.execute(
            select(ApiToken).where(
                ApiToken.user_id == user_id,
                ApiToken.name == name,
                ApiToken.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None, existing
    raw = generate_pat()
    row = ApiToken(
        user_id=user_id,
        workspace_id=workspace_id,
        name=name,
        hashed_secret=hash_pat(raw),
        prefix=raw[:14],
        scopes=["workspace.admin"],
    )
    session.add(row)
    await session.flush()
    return raw, row


async def _ensure_workspace_repo(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    installation_id: uuid.UUID | None,
) -> WorkspaceRepo:
    """Plant the WorkspaceRepo row pointing at the real sandbox repo.

    ``external_id`` defaults to 0 when the operator hasn't supplied a
    real numeric repo id yet — we backfill on the first webhook
    delivery from GitHub. The de-dupe key is
    ``(workspace_id, provider, external_id)``; running with the
    placeholder twice would conflict, so we look up by
    ``full_name`` first.
    """
    existing = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.workspace_id == workspace_id,
                WorkspaceRepo.full_name == REPO_FULL_NAME,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if installation_id is not None and existing.installation_id != installation_id:
            existing.installation_id = installation_id
            existing.activated_at = datetime.now(timezone.utc)
        return existing
    row = WorkspaceRepo(
        workspace_id=workspace_id,
        installation_id=installation_id,
        provider="github",
        external_id=0,  # placeholder; webhook delivery backfills
        full_name=REPO_FULL_NAME,
        default_branch="main",
        private=True,
        html_url=f"https://github.com/{REPO_FULL_NAME}",
        description="Ship pipeline e2e sandbox.",
        activated_at=datetime.now(timezone.utc) if installation_id else None,
    )
    session.add(row)
    await session.flush()
    return row


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rotate",
        action="store_true",
        help="Revoke existing e2e PATs and mint fresh ones.",
    )
    parser.add_argument(
        "--installation-id",
        type=uuid.UUID,
        default=None,
        help=(
            "Backfill the WorkspaceRepo.installation_id FK after the "
            "Ship App is installed on the sandbox repo. Take the value "
            "from github_installations.id."
        ),
    )
    args = parser.parse_args()

    db_url, connect_args = _dsn()
    engine = create_async_engine(db_url, future=True, connect_args=connect_args)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        org_id = await _resolve_org_id(session)
        ws = await _ensure_workspace(session, org_id=org_id)
        print(f"workspace: {ws.id}  ({ws.slug})")

        operator = (
            await session.execute(
                select(User).where(User.email == OPERATOR_EMAIL)
            )
        ).scalar_one()
        await _ensure_workspace_member(
            session,
            workspace_id=ws.id,
            user_id=operator.id,
            role="owner",
        )

        po = await _ensure_user(
            session, email=PO_USER_EMAIL, display_name=PO_USER_NAME
        )
        await _ensure_org_member(session, org_id=org_id, user_id=po.id)
        await _ensure_workspace_member(
            session, workspace_id=ws.id, user_id=po.id, role="admin"
        )

        dev = await _ensure_user(
            session, email=DEV_USER_EMAIL, display_name=DEV_USER_NAME
        )
        await _ensure_org_member(session, org_id=org_id, user_id=dev.id)
        await _ensure_workspace_member(
            session, workspace_id=ws.id, user_id=dev.id, role="admin"
        )

        repo = await _ensure_workspace_repo(
            session,
            workspace_id=ws.id,
            installation_id=args.installation_id,
        )
        print(
            f"workspace_repo: {repo.id}  ({repo.full_name}, "
            f"installation_id={repo.installation_id})"
        )

        if args.rotate:
            for u, name in ((po, PO_PAT_NAME), (dev, DEV_PAT_NAME)):
                existing = (
                    await session.execute(
                        select(ApiToken).where(
                            ApiToken.user_id == u.id,
                            ApiToken.name == name,
                            ApiToken.revoked_at.is_(None),
                        )
                    )
                ).scalars().all()
                for tok in existing:
                    tok.revoked_at = datetime.now(timezone.utc)

        raw_po, _ = await _mint_pat(
            session, user_id=po.id, workspace_id=ws.id, name=PO_PAT_NAME
        )
        raw_dev, _ = await _mint_pat(
            session, user_id=dev.id, workspace_id=ws.id, name=DEV_PAT_NAME
        )

        await session.commit()

    print()
    print(f"PO user:   {po.id}  ({po.email})")
    print(f"dev user:  {dev.id}  ({dev.email})")
    print()
    print(f"E2E_PIPELINE_WORKSPACE_ID={ws.id}")
    if raw_po:
        print(f"E2E_PIPELINE_PO_TOKEN={raw_po}")
    else:
        print(
            "E2E_PIPELINE_PO_TOKEN: existing token in DB; pass --rotate to "
            "mint fresh."
        )
    if raw_dev:
        print(f"E2E_PIPELINE_DEV_TOKEN={raw_dev}")
    else:
        print(
            "E2E_PIPELINE_DEV_TOKEN: existing token in DB; pass --rotate to "
            "mint fresh."
        )
    print()
    print(f"E2E_PIPELINE_REPO={REPO_FULL_NAME}")
    if repo.installation_id is None:
        print()
        print(
            "NOTE: WorkspaceRepo.installation_id is NULL. Install the Ship "
            "App on the sandbox repo via the Console wizard, then re-run "
            "this script with --installation-id <github_installations.id> "
            "to backfill."
        )

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
