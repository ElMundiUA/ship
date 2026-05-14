"""Bootstrap the isolated e2e-navigator workspace + two users + two PATs.

Why isolated: the Navigator e2e suite creates real mem0 facts, may
spawn test projects/tickets, and exercises tenancy boundaries. Running
those against the live ``denys-99938640`` workspace would pollute the
operator's day-to-day memory and conflate test artifacts with real
work. A dedicated workspace solves both — plus it gives us the 2nd
PAT in the same workspace that the cross-user-isolation test (M10)
requires.

Idempotent: re-running detects existing rows by slug / email and
either reuses them (workspace / users / membership) or, in the PAT
case, mints fresh tokens since the raw secret is unrecoverable from
the SHA-256 mirror. Operators should run this once, paste the
printed PATs into ``.env``, and never need to touch it again unless
a token leaks.

Usage:

    DATABASE_URL=... ENCRYPTION_KEY=... JWT_SECRET=... \\
      python tools/scripts/setup_e2e_navigator_workspace.py

Output: prints the workspace UUID + two ``ship_pat_…`` tokens. Pipe
to ``tee`` if you want to capture them on disk; the secrets are NOT
re-printed on re-run.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.db.models.tenancy import (
    ApiToken,
    OrgMember,
    User,
    Workspace,
    WorkspaceMember,
)
from backend.app.security.tokens import generate_pat, hash_pat


# The org we attach the e2e workspace to. Denys's personal org —
# created during his Auth0 signup. We don't need a separate org for
# the e2e workspace; an isolated workspace under the same org is
# sufficient for tenancy testing.
DENYS_USER_EMAIL = os.environ.get(
    "E2E_SETUP_OPERATOR_EMAIL", "denys@bodyman.io"
)
WORKSPACE_SLUG = "e2e-navigator"
WORKSPACE_NAME = "E2E — Navigator suite"

# Service-user identities. Auth0 will never see these — they only
# exist server-side so we have two distinct ``users.id`` values to
# anchor the tenancy test against.
PRIMARY_USER_EMAIL = "e2e-navigator-primary@elmundi.dev"
PRIMARY_USER_NAME = "E2E Navigator (primary)"
PRIMARY_PAT_NAME = "e2e-navigator-primary-pat"

SECONDARY_USER_EMAIL = "e2e-navigator-secondary@elmundi.dev"
SECONDARY_USER_NAME = "E2E Navigator (secondary)"
SECONDARY_PAT_NAME = "e2e-navigator-secondary-pat"


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
    """Find Denys's personal org via his email."""
    user = (
        await session.execute(
            select(User).where(User.email == DENYS_USER_EMAIL)
        )
    ).scalar_one_or_none()
    if user is None:
        raise SystemExit(
            f"User {DENYS_USER_EMAIL} not found in users table; "
            "log in once through Auth0 first."
        )
    member = (
        await session.execute(
            select(OrgMember).where(OrgMember.user_id == user.id)
        )
    ).scalars().first()
    if member is None:
        raise SystemExit(
            f"User {DENYS_USER_EMAIL} has no OrgMember row — "
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
        # ``external_subject`` is the Auth0 ``sub`` claim. Service
        # users don't log in via Auth0, so we leave it NULL. The
        # auth middleware only looks at the column when validating an
        # Auth0 JWT; PAT auth path reads ``api_tokens`` and joins to
        # ``users`` directly, no Auth0 dance needed.
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
    # Upgrade role if needed.
    if existing.role != role:
        existing.role = role


async def _mint_pat(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
) -> tuple[str | None, ApiToken]:
    """Returns ``(raw_token_if_new, row)``.

    When a non-revoked PAT with this name already exists for the
    user, we return the row with raw=None — the operator must check
    their stored copy. Re-running can mint a *new* token by passing
    ``--rotate`` on the CLI, which revokes the old row first.
    """
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
        # Display prefix — first 14 chars of the raw token so the
        # operator can recognise it in token-list UIs without
        # revealing the full secret.
        prefix=raw[:14],
        scopes=["workspace.admin"],
    )
    session.add(row)
    await session.flush()
    return raw, row


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rotate",
        action="store_true",
        help=(
            "Revoke any existing e2e PATs and mint fresh ones. "
            "Use when a token leaked or .env was wiped."
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

        # Ensure Denys is also a member of this workspace as admin
        # so a live operator session can land here via the workspace
        # picker.
        denys = (
            await session.execute(
                select(User).where(User.email == DENYS_USER_EMAIL)
            )
        ).scalar_one()
        await _ensure_workspace_member(
            session,
            workspace_id=ws.id,
            user_id=denys.id,
            role="owner",
        )

        primary = await _ensure_user(
            session,
            email=PRIMARY_USER_EMAIL,
            display_name=PRIMARY_USER_NAME,
        )
        await _ensure_org_member(session, org_id=org_id, user_id=primary.id)
        await _ensure_workspace_member(
            session,
            workspace_id=ws.id,
            user_id=primary.id,
            role="admin",
        )

        secondary = await _ensure_user(
            session,
            email=SECONDARY_USER_EMAIL,
            display_name=SECONDARY_USER_NAME,
        )
        await _ensure_org_member(session, org_id=org_id, user_id=secondary.id)
        await _ensure_workspace_member(
            session,
            workspace_id=ws.id,
            user_id=secondary.id,
            role="admin",
        )

        if args.rotate:
            from datetime import datetime, timezone

            for u, name in (
                (primary, PRIMARY_PAT_NAME),
                (secondary, SECONDARY_PAT_NAME),
            ):
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

        raw_primary, _ = await _mint_pat(
            session,
            user_id=primary.id,
            workspace_id=ws.id,
            name=PRIMARY_PAT_NAME,
        )
        raw_secondary, _ = await _mint_pat(
            session,
            user_id=secondary.id,
            workspace_id=ws.id,
            name=SECONDARY_PAT_NAME,
        )

        await session.commit()

    print()
    print(f"primary user:    {primary.id}  ({primary.email})")
    print(f"secondary user:  {secondary.id}  ({secondary.email})")
    print()
    if raw_primary:
        print(f"E2E_SHIP_API_TOKEN={raw_primary}")
    else:
        print(
            "E2E_SHIP_API_TOKEN: existing token in DB; pass --rotate "
            "to mint a fresh one."
        )
    if raw_secondary:
        print(f"E2E_SHIP_API_TOKEN_SECONDARY={raw_secondary}")
    else:
        print(
            "E2E_SHIP_API_TOKEN_SECONDARY: existing token in DB; pass "
            "--rotate to mint a fresh one."
        )
    print()
    print(f"E2E_WORKSPACE_ID={ws.id}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
