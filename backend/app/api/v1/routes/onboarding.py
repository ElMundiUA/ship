"""Repo-driven onboarding API.

The wizard in ``console/src/app/onboarding/page.tsx`` calls these endpoints
in order:

1. ``POST /v1/onboarding/inspect`` — given a repo URL or path, return a
   :class:`RepoProfile` so the wizard can suggest a workspace name and
   recommend workflow artifacts.
2. ``POST /v1/onboarding/scaffold-demo-repo`` — create a tiny fixture repo
   under the workbench. Used by the smoke test and by users who just want
   to see the wizard run end-to-end without standing up a project first.
3. ``POST /v1/onboarding/install-workflows`` — write the chosen workflow
   artifacts into the user's repo and commit them.
4. ``POST /v1/onboarding/seed-knowledge`` — generate the brandbook /
   code-style / testing markdowns and commit them under
   ``.ship/knowledge/``.

All of these require an authenticated session. ``install`` and ``seed``
also gate on workspace membership so a stray PAT cannot mutate someone
else's working copy.
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import ROLES_ADMIN, _require_membership
from backend.app.db.models.tenancy import ArtifactRepo, AuditLog
from backend.app.db.session import get_session
from backend.app.services import knowledge_seeder, repo_inspector, workflow_installer


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/onboarding", tags=["onboarding"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class InspectRequest(BaseModel):
    source: str = Field(min_length=1, max_length=2048)


class RepoProfileOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    source_kind: str
    local_path: str
    cached: bool
    suggested_name: str
    suggested_slug: str
    head_branch: str | None
    head_sha: str | None
    remote_url: str | None
    file_count: int
    truncated: bool
    languages: dict[str, int]
    primary_language: str | None
    frameworks: list[str]
    package_managers: list[str]
    has_readme: bool
    readme_excerpt: str | None
    has_tests: bool
    test_frameworks: list[str]
    has_ci: bool
    ci_systems: list[str]
    code_style_configs: list[str]
    recommended_workflows: list[str]


class ScaffoldDemoResponse(BaseModel):
    path: str
    suggestion: str  # e.g. "file:///tmp/ship-repos/demo-…"


class InstallWorkflowsRequest(BaseModel):
    workspace_id: uuid.UUID
    repo_source: str = Field(min_length=1, max_length=2048)
    workflow_ids: list[str] = Field(min_length=1, max_length=20)


class InstalledFileOut(BaseModel):
    path: str
    bytes_written: int
    overwrote_existing: bool


class InstalledWorkflowOut(BaseModel):
    id: str
    name: str | None
    version: str | None
    install_target: str
    contract_path: str


class InstallSkippedOut(BaseModel):
    id: str
    reason: str


class InstallResultOut(BaseModel):
    repo_path: str
    branch: str | None
    head_before: str | None
    head_after: str | None
    commit_made: bool
    files: list[InstalledFileOut]
    installed: list[InstalledWorkflowOut]
    skipped: list[InstallSkippedOut]


class SeedKnowledgeRequest(BaseModel):
    workspace_id: uuid.UUID
    repo_source: str = Field(min_length=1, max_length=2048)
    bucket_slugs: list[str] | None = None


class SeededDocOut(BaseModel):
    slug: str
    title: str
    path: str
    bytes_written: int
    excerpt: str


class SeedResultOut(BaseModel):
    repo_path: str
    branch: str | None
    head_before: str | None
    head_after: str | None
    commit_made: bool
    docs: list[SeededDocOut]


# ---------------------------------------------------------------------------
# Inspect
# ---------------------------------------------------------------------------


def _profile_to_out(profile: repo_inspector.RepoProfile) -> RepoProfileOut:
    return RepoProfileOut(**dataclasses.asdict(profile))


@router.post("/inspect", response_model=RepoProfileOut)
async def inspect_repo(
    payload: InspectRequest,
    auth: AuthContext = Depends(get_current_auth),
) -> RepoProfileOut:
    # Authenticated, but no workspace gate yet — inspect is read-only and
    # used pre-workspace-create.
    _ = auth
    try:
        profile = repo_inspector.inspect(payload.source)
    except repo_inspector.RepoSourceError as exc:
        raise HTTPException(
            status_code=400 if exc.code == "not_found" else 422,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("repo inspect failed for %s", payload.source)
        raise HTTPException(
            status_code=500,
            detail={"code": "inspect_failed", "message": str(exc)},
        ) from exc
    return _profile_to_out(profile)


# ---------------------------------------------------------------------------
# Scaffold a demo repo (handy for the wizard's "I just want to see it" path)
# ---------------------------------------------------------------------------


_DEMO_README = """\
# Aurora Notes

