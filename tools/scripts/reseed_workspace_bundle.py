"""Open a wizard-seed PR per repo for a workspace that's stuck on an
older bundle.

This is the platform-admin equivalent of clicking "Update bundle" in
the Console for every repo in a workspace. Used when a workspace
(e.g. ``askslayer-e83ad0f6``) wasn't auto-migrated to the current
BUNDLE_VERSION and its agent runs went stale — tickets sitting on
legacy FSM stages (``task_intake`` / ``tech_arch_plan`` / ``qa_arch_plan``)
that the post-E16 dispatcher's routine map handles, but only when
the repo carries the current ``.ship/config.yml`` + the current
``ship-agent-run.yml`` workflow.

The script bypasses the workspace-membership gate that the regular
``/repos/{id}/wizard_seed`` HTTP endpoint enforces — operators only
need ``is_platform_admin=true`` to run this. Mechanically it inlines
the same orchestration the endpoint does:

1. mint a fresh ``SHIP_RUN_TOKEN`` for the repo via
   :func:`backend.app.services.repo_tokens.mint_repo_callback_token`
   (or reuse the existing one if intact)
2. push ``SHIP_RUN_TOKEN`` + ``SHIP_API_BASE`` + ``SHIP_API_TOKEN``
   into the repo's Actions secrets store
3. compose the seed-file list via
   :func:`backend.app.services.seed_bundle.compose_seed_files`
4. open ONE PR per repo via ``commit_bundle_pr``
5. bump ``workspace_repos.installed_bundle_version`` to the current
   ``BUNDLE_VERSION``
6. write a ``repo.wizard_seed`` audit row

Usage (from a backend pod where ``GITHUB_APP_PRIVATE_KEY`` is in env):

    PYTHONPATH=apps python tools/scripts/reseed_workspace_bundle.py \\
      --workspace-slug askslayer-e83ad0f6 --dry-run

    # confirm output, then drop --dry-run
    PYTHONPATH=apps python tools/scripts/reseed_workspace_bundle.py \\
      --workspace-slug askslayer-e83ad0f6

After the PRs land, the workspace owner / admin merges each one.
The post-merge wizard-seed-activate flow refreshes the tracker
bindings + reconciles routing. Existing tickets with legacy stage
labels keep working — the dispatcher's ``_STAGE_TO_ROUTINE`` map
still routes ``task_intake`` → ``planning`` bundle.
"""

from __future__ import annotations

import argparse
import asyncio
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
from backend.app.db.models.tenancy import AuditLog, User, Workspace
from backend.app.integrations.github.workflows import commit_bundle_pr
from backend.app.services.lane_recipes import DEFAULT_BUNDLE
from backend.app.services.repo_tokens import (
    mint_repo_callback_token,
    push_ship_methodology_github_secrets,
)
from backend.app.services.seed_bundle import BUNDLE_VERSION, compose_seed_files


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


async def _resolve_actor(session, operator_email: str) -> User:
    """Find the platform-admin user whose id will land on audit rows."""
    user = (
        await session.execute(select(User).where(User.email == operator_email))
    ).scalar_one_or_none()
    if user is None:
        raise SystemExit(
            f"operator email {operator_email!r} not found in users — "
            "pass --operator-email pointing at a platform-admin row"
        )
    if not user.is_platform_admin:
        print(
            f"WARN: {operator_email} is not platform_admin — audit row will "
            "still land but operator-side authorization is on the trust honor system."
        )
    return user


