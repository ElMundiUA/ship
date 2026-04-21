"""Long-lived per-repo callback tokens (``SHIP_RUN_TOKEN``).

RFC-0007 lanes trigger on ``schedule`` / ``push`` / ``pull_request``
— events that don't carry a ``workflow_dispatch`` input channel. So
the short-lived JWT pipelines rely on (minted at dispatch, injected
into ``inputs.ship_run_token``) can't reach them, and a lane-driven
``shipctl run`` in those events would have no way to authenticate
its callback.

The fix is a **persistent per-repo secret**, exposed to the runner as
``secrets.SHIP_RUN_TOKEN`` on every lane invocation. This module owns
its lifecycle:

1. **Mint** — 32 random bytes, base64-urlsafe; plaintext kept in
   memory only for the duration of the PUT to GitHub's secrets API.
2. **Persist** — sha256 hex on ``workspace_repos.run_token_hash``,
   short prefix on ``run_token_prefix``, timestamp on
   ``run_token_rotated_at``. The plaintext is **never** stored.
3. **Push** — encrypted with the repo's libsodium public key and
   PUT to ``/repos/{owner}/{repo}/actions/secrets/SHIP_RUN_TOKEN``
   via the existing :func:`put_repo_secret` helper. Failure here is
   a hard error; we would rather leave the token un-rotated than
   commit a hash whose plaintext never reached the repo.
4. **Verify** — :func:`verify_repo_callback_token` hashes a presented
   bearer and returns the repo row whose ``run_token_hash`` matches,
   or ``None``. Constant-time compare keeps timing attacks off the
   table even though the hash space is already huge.

Rotation is the same path as mint — calling :func:`mint_repo_callback_token`
again overwrites all three columns and PUTs a fresh secret to GitHub.
Old plaintext in flight becomes invalid the moment the new hash is
committed (single ``updated_at`` monotonicity), which matches the
"secret rotated → old runners die 401" semantics GitHub's Actions
runtime already exposes through its own rotation flow.
"""

from __future__ import annotations

import base64
import hashlib
import secrets as stdlib_secrets
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models import GitHubInstallation, WorkspaceRepo
from backend.app.integrations.github.actions_secrets import put_repo_secret


# Name of the GitHub Actions secret. Hard-coded because ``run-agent.yml``
# and every thin wrapper Ship renders both reference this literal;
# changing it would require a coordinated migration of existing
# installations.
SHIP_RUN_TOKEN_SECRET_NAME = "SHIP_RUN_TOKEN"

# 32 random bytes → 43 base64url chars. Long enough that brute-forcing
# the hash column is infeasible, short enough to fit comfortably in
# shell exports and workflow env lines.
_TOKEN_ENTROPY_BYTES = 32

# Length of the prefix we mirror to ``run_token_prefix`` for UI display.
# Narrow enough (8 hex chars → 32 bits) that it doesn't meaningfully
# reduce the search space, wide enough for humans to eyeball
# "is this the rotation I just triggered?".
_PREFIX_HEX_LEN = 8


def _mint_plaintext() -> str:
    """Return a fresh 32-byte base64url token (no padding)."""

    raw = stdlib_secrets.token_bytes(_TOKEN_ENTROPY_BYTES)
    # ``urlsafe_b64encode`` leaves ``=`` padding which is noisy in env
    # exports; strip it for a cleaner secret value. base64 decoders
    # (we never decode this ourselves) accept both forms anyway.
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def hash_token(plaintext: str) -> str:
    """Return the canonical hex sha256 used for storage + comparison.

    Centralised so ``mint_repo_callback_token`` and
    ``verify_repo_callback_token`` cannot drift on algorithm or
    encoding and silently stop matching each other.
    """

    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _prefix_of(token_hash: str) -> str:
    return token_hash[:_PREFIX_HEX_LEN]


async def mint_repo_callback_token(
    session: AsyncSession,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    *,
    settings: Settings,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Generate a fresh ``SHIP_RUN_TOKEN`` for ``repo`` and push it.

    Ordering matters and is: **encrypt-and-PUT first, then commit the
    hash**. If GitHub's secrets API rejects the PUT (e.g. the App
    installation lost ``actions:write`` scope) we must not persist a
    hash whose plaintext never reached the repo — otherwise every
    subsequent callback from that repo would 401 against a hash we
    can't recover.

    Returns the plaintext token solely so the caller (seed-PR flow,
    admin rotation button) can react to it in the same request — no
    code path should persist it. The plaintext is out of the process
    the moment the caller's handler returns.

    Rotation is exactly this function called twice: second invocation
    overwrites ``run_token_hash`` / ``run_token_prefix`` /
    ``run_token_rotated_at`` and pushes a new secret. Runners mid-flight
    using the old plaintext will start failing auth as soon as the
    new hash commits.
    """

    plaintext = _mint_plaintext()
    token_hash = hash_token(plaintext)

    # Push first. If this raises (network error, App permissions
    # revoked, secret name rejected by GitHub) we bail without
    # touching the DB — the repo keeps whatever token it had before.
    await put_repo_secret(
        repo,
        install,
        name=SHIP_RUN_TOKEN_SECRET_NAME,
        plaintext=plaintext,
        settings=settings,
        client=client,
    )

    repo.run_token_hash = token_hash
    repo.run_token_prefix = _prefix_of(token_hash)
    repo.run_token_rotated_at = datetime.now(timezone.utc)
    repo.updated_at = repo.run_token_rotated_at

    # Flush so the caller can immediately issue queries that depend
    # on the new hash (e.g. a smoke-test callback from the same
    # request). The outer transaction still owns commit.
    await session.flush()

    return plaintext


async def verify_repo_callback_token(
    session: AsyncSession, raw_token: str
) -> WorkspaceRepo | None:
    """Look up the ``WorkspaceRepo`` whose ``SHIP_RUN_TOKEN`` is ``raw_token``.

    Returns the row on success, ``None`` on any failure (empty token,
    no matching hash, hash-prefix collision that doesn't resolve to a
    full match). Callers must treat ``None`` as "401 unauthorized" —
    never leak which of those three happened.

    The lookup is two-phase for constant-time safety:

    1. Narrow by ``run_token_prefix`` (indexed-friendly string match)
       to pull back at most a handful of rows.
    2. ``secrets.compare_digest`` each full hash against the query.

    Steady state is a single candidate (prefix is 8 hex chars, so
    ~4 billion distinct values); phase 2 is there to keep the
    authoritative comparison constant-time regardless of how many
    candidates come back.
    """

    if not raw_token:
        return None

    token_hash = hash_token(raw_token)
    prefix = _prefix_of(token_hash)

    # Short-circuit: no prefix match means no candidate row. This
    # also avoids a full table scan if someone sends a random string
    # that happens not to line up with any real token.
    stmt = select(WorkspaceRepo).where(WorkspaceRepo.run_token_prefix == prefix)
    rows = (await session.execute(stmt)).scalars().all()

    for repo in rows:
        stored = repo.run_token_hash
        if stored is None:
            continue
        if stdlib_secrets.compare_digest(stored, token_hash):
            return repo
    return None


__all__ = [
    "SHIP_RUN_TOKEN_SECRET_NAME",
    "hash_token",
    "mint_repo_callback_token",
    "verify_repo_callback_token",
]
