"""Seed the laptop-dev Postgres with one workspace + one operator.

Designed for use by ``make dev-up`` against the docker-compose
``postgres`` service. Idempotent — re-runs detect existing rows by
email/slug and reuse them rather than duplicating.

Default credentials (override via env if you want something else):

    Email:    dev@localhost
    Password: dev

The user lands in an Org ``local-dev`` with one Workspace ``dev``,
role ``owner``. Local-auth (``SHIP_AUTH_MODE=local``) is the
default profile for laptop dev; the user's ``password_hash`` is
populated so ``POST /v1/auth/local/login`` accepts them right
after seed.

Usage:

    DATABASE_URL=postgresql://ship:ship@localhost:5433/ship \\
      python tools/scripts/seed_dev.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

# Env is layered by ``tools/scripts/run-with-dotenv.mjs`` BEFORE we
# launch — .env.shared then .env then any --set passed by the
# Makefile. We deliberately do NOT call load_dotenv() here: the
# Makefile's ``--set DATABASE_URL=...`` must win, and a second
# load_dotenv(override=True) would un-do that, sending writes to
# whatever .env says (typically the operator's Neon DSN).
ROOT = Path(__file__).resolve().parents[2]

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.db.models.tenancy import (
    Org,
    OrgMember,
    User,
    Workspace,
    WorkspaceMember,
)
from backend.app.security.passwords import hash_password


DEV_EMAIL = os.environ.get("DEV_SEED_EMAIL", "dev@ship.dev")
DEV_PASSWORD = os.environ.get("DEV_SEED_PASSWORD", "dev")
DEV_USER_NAME = "Dev User"

ORG_SLUG = "local-dev"
ORG_NAME = "Local Dev"
WORKSPACE_SLUG = "dev"
WORKSPACE_NAME = "Dev workspace"


def _dsn() -> tuple[str, dict]:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    if raw.startswith("postgresql+psycopg://"):
        raw = raw.replace("postgresql+psycopg://", "postgresql+asyncpg://", 1)
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    parts = urlsplit(raw)
    qs = dict(parse_qsl(parts.query))
    sslmode = qs.pop("sslmode", None)
    qs.pop("channel_binding", None)
    raw = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment)
    )
    return raw, ({"ssl": True} if sslmode and sslmode != "disable" else {})


async def main() -> int:
    db_url, connect_args = _dsn()
    engine = create_async_engine(db_url, future=True, connect_args=connect_args)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        # ---- User --------------------------------------------------------
        user = (
            await session.execute(
                select(User).where(User.email == DEV_EMAIL)
            )
        ).scalar_one_or_none()
        if user is None:
            user = User(
                email=DEV_EMAIL,
                display_name=DEV_USER_NAME,
                password_hash=hash_password(DEV_PASSWORD),
            )
            session.add(user)
            await session.flush()
            print(f"created user: {DEV_EMAIL}  ({user.id})")
        else:
            # If the password got wiped (e.g. fresh-bootstrap forgetting),
            # re-set it. Cheap and idempotent.
            if user.password_hash is None:
                user.password_hash = hash_password(DEV_PASSWORD)
                print(f"refreshed password for: {DEV_EMAIL}")
            else:
                print(f"reuse user: {DEV_EMAIL}  ({user.id})")

        # ---- Org + workspace --------------------------------------------
        org = (
            await session.execute(select(Org).where(Org.slug == ORG_SLUG))
        ).scalar_one_or_none()
        if org is None:
            org = Org(slug=ORG_SLUG, name=ORG_NAME, plan="free")
            session.add(org)
            await session.flush()
            print(f"created org:  {ORG_SLUG}  ({org.id})")
        else:
            print(f"reuse org:  {ORG_SLUG}  ({org.id})")

        org_member = (
            await session.execute(
                select(OrgMember).where(
                    OrgMember.org_id == org.id,
                    OrgMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if org_member is None:
            session.add(
                OrgMember(org_id=org.id, user_id=user.id, role="org_owner")
            )

        workspace = (
            await session.execute(
                select(Workspace).where(
                    Workspace.org_id == org.id,
                    Workspace.slug == WORKSPACE_SLUG,
                )
            )
        ).scalar_one_or_none()
        if workspace is None:
            workspace = Workspace(
                org_id=org.id,
                slug=WORKSPACE_SLUG,
                name=WORKSPACE_NAME,
            )
            session.add(workspace)
            await session.flush()
            print(f"created ws:   {WORKSPACE_SLUG}  ({workspace.id})")
        else:
            print(f"reuse ws:   {WORKSPACE_SLUG}  ({workspace.id})")

        ws_member = (
            await session.execute(
                select(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == workspace.id,
                    WorkspaceMember.user_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if ws_member is None:
            session.add(
                WorkspaceMember(
                    workspace_id=workspace.id,
                    user_id=user.id,
                    role="owner",
                    answer_specialist_slugs=["*"],
                )
            )

        await session.commit()

    print()
    print("=" * 60)
    print(f"  login email:    {DEV_EMAIL}")
    print(f"  login password: {DEV_PASSWORD}")
    print(f"  workspace:      {WORKSPACE_NAME}  ({workspace.id})")
    print(f"  console:        http://localhost:3001/login")
    print("=" * 60)

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