async def _reseed_one(
    session,
    *,
    workspace: Workspace,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    actor: User,
    tracker_kind: str | None,
    settings,
    client: httpx.AsyncClient,
    dry_run: bool,
) -> None:
    print(f"  → {repo.full_name} (installed bundle={repo.installed_bundle_version})")
    if install.suspended_at is not None:
        print(f"    SKIP: installation suspended at {install.suspended_at}")
        return

    # Compose the seed bundle (works in dry-run too — it's pure
    # computation, no network).
    bundle_obj = compose_seed_files(
        bundle=DEFAULT_BUNDLE,
        knowledge_slugs=[],
        tracker_kind=tracker_kind,
        workspace_default_tracker_kind=tracker_kind,
        include_fsm=True,
        repo_intel_placeholder=False,
        repo_full_name=repo.full_name,
        agents=("claude-md",),
    )
    files = bundle_obj.files
    print(f"    composed {len(files)} files for bundle v{BUNDLE_VERSION} (hash={bundle_obj.bundle_hash[:12]})")

    if dry_run:
        print(f"    DRY RUN: would bump {repo.installed_bundle_version} → {BUNDLE_VERSION}, open 1 PR")
        for path, _ in files[:5]:
            print(f"      - {path}")
        if len(files) > 5:
            print(f"      … and {len(files) - 5} more")
        return

    # 1. Rotate the per-repo run-token (returns plaintext; backend
    #    persists the hash on the repo row) BEFORE opening the PR.
    print("    minting fresh run token + pushing Actions secrets…")
    await mint_repo_callback_token(
        session, repo, install, settings=settings, client=client
    )
    # 2. Push the standard SHIP_* secrets to the repo's Actions store
    #    so the workflows installed by the PR can authenticate on
    #    their first tick. mint_new_api_pat=True also mints a fresh
    #    workspace-scoped PAT for ``SHIP_API_TOKEN``.
    await push_ship_methodology_github_secrets(
        session,
        workspace_id=workspace.id,
        acting_user_id=actor.id,
        repo=repo,
        install=install,
        settings=settings,
        mint_new_api_pat=True,
        client=client,
    )

    # 3. Open the PR.
    pr = await commit_bundle_pr(
        repo,
        install,
        files=files,
        title=f"Ship · bundle update ({BUNDLE_VERSION})",
        branch_label="bundle-reseed",
        pr_body_header=(
            f"This PR updates Ship's process bundle from "
            f"`{repo.installed_bundle_version or '(unknown)'}` to "
            f"`{BUNDLE_VERSION}`. Merge to migrate the repo onto the "
            f"E16 dispatcher path — the new `ship-agent-run.yml` "
            f"workflow lets the Ship backend fire `workflow_dispatch` "
            f"directly, and the new `.ship/config.yml` carries the "
            f"bundle-form routines (planning / dev_implementation / "
            f"validation / decomposition / code_review)."
        ),
        settings=settings,
        client=client,
    )
    print(f"    PR opened: {pr.html_url}")

    # 4. Bump DB.
    repo.installed_bundle_version = BUNDLE_VERSION
    session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=actor.id,
            actor_token_id=None,
            action="repo.wizard_seed",
            target_kind="workspace_repo",
            target_id=str(repo.id),
            payload={
                "trigger": "platform_admin_reseed_script",
                "bundle_version": BUNDLE_VERSION,
                "previous_bundle_version": repo.installed_bundle_version,
                "pr_url": pr.html_url,
                "pr_number": pr.number,
            },
        )
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--workspace-id", type=uuid.UUID)
    g.add_argument("--workspace-slug", type=str)
    parser.add_argument(
        "--operator-email",
        default="denys@bodyman.io",
        help="Platform-admin email — lands on the audit row.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compose file list + report; no commits, no DB writes.",
    )
    args = parser.parse_args()

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
        actor = await _resolve_actor(session, args.operator_email)
        print(f"workspace: {ws.id}  slug={ws.slug}  name={ws.name}")
        print(f"actor: {actor.email}  platform_admin={actor.is_platform_admin}")
        print(f"target bundle: {BUNDLE_VERSION}")
        rows = (
            await session.execute(
                select(WorkspaceRepo, GitHubInstallation)
                .join(
                    GitHubInstallation,
                    GitHubInstallation.id == WorkspaceRepo.installation_id,
                )
                .where(WorkspaceRepo.workspace_id == ws.id)
            )
        ).all()
        if not rows:
            print("no workspace_repos — nothing to do.")
            return 0
        # Resolve tracker_kind once — same value applies to every
        # repo in the workspace.
        from backend.app.services.tracker_resolver import resolve_for_workspace
        resolved = await resolve_for_workspace(
            session=session, settings=settings, workspace_id=ws.id
        )
        tracker_kind = resolved.kind if resolved else None
        print(f"tracker: {tracker_kind or '(none bound)'}")

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as http:
            for repo, install in rows:
                await _reseed_one(
                    session,
                    workspace=ws,
                    repo=repo,
                    install=install,
                    actor=actor,
                    tracker_kind=tracker_kind,
                    settings=settings,
                    client=http,
                    dry_run=args.dry_run,
                )
        if not args.dry_run:
            await session.commit()
            print("\ncommitted. The workspace owner / admin merges each PR.")
        else:
            await session.rollback()
            print("\n(dry-run; nothing written)")

    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
