"""One-shot delete-PR for legacy Ship workflows that pre-date the
E16 cutover.

Bundle reseed PRs (``commit_bundle_pr``) can only **add** or update
files — they have no concept of deletions. Customers that came
through onboarding before ELS-124 retired the cron-based picker
ended up with both the legacy ``ship-trigger-schedule.yml`` (still
firing hourly) and the current ``ship-agent-run.yml`` (fired by the
backend dispatcher) coexisting in their repo. The legacy workflow
keeps blowing up on every cron tick because its routine slugs
(``workflow-self-heal`` etc.) no longer exist in the catalog —
which is what askslayer hit this morning.

This script opens ONE PR per repo that DELETES the legacy
workflows via the git tree API (``sha: null`` entries on a new
tree built from the parent's base_tree). Customer workflows
(``flutter.yml``, ``deploy-staging.yml``) and Ship's own CI on
ElMundiUA/ship (``ci.yml`` / ``docker-publish-platform.yml`` etc.)
are left untouched — only files starting with the deprecated
Ship-managed prefixes are removed.

Requires GH App credentials in env (mint installation tokens):

    GITHUB_APP_ID=3434673
    GITHUB_APP_PRIVATE_KEY="$(cat path/to/ship-elmundi.*.pem)"
    DATABASE_URL=...
    PYTHONPATH=apps python tools/scripts/delete_legacy_ship_workflows.py \\
      --workspace-slug askslayer-e83ad0f6 [--workspace-slug denys-99938640] \\
      --dry-run

Drop ``--dry-run`` to actually open PRs.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
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


# Filenames considered legacy and safe to delete WHEN PRESENT in any
# repo Ship manages. Anything else (including customer-owned CI like
# ``flutter.yml`` and Ship's own monorepo CI like ``ci.yml``) is left
# alone — we never delete workflows we didn't seed.
LEGACY_WORKFLOW_FILES: tuple[str, ...] = (
    ".github/workflows/ship-trigger-schedule.yml",
    ".github/workflows/parallel-audit-lanes.yml",
    ".github/workflows/scheduled-sdlc-lane.yml",
    ".github/workflows/pr-and-ci-gate.yml",
    ".github/workflows/adhoc-agent-run.yml",
    ".github/workflows/lanes-smoke.yml",
    ".github/workflows/ship-bootstrap.yml",
    ".github/workflows/run-agent.yml",
    ".github/workflows/e2e-console.yml",
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
    sslmode = qs.pop("sslmode", None); qs.pop("channel_binding", None)
    raw = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment))
    return raw, ({"ssl": True} if sslmode and sslmode != "disable" else {})


async def _file_exists(
    client: httpx.AsyncClient, *, full_name: str, branch: str, path: str, token: str
) -> bool:
    r = await client.get(
        f"https://api.github.com/repos/{full_name}/contents/{path}",
        params={"ref": branch},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    return r.status_code == 200


async def _git_get_ref(
    client: httpx.AsyncClient, *, full_name: str, branch: str, token: str
) -> str:
    r = await client.get(
        f"https://api.github.com/repos/{full_name}/git/ref/heads/{branch}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    r.raise_for_status()
    return r.json()["object"]["sha"]


async def _git_get_commit_tree(
    client: httpx.AsyncClient, *, full_name: str, commit_sha: str, token: str
) -> str:
    r = await client.get(
        f"https://api.github.com/repos/{full_name}/git/commits/{commit_sha}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    r.raise_for_status()
    return r.json()["tree"]["sha"]


async def _git_create_tree(
    client: httpx.AsyncClient,
    *,
    full_name: str,
    base_tree: str,
    deletions: list[str],
    token: str,
) -> str:
    # Tree entries with ``sha: null`` remove the path from the new tree.
    tree_entries = [
        {"path": p, "mode": "100644", "type": "blob", "sha": None}
        for p in deletions
    ]
    r = await client.post(
        f"https://api.github.com/repos/{full_name}/git/trees",
        json={"base_tree": base_tree, "tree": tree_entries},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    r.raise_for_status()
    return r.json()["sha"]


async def _git_create_commit(
    client: httpx.AsyncClient,
    *,
    full_name: str,
    tree_sha: str,
    parent_sha: str,
    message: str,
    token: str,
) -> str:
    r = await client.post(
        f"https://api.github.com/repos/{full_name}/git/commits",
        json={"message": message, "tree": tree_sha, "parents": [parent_sha]},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    r.raise_for_status()
    return r.json()["sha"]


async def _git_create_ref(
    client: httpx.AsyncClient,
    *,
    full_name: str,
    branch: str,
    commit_sha: str,
    token: str,
) -> None:
    r = await client.post(
        f"https://api.github.com/repos/{full_name}/git/refs",
        json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    r.raise_for_status()


async def _open_pr(
    client: httpx.AsyncClient,
    *,
    full_name: str,
    head: str,
    base: str,
    title: str,
    body: str,
    token: str,
) -> dict:
    r = await client.post(
        f"https://api.github.com/repos/{full_name}/pulls",
        json={"title": title, "head": head, "base": base, "body": body},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    r.raise_for_status()
    return r.json()


async def _process_repo(
    *,
    workspace: Workspace,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    settings,
    client: httpx.AsyncClient,
    dry_run: bool,
) -> None:
    print(f"\n→ {repo.full_name}@{repo.default_branch} (ws={workspace.slug})")
    if install.suspended_at is not None:
        print(f"  SKIP: installation suspended")
        return

    token = await fetch_installation_token(
        install.installation_id, settings=settings, client=client
    )

    # 1. Probe which legacy paths actually exist in this repo.
    present: list[str] = []
    for path in LEGACY_WORKFLOW_FILES:
        if await _file_exists(
            client, full_name=repo.full_name, branch=repo.default_branch, path=path, token=token
        ):
            present.append(path)
            print(f"  found  {path}")

    if not present:
        print("  nothing legacy here — skip")
        return
    if dry_run:
        print(f"  DRY RUN: would delete {len(present)} file(s) in one PR")
        return

    # 2. Build a new tree without the legacy entries on top of HEAD.
    head_sha = await _git_get_ref(
        client, full_name=repo.full_name, branch=repo.default_branch, token=token
    )
    base_tree = await _git_get_commit_tree(
        client, full_name=repo.full_name, commit_sha=head_sha, token=token
    )
    new_tree = await _git_create_tree(
        client, full_name=repo.full_name, base_tree=base_tree, deletions=present, token=token
    )
    msg = (
        "ci(ship): remove legacy Ship-managed workflows\n\n"
        "Pre-E16 / pre-lanes-cleanup workflow files that bundle\n"
        "reseed couldn't remove on its own (commit_bundle_pr does\n"
        "additions only). Their cron triggers keep firing against\n"
        "agent roles that the catalog no longer ships, blowing up\n"
        "every tick. Deleted via tools/scripts/"
        "delete_legacy_ship_workflows.py:\n\n"
        + "\n".join(f"  - {p}" for p in present)
    )
    commit_sha = await _git_create_commit(
        client, full_name=repo.full_name, tree_sha=new_tree, parent_sha=head_sha,
        message=msg, token=token,
    )
    branch = f"ship/cleanup-legacy-workflows-{int(time.time())}"
    await _git_create_ref(
        client, full_name=repo.full_name, branch=branch, commit_sha=commit_sha, token=token
    )
    pr = await _open_pr(
        client,
        full_name=repo.full_name,
        head=branch,
        base=repo.default_branch,
        title="Ship · cleanup: remove legacy Ship-managed workflows",
        body=(
            "Pre-E16 / lanes-cleanup workflow files are still in this repo\n"
            "even though the bundle reseed bumped the dispatch path to the\n"
            "current `ship-agent-run.yml`. Their cron triggers keep firing\n"
            "against routines the backend catalog no longer ships, which\n"
            "is the root cause of the recurring `unknown agent role …`\n"
            "errors in the Actions tab.\n\n"
            "Files deleted in this PR:\n\n"
            + "\n".join(f"- `{p}`" for p in present)
            + "\n\nSafe to merge once CI on the deletion branch is green.\n"
            "After merge, the remaining `ship-agent-run.yml` is the only\n"
            "Ship-managed workflow and the backend dispatcher drives it\n"
            "via `workflow_dispatch`."
        ),
        token=token,
    )
    print(f"  PR opened: {pr['html_url']}")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-slug", action="append", required=True,
        help="Workspace slug — repeat to target multiple workspaces.",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_url, connect_args = _dsn()
    engine = create_async_engine(db_url, future=True, connect_args=connect_args)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    settings = get_settings()

    async with Session() as session:
        rows = await session.execute(
            select(WorkspaceRepo, GitHubInstallation, Workspace)
            .join(GitHubInstallation, GitHubInstallation.id == WorkspaceRepo.installation_id)
            .join(Workspace, Workspace.id == WorkspaceRepo.workspace_id)
            .where(Workspace.slug.in_(tuple(args.workspace_slug)))
        )
        targets = [(ws, repo, install) for repo, install, ws in rows.all()]
        if not targets:
            print("no repos matched — nothing to do")
            return 0
        print(f"target workspaces: {sorted({ws.slug for ws, *_ in targets})}")
        print(f"target repos: {len(targets)}")
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
            for ws, repo, install in targets:
                await _process_repo(
                    workspace=ws, repo=repo, install=install,
                    settings=settings, client=http, dry_run=args.dry_run,
                )
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