Aurora Notes is a tiny markdown notebook for product teams. It runs as a
Next.js + FastAPI duo and ships with a Playwright smoke pack.

We pride ourselves on **calm UI**, **honest copy**, and **PRs you can read in
five minutes**.

- [Roadmap](https://example.com/aurora/roadmap)
- [Style guide](https://example.com/aurora/style)
"""

_DEMO_PACKAGE_JSON = """\
{
  "name": "aurora-notes",
  "version": "0.1.0",
  "description": "Calm markdown notebook for product teams.",
  "license": "MIT",
  "homepage": "https://example.com/aurora",
  "scripts": {
    "test": "vitest"
  },
  "devDependencies": {
    "vitest": "^1.6.0",
    "@playwright/test": "^1.45.0",
    "next": "^14.2.0",
    "react": "^18.3.0",
    "prettier": "^3.3.0",
    "eslint": "^9.0.0"
  }
}
"""

_DEMO_EDITORCONFIG = """\
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
indent_style = space
indent_size = 2

[*.py]
indent_size = 4
"""

_DEMO_WORKFLOW = """\
name: ci
on:
  pull_request: {}
  push:
    branches: [main]
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "TODO real CI"
"""

_DEMO_TEST = """\
import { describe, it, expect } from \"vitest\";
describe(\"smoke\", () => { it(\"runs\", () => expect(1 + 1).toBe(2)); });
"""


@router.post("/scaffold-demo-repo", response_model=ScaffoldDemoResponse)
async def scaffold_demo(
    auth: AuthContext = Depends(get_current_auth),
) -> ScaffoldDemoResponse:
    _ = auth
    base = repo_inspector.WORKBENCH_ROOT / f"demo-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    (base / "README.md").write_text(_DEMO_README, encoding="utf-8")
    (base / "package.json").write_text(_DEMO_PACKAGE_JSON, encoding="utf-8")
    (base / ".editorconfig").write_text(_DEMO_EDITORCONFIG, encoding="utf-8")
    (base / ".prettierrc").write_text("{}\n", encoding="utf-8")
    (base / ".github" / "workflows").mkdir(parents=True, exist_ok=True)
    (base / ".github" / "workflows" / "ci.yml").write_text(_DEMO_WORKFLOW, encoding="utf-8")
    (base / "tests").mkdir(parents=True, exist_ok=True)
    (base / "tests" / "smoke.test.ts").write_text(_DEMO_TEST, encoding="utf-8")
    # Initialise as a git repo so the install/seed commits have somewhere to
    # land. We seed an empty initial commit so HEAD always exists.
    import subprocess

    def _git(args: list[str]) -> None:
        subprocess.run(
            ["git", *args], cwd=base, check=True, capture_output=True, timeout=10
        )

    try:
        _git(["init", "--initial-branch=main"])
        _git(["config", "user.email", "ship-onboarding@ship.dev"])
        _git(["config", "user.name", "Ship Onboarding"])
        _git(["add", "."])
        _git(["commit", "-m", "initial demo scaffold"])
    except Exception as exc:  # pragma: no cover
        logger.warning("demo repo git init failed: %s", exc)

    return ScaffoldDemoResponse(path=str(base), suggestion=f"file://{base}")


# ---------------------------------------------------------------------------
# Install workflows + audit
# ---------------------------------------------------------------------------


def _install_to_out(result: workflow_installer.InstallResult) -> InstallResultOut:
    return InstallResultOut(
        repo_path=result.repo_path,
        branch=result.branch,
        head_before=result.head_before,
        head_after=result.head_after,
        commit_made=result.commit_made,
        files=[InstalledFileOut(**dataclasses.asdict(f)) for f in result.files],
        installed=[InstalledWorkflowOut(**a) for a in result.installed],
        skipped=[InstallSkippedOut(**s) for s in result.skipped],
    )


@router.post("/install-workflows", response_model=InstallResultOut)
async def install_workflows_route(
    payload: InstallWorkflowsRequest,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> InstallResultOut:
    await _require_membership(session, payload.workspace_id, auth.user.id, ROLES_ADMIN)

    # Materialise the source first so we know the local path; this also
    # validates that the source is reachable before we risk a partial
    # install.
    try:
        path, _kind, _cached = repo_inspector.resolve_source(payload.repo_source)
    except repo_inspector.RepoSourceError as exc:
        raise HTTPException(
            status_code=400 if exc.code == "not_found" else 422,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    try:
        result = workflow_installer.install_workflows(
            repo_path=path,
            workflow_ids=payload.workflow_ids,
            actor=auth.user.email,
        )
    except workflow_installer.InstallerError as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": exc.message}
        ) from exc

    # Now that the user has actually committed artifacts into this repo,
    # register it as the workspace's `project` artifact-repo so the
    # resolver can read .ship/artifacts/ back. We dedupe on (workspace, url)
    # — the table has no unique constraint, so a re-run of the wizard
    # against the same repo would otherwise create a duplicate row.
    repo_registered = False
    existing_stmt = (
        select(ArtifactRepo)
        .where(ArtifactRepo.workspace_id == payload.workspace_id)
        .where(ArtifactRepo.url == payload.repo_source)
    )
    existing = (await session.execute(existing_stmt)).scalars().first()
    if existing is None:
        repo_row = ArtifactRepo(
            workspace_id=payload.workspace_id,
            kind="project",
            url=payload.repo_source,
            default_branch=result.branch or "main",
        )
        # file:// repos are read inline; remote URLs need the (still-pending)
        # sync worker. Surface that here so the settings page can flag it.
        if not payload.repo_source.startswith("file://"):
            repo_row.last_sync_error = (
                "git sync worker not yet implemented; project artifacts "
                "will appear once the worker lands"
            )
        session.add(repo_row)
        repo_registered = True

    session.add(
        AuditLog(
            workspace_id=payload.workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="onboarding.install_workflows",
            target_kind="repo",
            target_id=str(path),
            payload={
                "repo_source": payload.repo_source,
                "installed_ids": [a["id"] for a in result.installed],
                "skipped_ids": [s["id"] for s in result.skipped],
                "commit_made": result.commit_made,
                "head_after": result.head_after,
                "artifact_repo_registered": repo_registered,
            },
        )
    )
    await session.flush()
    return _install_to_out(result)


# ---------------------------------------------------------------------------
# Seed knowledge
# ---------------------------------------------------------------------------


def _seed_to_out(result: knowledge_seeder.SeedResult) -> SeedResultOut:
    return SeedResultOut(
        repo_path=result.repo_path,
        branch=result.branch,
        head_before=result.head_before,
        head_after=result.head_after,
        commit_made=result.commit_made,
        docs=[SeededDocOut(**dataclasses.asdict(d)) for d in result.docs],
    )


@router.post("/seed-knowledge", response_model=SeedResultOut)
async def seed_knowledge_route(
    payload: SeedKnowledgeRequest,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
) -> SeedResultOut:
    await _require_membership(session, payload.workspace_id, auth.user.id, ROLES_ADMIN)

    try:
        profile = repo_inspector.inspect(payload.repo_source)
    except repo_inspector.RepoSourceError as exc:
        raise HTTPException(
            status_code=400 if exc.code == "not_found" else 422,
            detail={"code": exc.code, "message": exc.message},
        ) from exc

    try:
        result = knowledge_seeder.seed(
            profile=profile,
            bucket_slugs=payload.bucket_slugs,
            actor=auth.user.email,
        )
    except knowledge_seeder.SeederError as exc:
        raise HTTPException(
            status_code=400, detail={"code": exc.code, "message": exc.message}
        ) from exc

    session.add(
        AuditLog(
            workspace_id=payload.workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="onboarding.seed_knowledge",
            target_kind="repo",
            target_id=str(Path(result.repo_path)),
            payload={
                "repo_source": payload.repo_source,
                "doc_slugs": [d.slug for d in result.docs],
                "commit_made": result.commit_made,
                "head_after": result.head_after,
            },
        )
    )
    await session.flush()
    return _seed_to_out(result)
