"""Memory code-host adapter (E19 step 4a).

Implements :class:`CodeHostGateway` over the workspace-scoped
``memory_git_*`` tables (migration 0073). The agent + orchestrator
can ask for repos, files, and pull-request state without an external
GitHub / GitLab / Bitbucket / ADO account — the laptop profile picks
this adapter up automatically when ``SHIP_USE_MEMORY_ADAPTERS=true``.

Helper write-side methods (``upsert_file``, ``open_pull_request``,
``mark_merged``) live next to the protocol surface so seed scripts +
the local-tracker UI can manipulate the simulated repo. These are
intentionally *not* on the gateway protocol — real adapters write
through `git push` and PR API calls, the memory shape does direct
table edits.

Disco shape: ``RepoRef(kind="github", owner=..., repo=...)``. We
deliberately reuse the github kind so downstream code that branches
on the kind doesn't need a "memory" arm. The dev workspace contains
one demo repo seeded by ``seed_dev.py``.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.memory_adapters import (
    MemoryCiRun,
    MemoryGitFile,
    MemoryGitPullRequest,
    MemoryGitRepo,
)
from backend.app.integrations.gateway.code_host import (
    BlobContent,
    PullRequestRef,
    RepoRef,
    RepoSummary,
)


class MemoryCodeHost:
    """Workspace-scoped in-Postgres code host."""

    _REF_KIND: Literal["github"] = "github"

    def __init__(
        self,
        *,
        session: AsyncSession,
        workspace_id: uuid.UUID,
        console_origin: str = "http://localhost:3001",
    ) -> None:
        self._session = session
        self._workspace_id = workspace_id
        self._origin = console_origin.rstrip("/")

    # ------------------------------------------------------------------
    # CodeHostGateway protocol
    # ------------------------------------------------------------------

    async def list_repos(self) -> list[RepoRef]:
        rows = (
            (
                await self._session.execute(
                    select(MemoryGitRepo).where(
                        MemoryGitRepo.workspace_id == self._workspace_id,
                    )
                    .order_by(MemoryGitRepo.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return [RepoRef(kind=self._REF_KIND, owner=r.owner, repo=r.name) for r in rows]

    async def list_repo_summaries(self) -> list[RepoSummary]:
        rows = (
            (
                await self._session.execute(
                    select(MemoryGitRepo).where(
                        MemoryGitRepo.workspace_id == self._workspace_id,
                    )
                    .order_by(MemoryGitRepo.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return [
            RepoSummary(
                ref=RepoRef(kind=self._REF_KIND, owner=r.owner, repo=r.name),
                external_id=str(r.id),
                full_name=f"{r.owner}/{r.name}",
                default_branch=r.default_branch,
                private=r.private,
                html_url=self._repo_url(r.owner, r.name),
                description=r.description,
            )
            for r in rows
        ]

    async def get_pull_request(self, ref: PullRequestRef) -> dict[str, Any]:
        repo = await self._fetch_repo_for(ref.repo)
        if repo is None:
            raise FileNotFoundError(
                f"repo {ref.repo.full_name} not found in memory adapter"
            )
        pr = (
            await self._session.execute(
                select(MemoryGitPullRequest).where(
                    MemoryGitPullRequest.repo_id == repo.id,
                    MemoryGitPullRequest.number == ref.number,
                )
            )
        ).scalar_one_or_none()
        if pr is None:
            raise FileNotFoundError(
                f"PR #{ref.number} not found on {ref.repo.full_name}"
            )
        return self._pr_to_dict(repo, pr)

    async def list_files(
        self, ref: RepoRef, *, ref_sha: str | None = None
    ) -> list[str]:
        repo = await self._fetch_repo_for(ref)
        if repo is None:
            return []
        branch = ref_sha or repo.default_branch
        rows = (
            (
                await self._session.execute(
                    select(MemoryGitFile.path).where(
                        MemoryGitFile.repo_id == repo.id,
                        MemoryGitFile.ref == branch,
                    )
                    .order_by(MemoryGitFile.path.asc())
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    async def get_blob(
        self,
        ref: RepoRef,
        *,
        path: str,
        ref_sha: str | None = None,
    ) -> BlobContent:
        repo = await self._fetch_repo_for(ref)
        if repo is None:
            raise FileNotFoundError(
                f"repo {ref.full_name} not found in memory adapter"
            )
        branch = ref_sha or repo.default_branch
        row = (
            await self._session.execute(
                select(MemoryGitFile).where(
                    MemoryGitFile.repo_id == repo.id,
                    MemoryGitFile.ref == branch,
                    MemoryGitFile.path == path,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise FileNotFoundError(
                f"{ref.full_name}@{branch}:{path} not found"
            )
        return BlobContent(
            path=row.path,
            ref=row.ref,
            sha=row.sha,
            size=row.size,
            encoding="utf-8",
            content=row.content,
        )

    # ------------------------------------------------------------------
    # Helpers — not on the gateway protocol; used by seeders + the
    # local-tracker UI to mutate the simulated repo.
    # ------------------------------------------------------------------

    async def ensure_repo(
        self,
        *,
        owner: str,
        name: str,
        default_branch: str = "main",
        description: str | None = None,
        private: bool = True,
    ) -> MemoryGitRepo:
        existing = await self._fetch_repo(owner=owner, name=name)
        if existing is not None:
            return existing
        row = MemoryGitRepo(
            workspace_id=self._workspace_id,
            owner=owner,
            name=name,
            default_branch=default_branch,
            description=description,
            private=private,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def upsert_file(
        self,
        repo: MemoryGitRepo,
        *,
        path: str,
        content: str,
        ref: str | None = None,
    ) -> MemoryGitFile:
        branch = ref or repo.default_branch
        existing = (
            await self._session.execute(
                select(MemoryGitFile).where(
                    MemoryGitFile.repo_id == repo.id,
                    MemoryGitFile.ref == branch,
                    MemoryGitFile.path == path,
                )
            )
        ).scalar_one_or_none()
        sha = hashlib.sha256(
            f"{path}\0{content}".encode("utf-8")
        ).hexdigest()
        size = len(content.encode("utf-8"))
        if existing is not None:
            existing.content = content
            existing.sha = sha
            existing.size = size
            existing.updated_at = _utcnow()
            return existing
        row = MemoryGitFile(
            repo_id=repo.id,
            ref=branch,
            path=path,
            content=content,
            sha=sha,
            size=size,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def open_pull_request(
        self,
        repo: MemoryGitRepo,
        *,
        title: str,
        body: str,
        head: str,
        base: str | None = None,
        draft: bool = False,
    ) -> MemoryGitPullRequest:
        next_number = await self._next_pr_number(repo.id)
        row = MemoryGitPullRequest(
            repo_id=repo.id,
            number=next_number,
            title=title,
            body=body,
            head=head,
            base=base or repo.default_branch,
            draft=draft,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def mark_pr_merged(
        self,
        repo: MemoryGitRepo,
        *,
        number: int,
    ) -> MemoryGitPullRequest | None:
        pr = (
            await self._session.execute(
                select(MemoryGitPullRequest).where(
                    MemoryGitPullRequest.repo_id == repo.id,
                    MemoryGitPullRequest.number == number,
                )
            )
        ).scalar_one_or_none()
        if pr is None:
            return None
        pr.state = "closed"
        pr.merged = True
        pr.merged_at = _utcnow()
        pr.updated_at = _utcnow()
        return pr

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch_repo_for(self, ref: RepoRef) -> MemoryGitRepo | None:
        return await self._fetch_repo(owner=ref.owner, name=ref.repo)

    async def _fetch_repo(
        self, *, owner: str, name: str
    ) -> MemoryGitRepo | None:
        return (
            await self._session.execute(
                select(MemoryGitRepo).where(
                    MemoryGitRepo.workspace_id == self._workspace_id,
                    MemoryGitRepo.owner == owner,
                    MemoryGitRepo.name == name,
                )
            )
        ).scalar_one_or_none()

    async def _next_pr_number(self, repo_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.coalesce(func.max(MemoryGitPullRequest.number), 0)).where(
                MemoryGitPullRequest.repo_id == repo_id
            )
        )
        return int(result.scalar_one() or 0) + 1

    def _pr_to_dict(
        self,
        repo: MemoryGitRepo,
        pr: MemoryGitPullRequest,
    ) -> dict[str, Any]:
        # Mirror GitHub's PR shape closely enough that the agent's
        # PR-reading code-paths (which key on ``state``, ``merged``,
        # ``head.sha``, ``base.ref`` etc.) work without special-casing.
        return {
            "number": pr.number,
            "title": pr.title,
            "body": pr.body,
            "state": pr.state,
            "draft": pr.draft,
            "merged": pr.merged,
            "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            "html_url": self._pr_url(repo.owner, repo.name, pr.number),
            "head": {
                "ref": pr.head,
                "sha": _pseudo_sha(repo.id, pr.head),
                "repo": {"full_name": f"{repo.owner}/{repo.name}"},
            },
            "base": {
                "ref": pr.base,
                "sha": _pseudo_sha(repo.id, pr.base),
                "repo": {"full_name": f"{repo.owner}/{repo.name}"},
            },
            "created_at": pr.created_at.isoformat() if pr.created_at else None,
            "updated_at": pr.updated_at.isoformat() if pr.updated_at else None,
        }

    def _repo_url(self, owner: str, name: str) -> str:
        return f"{self._origin}/local-tracker/repos/{owner}/{name}"

    def _pr_url(self, owner: str, name: str, number: int) -> str:
        return f"{self._origin}/local-tracker/repos/{owner}/{name}/pull/{number}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pseudo_sha(repo_id: uuid.UUID, branch: str) -> str:
    """Deterministic 40-char hex that looks like a git SHA.

    We don't actually have commits — the agent only reads the value
    so it can quote the ref in PR comments + memory facts. A stable
    pseudo-SHA keeps round-trips deterministic for replay tests.
    """
    seed = f"{repo_id}:{branch}".encode("utf-8")
    return hashlib.sha1(seed).hexdigest()
