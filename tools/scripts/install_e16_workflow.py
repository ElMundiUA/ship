"""Push the E16 ``ship-agent-run.yml`` workflow into a workspace's
customer repos using the Ship App's installation token.

Background: pre-E16 customer repos run the legacy
``Ship · Schedule trigger`` cron workflow. After the E16 cutover the
backend dispatcher fires ``ship-agent-run.yml`` via
``workflow_dispatch``. Repos onboarded before the cutover (or that
never reseeded their bundle) lack the new workflow, so the backend
can't dispatch into them — they sit on the legacy cron path and
go silent the moment GHA auto-disables the schedule (long fail
streak / 60-day inactivity).

This script is the one-shot migration: for the named workspace,
walk every ``WorkspaceRepo`` row, mint an installation token, and
commit ``apps/backend/app/resources/starter_workflows/ship-agent-run.yml``
to that repo at ``.github/workflows/ship-agent-run.yml`` on the
default branch. Idempotent — if the file already exists with the
same content, no commit lands.

Usage:

    ANTHROPIC_API_KEY=... DATABASE_URL=postgresql://... \\
      PYTHONPATH=apps .venv/bin/python \\
      tools/scripts/install_e16_workflow.py \\
        --workspace-slug askslayer-e83ad0f6

    # or by id
    tools/scripts/install_e16_workflow.py \\
      --workspace-id 2afef370-893c-4610-b31e-da0de5aa7c47

    # dry-run prints planned commits without writing
    --dry-run

Requires the Ship backend's GitHub App private key in env
(``GITHUB_APP_PRIVATE_KEY`` or the path the backend reads from).
The script reuses the backend's ``fetch_installation_token`` so
auth shape is identical to live dispatch.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import os
import sys
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.models.integrations import (
    GitHubInstallation,
    WorkspaceRepo,
)
from backend.app.db.models.tenancy import Workspace
from backend.app.integrations.github.app_auth import fetch_installation_token


STARTER = ROOT / "apps" / "backend" / "app" / "resources" / "starter_workflows" / "ship-agent-run.yml"
TARGET_PATH = ".github/workflows/ship-agent-run.yml"
COMMIT_MESSAGE = (
    "ci(ship): install E16 dispatcher workflow\n\n"
    "Adds ``ship-agent-run.yml`` so the Ship backend dispatcher\n"
    "can fire ``workflow_dispatch`` directly instead of relying on\n"
    "the legacy ``Ship · Schedule trigger`` cron. After this lands\n"
    "the repo can keep moving even if the schedule trigger gets\n"
    "GHA-disabled by an unrelated fail streak.\n"
)


def _dsn() -> tuple[str, dict]:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise SystemExit("DATABASE_URL not set")
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


async def _resolve_workspace(
    session,
    *,
    workspace_id: uuid.UUID | None,
    workspace_slug: str | None,
) -> Workspace:
    stmt = select(Workspace)
    if workspace_id:
        stmt = stmt.where(Workspace.id == workspace_id)
    elif workspace_slug:
        stmt = stmt.where(Workspace.slug == workspace_slug)
    else:
        raise SystemExit("pass --workspace-id or --workspace-slug")
    ws = (await session.execute(stmt)).scalar_one_or_none()
    if ws is None:
        raise SystemExit("workspace not found")
    return ws


async def _list_repos(
    session, workspace_id: uuid.UUID
) -> list[tuple[WorkspaceRepo, GitHubInstallation]]:
    rows = (
        await session.execute(
            select(WorkspaceRepo, GitHubInstallation)
            .join(
                GitHubInstallation,
                GitHubInstallation.id == WorkspaceRepo.installation_id,
            )
            .where(WorkspaceRepo.workspace_id == workspace_id)
        )
    ).all()
    return [(r, g) for r, g in rows]


async def _get_file(
    client: httpx.AsyncClient,
    *,
    full_name: str,
    branch: str,
    path: str,
    token: str,
) -> tuple[str | None, str | None]:
    """Return (sha, base64_content) for an existing file or (None, None)
    when missing. Lets us 1/ idempotently detect "already installed
    with same content" and 2/ pass ``sha`` on subsequent PUTs to
    update rather than 422."""
    r = await client.get(
        f"https://api.github.com/repos/{full_name}/contents/{path}",
        params={"ref": branch},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    if r.status_code == 404:
        return None, None
    r.raise_for_status()
    data = r.json()
    return data.get("sha"), data.get("content")


async def _put_file(
    client: httpx.AsyncClient,
    *,
    full_name: str,
    branch: str,
    path: str,
    content_bytes: bytes,
    token: str,
    sha: str | None,
    message: str,
) -> dict:
    payload: dict[str, object] = {
        "message": message,
        "content": base64.b64encode(content_bytes).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    r = await client.put(
        f"https://api.github.com/repos/{full_name}/contents/{path}",
        json=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    r.raise_for_status()
    return r.json()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--workspace-id", type=uuid.UUID)
    g.add_argument("--workspace-slug", type=str)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned commits without writing.",
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="Override target branch (default: repo's default_branch).",
    )
    args = parser.parse_args()

    if not STARTER.is_file():
        raise SystemExit(f"starter workflow missing: {STARTER}")
    content = STARTER.read_bytes()
    content_sha = hashlib.sha256(content).hexdigest()[:12]
    print(f"starter: {STARTER}  sha256={content_sha}  bytes={len(content)}")

    db_url, connect_args = _dsn()
    engine = create_async_engine(db_url, future=True, connect_args=connect_args)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()

    async with Session() as session:
        ws = await _resolve_workspace(
            session,
            workspace_id=args.workspace_id,
            workspace_slug=args.workspace_slug,
        )
        print(f"workspace: {ws.id}  slug={ws.slug}  name={ws.name}")
        rows = await _list_repos(session, ws.id)
        if not rows:
            print("no workspace_repos rows — nothing to do.")
            return 0
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
            for repo, install in rows:
                if install.suspended_at is not None:
                    print(f"  SKIP {repo.full_name}: installation suspended")
                    continue
                branch = args.branch or repo.default_branch or "main"
                token = await fetch_installation_token(
                    install.installation_id, settings=settings, client=http
                )
                print(f"  → {repo.full_name}@{branch}: minted install token (last4={token[-4:]})")

                existing_sha, existing_b64 = await _get_file(
                    http,
                    full_name=repo.full_name,
                    branch=branch,
                    path=TARGET_PATH,
                    token=token,
                )
                if existing_b64:
                    decoded = base64.b64decode(existing_b64)
                    if decoded == content:
                        print(f"    already installed, identical content — skip")
                        continue
                    print(f"    already exists, content drift — will update (sha={existing_sha[:8]})")
                else:
                    print(f"    no existing file — will create")

                if args.dry_run:
                    print(f"    DRY RUN: would PUT {TARGET_PATH}")
                    continue

                result = await _put_file(
                    http,
                    full_name=repo.full_name,
                    branch=branch,
                    path=TARGET_PATH,
                    content_bytes=content,
                    token=token,
                    sha=existing_sha,
                    message=COMMIT_MESSAGE,
                )
                commit_sha = result.get("commit", {}).get("sha", "?")
                print(f"    committed {commit_sha[:8]} → {result.get('content', {}).get('html_url')}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
