"""Per-repo intel harvest service (Wave 8a P5-03).

The onboarding wizard dispatches one harvest per newly activated repo
so future agent runs can read a structured snapshot of the project
(languages, frameworks, entry points, commit style, …) without
re-scanning the tree on every invocation.

Pipeline shape
--------------

1. **List files.** One ``CodeHostGateway.list_files`` call returns
   the flat path list at HEAD on the default branch.
2. **Detect languages.** Pure extension heuristic. No tree-sitter, no
   ``github-linguist`` — operators get a "good enough" mix that
   answers "is this mostly Python?" without dragging in C deps.
3. **Detect frameworks.** Read manifest files (``package.json``,
   ``requirements.txt``, ``pyproject.toml``, ``Gemfile``,
   ``go.mod``, ``Cargo.toml``) and look for well-known names.
4. **Identify entry points.** A small allow-list of paths the agent
   most often wants to find first (``main.py``, ``src/index.ts*``,
   App-Router ``page.tsx``, Pages-Router ``_app.tsx``,
   ``Dockerfile``, GitHub Actions workflows).
5. **Structure summary.** Top-level dirs (capped 30) + depth p50 /
   p95 + total file count.
6. **Commit style.** Sample the last ~200 commits from the default
   branch via the REST commits API and compute conventional vs.
   freeform based on subject prefixes.
7. **Visual tokens.** Best-effort; missing files are not failures.
   Currently parses ``tailwind.config.*`` / ``tokens.css`` /
   ``theme.json`` for a primary colour + font family.
8. **Persist.** Insert a fresh ``repo_intel`` row with
   ``version = max(prev.version) + 1`` and demote the previous
   ``is_current`` row to ``FALSE``.
9. **Project to knowledge bucket.** Write a human-readable
   ``.ship/knowledge/repo-intel.md`` article into the existing
   ``knowledge_buckets`` plumbing so agents reading the bucket see a
   markdown summary, not just the JSONB row.

What this module deliberately does NOT do
-----------------------------------------

- It does not embed the markdown summary. The KB indexer picks it
  up on its next run.
- It does not crawl arbitrary deep heuristics — every step is a
  cheap, deterministic pass over already-fetched bytes.
- It does not raise on harvest failures (network, parse). Per-step
  errors are swallowed into the report; an outer failure is
  recorded on the row via ``harvest_error`` so retries are honest
  about prior attempts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.agent_memory import (
    BucketArticle,
    BucketArticleSource,
    BucketArticleStatus,
    BucketScope,
    BucketSource,
    KnowledgeBucket,
    KnowledgeImportSource,
    KnowledgeImportSourceKind,
    KnowledgeIngestionRun,
    KnowledgeIngestionStatus,
    KnowledgeSourceItem,
    KnowledgeSourceStatus,
)
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.repo_intel import RepoIntel, RepoIntelTriggeredBy
from backend.app.integrations.gateway.code_host import (
    BlobContent,
    CodeHostGateway,
    RepoRef,
)
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


# Caller-injectable hook so tests can stub the GitHub commits API
# without monkey-patching httpx. Each item is a dict with at least
# ``{"sha": str, "message": str}``; extra keys are ignored.
CommitFetcher = Callable[
    [RepoRef, str | None, int],
    Awaitable[list[dict[str, Any]]],
]


@dataclass(frozen=True, slots=True)
class HarvestReport:
    """Caller-visible summary of one ``harvest_repo_intel`` invocation.

    All counts are best-effort. ``files_examined`` reflects the size
    of the tree we actually walked (post-cap), not the number of
    files in the repo.
    """

    intel_id: uuid.UUID
    version: int
    duration_ms: int
    files_examined: int
    languages_detected: int
    knowledge_articles_written: int
    project_type: str | None = None


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------


# Defensive caps so a runaway monorepo can't wedge the harvest. The
# numbers are calibrated to be comfortably above a typical repo
# (Linux kernel-shaped repos are explicitly out of scope for the
# wizard) without letting one tenant burn the worker's wall-clock.
_MAX_FILES: int = 5_000
_MAX_BLOB_BYTES: int = 256 * 1024
_TOP_LEVEL_DIRS_CAP: int = 30
_COMMIT_SAMPLE_SIZE: int = 200

# Conventional-commit detection: if more than this fraction of sampled
# subjects match ``<type>(<scope>)?(!)?: ``, declare the repo
# conventional. Tuned to match real-world repos that have a small
# legacy tail of pre-convention commits.
_CONVENTIONAL_THRESHOLD: float = 0.5

# Bucket / article slugs the project surfaces under.
_INTEL_ARTICLE_PATH: str = ".ship/knowledge/repo-intel.md"
_HARVESTER_VERSION: int = 2


def _slugify_repo(full_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", full_name.lower()).strip("-")
    return slug or "repository"


def _fingerprint_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Static lookup tables
# ---------------------------------------------------------------------------


# Extension → canonical language. Only "code" extensions are weighted
# in the language ratio so configuration files (yaml, json) and
# documentation (md) don't dominate small projects. Add
# advisedly — every key here ships with downstream consumers.
_LANG_BY_EXT: dict[str, str] = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
    ".pyi": "python",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".scala": "scala",
    ".php": "php",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".lua": "lua",
    ".dart": "dart",
}


# Manifest file → ``(canonical-framework, package-manager)``.
# Multiple manifests can contribute to the same project (e.g. a
# Python+JS monorepo), so we union per-source results.
_PYTHON_FRAMEWORKS: dict[str, str] = {
    "fastapi": "fastapi",
    "django": "django",
    "flask": "flask",
    "starlette": "starlette",
    "tornado": "tornado",
    "pyramid": "pyramid",
    "sanic": "sanic",
    "litestar": "litestar",
}

_JS_FRAMEWORKS: dict[str, str] = {
    "next": "next.js",
    "react": "react",
    "vue": "vue",
    "nuxt": "nuxt",
    "svelte": "svelte",
    "remix": "remix",
    "express": "express",
    "@nestjs/core": "nestjs",
    "vite": "vite",
    "tailwindcss": "tailwindcss",
    "astro": "astro",
    # Mobile / desktop shells — feed the BS0.2 project-type classifier.
    # ``react-native`` always ships alongside ``react``; the classifier
    # checks the mobile set first so the combo resolves to mobile.
    "react-native": "react-native",
    "expo": "expo",
    "electron": "electron",
    "@tauri-apps/api": "tauri",
    "@tauri-apps/cli": "tauri",
}

_RUBY_FRAMEWORKS: dict[str, str] = {
    "rails": "rails",
    "sinatra": "sinatra",
}

_GO_FRAMEWORKS: dict[str, str] = {
    "github.com/gin-gonic/gin": "gin",
    "github.com/labstack/echo": "echo",
    "github.com/gofiber/fiber": "fiber",
    "github.com/go-chi/chi": "chi",
}

_RUST_FRAMEWORKS: dict[str, str] = {
    "actix-web": "actix-web",
    "rocket": "rocket",
    "axum": "axum",
    "warp": "warp",
}

_CONVENTIONAL_TYPES: tuple[str, ...] = (
    "feat",
    "fix",
    "chore",
    "docs",
    "refactor",
    "test",
    "ci",
    "perf",
    "build",
    "style",
    "revert",
)
_CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\([^)]+\))?(?:!)?:\s+\S",
)


# ---------------------------------------------------------------------------
# harvest_repo_intel — top-level orchestration
# ---------------------------------------------------------------------------


async def harvest_repo_intel(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    triggered_by: str = RepoIntelTriggeredBy.WIZARD,
    settings: Settings | None = None,
    gateway: CodeHostGateway | None = None,
    commit_fetcher: CommitFetcher | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> HarvestReport:
    """Run a full intel harvest synchronously and persist the result.

    Looks up the repo + installation, runs the harvest pipeline, marks
    any prior ``is_current=TRUE`` row as superseded, inserts the fresh
    row, and writes the markdown projection into the
    ``knowledge_buckets`` system.

    Per-step errors are swallowed into the report. An outer failure
    (network, auth) is logged on the row in ``harvest_error`` so a
    later observer can tell "the heuristic produced empty data" apart
    from "we never got off the ground".
    """
    started_at = time.monotonic()

    repo = await session.get(WorkspaceRepo, repo_id)
    if repo is None or repo.workspace_id != workspace_id:
        raise ValueError(
            f"WorkspaceRepo {repo_id} not found for workspace {workspace_id}"
        )

    s = settings or get_settings()
    install = await _load_install(session, repo)
    gw = gateway or (
        GitHubCodeHost(install.installation_id, settings=s, client=http_client)
        if install is not None
        else None
    )
    if gw is None:
        raise ValueError(
            f"WorkspaceRepo {repo_id} has no installation; cannot harvest"
        )
    fetch_commits = commit_fetcher or _default_commit_fetcher(
        install=install,
        settings=s,
        client=http_client,
    )

    owner, name = _owner_repo(repo)
    ref = RepoRef(kind="github", owner=owner, repo=name)

    languages: dict[str, float] = {}
    frameworks: list[str] = []
    package_managers: list[str] = []
    entry_points: list[dict[str, Any]] = []
    structure: dict[str, Any] = {}
    commit_style: dict[str, Any] = {}
    visual_tokens: dict[str, Any] = {}
    project_type: str | None = None
    sdlc_maturity: dict[str, Any] = {}
    files_examined: int = 0
    error_text: str | None = None

    try:
        all_paths = await gw.list_files(
            ref, ref_sha=repo.default_branch or None
        )
        if len(all_paths) > _MAX_FILES:
            logger.warning(
                "repo %s has %d paths, intel harvest capped at %d",
                repo.full_name,
                len(all_paths),
                _MAX_FILES,
            )
            all_paths = all_paths[:_MAX_FILES]
        files_examined = len(all_paths)

        languages = _detect_languages(all_paths)
        package_managers = _detect_package_managers(all_paths)
        entry_points = _detect_entry_points(all_paths)
        structure = _summarise_structure(all_paths)

        frameworks = await _detect_frameworks(
            gw, ref=ref, repo=repo, paths=all_paths
        )
        commit_style = await _detect_commit_style(
            ref=ref, branch=repo.default_branch or None, fetcher=fetch_commits
        )
        visual_tokens = await _detect_visual_tokens(
            gw, ref=ref, repo=repo, paths=all_paths
        )

        # BS0.2 — classify project type + delivery maturity from the
        # signals gathered above. Pure + deterministic; never raises.
        project_type, _pt_conf, _pt_signals = _classify_project(
            frameworks=frameworks,
            entry_points=entry_points,
            paths=all_paths,
        )
        sdlc_maturity = _assess_sdlc_maturity(paths=all_paths)
        sdlc_maturity["project_type_confidence"] = _pt_conf
        sdlc_maturity["project_type_signals"] = _pt_signals
    except Exception as exc:  # noqa: BLE001 — recorded, not raised
        logger.exception(
            "repo_intel harvest failed for repo=%s", repo_id
        )
        error_text = f"{type(exc).__name__}: {exc}"

    duration_ms = int((time.monotonic() - started_at) * 1000)

    next_version = await _next_version(session, repo_id=repo_id)
    await _demote_current(session, repo_id=repo_id)

    row = RepoIntel(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        repo_id=repo_id,
        version=next_version,
        is_current=True,
        languages=languages,
        frameworks=frameworks,
        package_managers=package_managers,
        entry_points=entry_points,
        structure=structure,
        commit_style=commit_style,
        visual_tokens=visual_tokens,
        project_type=project_type,
        sdlc_maturity=sdlc_maturity,
        harvested_by=triggered_by,
        harvest_duration_ms=duration_ms,
        harvest_error=error_text,
    )
    session.add(row)
    await session.flush()

    articles_written = 0
    if error_text is None:
        # K8: ship the repo intel to Lighthouse only. The internal
        # bucket projection (_write_intel_markdown) is retired now that
        # reads are Lighthouse-only; it's removed with the internal
        # knowledge tables in the K8 purge.
        await _emit_intel_to_lighthouse(repo=repo, intel=row)

    await session.flush()

    return HarvestReport(
        intel_id=row.id,
        version=next_version,
        duration_ms=duration_ms,
        files_examined=files_examined,
        languages_detected=len(languages),
        knowledge_articles_written=articles_written,
        project_type=project_type,
    )


async def get_current_intel(
    session: AsyncSession, repo_id: uuid.UUID
) -> RepoIntel | None:
    """Return the live :class:`RepoIntel` row for ``repo_id`` or ``None``.

    "Live" means ``is_current=TRUE``. Callers that need history can
    query the table directly with an ``ORDER BY version DESC`` —
    intentionally not exposed here so the resolver path stays the
    obvious "latest snapshot" one-liner.
    """
    return (
        await session.execute(
            select(RepoIntel).where(
                RepoIntel.repo_id == repo_id,
                RepoIntel.is_current.is_(True),
            )
        )
    ).scalars().first()


async def enqueue_harvest(
    redis_pool: Any,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    triggered_by: str = RepoIntelTriggeredBy.WIZARD,
) -> None:
    """Schedule a harvest, falling back to inline execution in dev.

    When ``redis_pool`` is non-None we hand the job off to arq via
    ``enqueue_job("harvest_repo_intel_job", ...)``. The worker picks
    it up out-of-process so the request that triggered it returns
    promptly.

    When ``redis_pool`` is ``None`` (no worker configured in this
    deploy — typical of local dev where the operator skipped the
    ``--profile worker`` opt-in) we instead run the harvest INLINE
    via ``asyncio.create_task``. The task opens its own session
    against the configured engine and commits when done; the caller
    does not block on it. Errors land in the worker logger same as
    the queued path.
    """
    job_id = f"harvest:{repo_id}:{int(time.time())}"

    if redis_pool is not None:
        await redis_pool.enqueue_job(
            "harvest_repo_intel_job",
            str(workspace_id),
            str(repo_id),
            triggered_by,
            _job_id=job_id,
        )
        return

    # Inline fallback. We deliberately don't await — the caller (a
    # request handler) shouldn't block on a multi-second harvest.
    asyncio.create_task(
        _inline_harvest(workspace_id, repo_id, triggered_by),
        name=job_id,
    )


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


async def _next_version(
    session: AsyncSession, *, repo_id: uuid.UUID
) -> int:
    max_prev = (
        await session.execute(
            select(func.max(RepoIntel.version)).where(
                RepoIntel.repo_id == repo_id
            )
        )
    ).scalar()
    return (int(max_prev) if max_prev is not None else 0) + 1


async def _demote_current(
    session: AsyncSession, *, repo_id: uuid.UUID
) -> None:
    """Flip the existing live row (if any) to ``is_current=FALSE``.

    Done as a single ``UPDATE`` rather than load-then-mutate so the
    partial unique index ``idx_repo_intel_repo_current`` never sees a
    transient two-current-rows state inside the same flush.
    """
    await session.execute(
        update(RepoIntel)
        .where(
            and_(
                RepoIntel.repo_id == repo_id,
                RepoIntel.is_current.is_(True),
            )
        )
        .values(is_current=False)
    )


async def _load_install(
    session: AsyncSession, repo: WorkspaceRepo
) -> GitHubInstallation | None:
    if repo.installation_id is None:
        return None
    return await session.get(GitHubInstallation, repo.installation_id)


def _owner_repo(repo: WorkspaceRepo) -> tuple[str, str]:
    owner, _, name = (repo.full_name or "").partition("/")
    if not owner or not name:
        raise ValueError(
            f"WorkspaceRepo.full_name {repo.full_name!r} is not owner/repo"
        )
    return owner, name


# ---------------------------------------------------------------------------
# Step 2 — language detection
# ---------------------------------------------------------------------------


def _detect_languages(paths: list[str]) -> dict[str, float]:
    """Return ``{language: ratio}`` summed to ~1.0 across code files.

    Pure extension heuristic — see the module docstring for why we're
    not pulling in ``github-linguist``. Files with extensions outside
    :data:`_LANG_BY_EXT` are ignored. If no code files are found we
    return an empty dict rather than zero-everywhere.
    """
    counts: dict[str, int] = {}
    for path in paths:
        ext = _ext(path)
        lang = _LANG_BY_EXT.get(ext)
        if lang is None:
            continue
        counts[lang] = counts.get(lang, 0) + 1
    total = sum(counts.values())
    if total == 0:
        return {}
    return {
        lang: round(count / total, 4)
        for lang, count in sorted(
            counts.items(), key=lambda kv: kv[1], reverse=True
        )
    }


def _ext(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    if "." not in base:
        return ""
    return "." + base.rsplit(".", 1)[-1].lower()


# ---------------------------------------------------------------------------
# Step 3 — framework detection
# ---------------------------------------------------------------------------


async def _detect_frameworks(
    gw: CodeHostGateway,
    *,
    ref: RepoRef,
    repo: WorkspaceRepo,
    paths: list[str],
) -> list[str]:
    """Read manifest files and return the canonical framework names found.

    Each manifest is best-effort: a parse failure on one manifest
    doesn't kill the others. Duplicate framework names are
    de-duplicated; the order reflects discovery order so the caller
    can show "primary first" without a separate ranking pass.
    """
    found: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        found.append(name)

    paths_set = set(paths)

    # JavaScript / TypeScript via package.json (root only — nested
    # workspace package.json parsing is out of scope for the harvest).
    if "package.json" in paths_set:
        body = await _read_text(gw, ref=ref, repo=repo, path="package.json")
        if body is not None:
            for name in _frameworks_from_package_json(body):
                _add(name)

    # Python via pyproject.toml + requirements.txt.
    if "pyproject.toml" in paths_set:
        body = await _read_text(
            gw, ref=ref, repo=repo, path="pyproject.toml"
        )
        if body is not None:
            for name in _frameworks_from_pyproject(body):
                _add(name)
    if "requirements.txt" in paths_set:
        body = await _read_text(
            gw, ref=ref, repo=repo, path="requirements.txt"
        )
        if body is not None:
            for name in _frameworks_from_requirements(body):
                _add(name)

    # Ruby via Gemfile.
    if "Gemfile" in paths_set:
        body = await _read_text(gw, ref=ref, repo=repo, path="Gemfile")
        if body is not None:
            for name in _frameworks_from_gemfile(body):
                _add(name)

    # Go via go.mod.
    if "go.mod" in paths_set:
        body = await _read_text(gw, ref=ref, repo=repo, path="go.mod")
        if body is not None:
            for name in _frameworks_from_go_mod(body):
                _add(name)

    # Rust via Cargo.toml.
    if "Cargo.toml" in paths_set:
        body = await _read_text(gw, ref=ref, repo=repo, path="Cargo.toml")
        if body is not None:
            for name in _frameworks_from_cargo(body):
                _add(name)

    return found


def _frameworks_from_package_json(body: str) -> list[str]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    deps: dict[str, Any] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            deps.update(section)
    out: list[str] = []
    for dep_name in deps:
        canonical = _JS_FRAMEWORKS.get(dep_name)
        if canonical is not None:
            out.append(canonical)
    return out


def _frameworks_from_pyproject(body: str) -> list[str]:
    """Match Python framework names against ``[project] dependencies``.

    We scan the raw text rather than parsing TOML to avoid pulling in
    ``tomllib`` import-time edge cases on older interpreters and to
    stay forgiving of malformed manifests. The match is "framework
    name appears as a token" — false positives are acceptable; this
    drives the agent's framing, not auth.
    """
    return _scan_for_known(body, _PYTHON_FRAMEWORKS)


def _frameworks_from_requirements(body: str) -> list[str]:
    out: list[str] = []
    for raw in body.splitlines():
        line = raw.strip().split("#", 1)[0].strip()
        if not line:
            continue
        # Strip extras / version specifiers ("fastapi[all]>=0.110").
        token = re.split(r"[\s\[<>=!~;]", line, maxsplit=1)[0].lower()
        canonical = _PYTHON_FRAMEWORKS.get(token)
        if canonical is not None and canonical not in out:
            out.append(canonical)
    return out


def _frameworks_from_gemfile(body: str) -> list[str]:
    return _scan_for_known(body, _RUBY_FRAMEWORKS)


def _frameworks_from_go_mod(body: str) -> list[str]:
    return _scan_for_known(body, _GO_FRAMEWORKS)


def _frameworks_from_cargo(body: str) -> list[str]:
    return _scan_for_known(body, _RUST_FRAMEWORKS)


def _scan_for_known(body: str, table: dict[str, str]) -> list[str]:
    """Return canonical names from ``table`` whose key appears in ``body``.

    Match is substring-based against a lowercased copy. Order
    follows ``table.values()`` for determinism, with duplicates
    suppressed.
    """
    needle = body.lower()
    out: list[str] = []
    seen: set[str] = set()
    for raw, canonical in table.items():
        if raw.lower() in needle and canonical not in seen:
            seen.add(canonical)
            out.append(canonical)
    return out


# ---------------------------------------------------------------------------
# Step 4 — entry-point detection
# ---------------------------------------------------------------------------


def _detect_entry_points(paths: list[str]) -> list[dict[str, Any]]:
    """Return a small ordered list of "what would I open first" paths.

    Order matters: agents rendering this list want the most diagnostic
    entry first. App-Router pages beat Pages-Router fallbacks, server
    entrypoints beat infra, infra beats CI definitions.
    """
    paths_set = set(paths)
    out: list[dict[str, Any]] = []

    def _push(path: str, kind: str) -> None:
        out.append({"path": path, "kind": kind})

    # Frontend entries — App Router wins over Pages Router by design.
    for candidate in (
        "app/page.tsx",
        "app/page.jsx",
        "src/app/page.tsx",
        "console/src/app/page.tsx",
    ):
        if candidate in paths_set:
            _push(candidate, "page")
            break
    if not any(e["kind"] == "page" for e in out):
        for candidate in ("pages/_app.tsx", "pages/_app.jsx", "src/pages/_app.tsx"):
            if candidate in paths_set:
                _push(candidate, "page")
                break

    # Library / app entry points (TS/JS, Python).
    for candidate in (
        "src/index.tsx",
        "src/index.ts",
        "src/index.jsx",
        "src/index.js",
        "index.ts",
        "index.js",
    ):
        if candidate in paths_set:
            _push(candidate, "module")
            break

    if "main.py" in paths_set:
        _push("main.py", "module")
    elif "app.py" in paths_set:
        _push("app.py", "module")

    if "Dockerfile" in paths_set:
        _push("Dockerfile", "container")

    workflow_paths = sorted(
        p for p in paths_set
        if p.startswith(".github/workflows/") and (
            p.endswith(".yml") or p.endswith(".yaml")
        )
    )
    for wf in workflow_paths[:5]:
        _push(wf, "workflow")

    return out


# ---------------------------------------------------------------------------
# Step 5 — structure summary
# ---------------------------------------------------------------------------


def _summarise_structure(paths: list[str]) -> dict[str, Any]:
    """Return ``{top_level_dirs, depth_p50, depth_p95, file_count}``."""
    top_level: list[str] = []
    seen_top: set[str] = set()
    depths: list[int] = []
    for path in paths:
        parts = path.split("/")
        if len(parts) >= 2 and parts[0] not in seen_top:
            seen_top.add(parts[0])
            top_level.append(parts[0])
        depths.append(max(0, len(parts) - 1))

    top_level.sort()
    if len(top_level) > _TOP_LEVEL_DIRS_CAP:
        top_level = top_level[:_TOP_LEVEL_DIRS_CAP]

    return {
        "top_level_dirs": top_level,
        "depth_p50": _percentile(depths, 0.5),
        "depth_p95": _percentile(depths, 0.95),
        "file_count": len(paths),
    }


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = int(round(q * (len(ordered) - 1)))
    return ordered[max(0, min(idx, len(ordered) - 1))]


# ---------------------------------------------------------------------------
# Step 5b — project-type + SDLC-maturity classification (BS0.2)
# ---------------------------------------------------------------------------
#
# Both passes are pure functions over already-harvested signals
# (frameworks + entry points + the flat path list). They never hit the
# network and never raise — an empty/garbage input yields ``unknown`` /
# all-false flags rather than blowing up the harvest. The output feeds
# the bootstrap readiness assessor (BS1) and recommendation card (BS2).


# Canonical-framework → project-type category. Checked most-specific
# first (mobile / desktop shells before web), because a React Native app
# also declares ``react`` and an Electron app also declares its web
# framework — the narrower signal must win.
_MOBILE_FRAMEWORKS: frozenset[str] = frozenset({"react-native", "expo"})
_DESKTOP_FRAMEWORKS: frozenset[str] = frozenset({"electron", "tauri"})
_WEB_FRAMEWORKS: frozenset[str] = frozenset(
    {"next.js", "nuxt", "remix", "astro", "svelte", "vue", "react"}
)
_BACKEND_FRAMEWORKS: frozenset[str] = frozenset(
    {
        "fastapi",
        "django",
        "flask",
        "starlette",
        "tornado",
        "pyramid",
        "sanic",
        "litestar",
        "express",
        "nestjs",
        "gin",
        "echo",
        "fiber",
        "chi",
        "rails",
        "sinatra",
        "actix-web",
        "rocket",
        "axum",
        "warp",
    }
)


def _classify_project(
    *,
    frameworks: list[str],
    entry_points: list[dict[str, Any]],
    paths: list[str],
) -> tuple[str, str, list[str]]:
    """Return ``(project_type, confidence, signals)``.

    ``project_type`` is one of ``web`` / ``mobile`` / ``desktop`` /
    ``backend`` / ``library`` / ``unknown``. ``confidence`` is ``high``
    for a framework-backed match, ``medium`` for a path-marker-only
    match, ``low`` for the library fallback, ``unknown`` for no signal.
    ``signals`` is the human-readable evidence list shown on the
    bootstrap card ("framework:next.js", "path:pubspec.yaml").
    """
    fw = set(frameworks)
    paths_set = set(paths)
    top_dirs = {p.split("/", 1)[0] for p in paths_set if "/" in p}
    signals: list[str] = []

    def _has_path(pred: Callable[[str], bool]) -> bool:
        return any(pred(p) for p in paths_set)

    # --- mobile (most specific) -----------------------------------------
    mobile_fw = fw & _MOBILE_FRAMEWORKS
    has_flutter = "pubspec.yaml" in paths_set
    native_pair = {"ios", "android"} <= top_dirs
    if mobile_fw or has_flutter or native_pair:
        if mobile_fw:
            signals.extend(f"framework:{n}" for n in sorted(mobile_fw))
        if has_flutter:
            signals.append("path:pubspec.yaml")
        if native_pair:
            signals.append("dirs:ios+android")
        confidence = "high" if mobile_fw else "medium"
        return "mobile", confidence, signals

    # --- desktop --------------------------------------------------------
    desktop_fw = fw & _DESKTOP_FRAMEWORKS
    has_tauri_conf = _has_path(
        lambda p: p.endswith("tauri.conf.json") or p.startswith("src-tauri/")
    )
    if desktop_fw or has_tauri_conf:
        if desktop_fw:
            signals.extend(f"framework:{n}" for n in sorted(desktop_fw))
        if has_tauri_conf and not desktop_fw:
            signals.append("path:src-tauri")
        confidence = "high" if desktop_fw else "medium"
        return "desktop", confidence, signals

    # --- web ------------------------------------------------------------
    web_fw = fw & _WEB_FRAMEWORKS
    has_page = any(e.get("kind") == "page" for e in entry_points)
    has_index_html = "index.html" in paths_set or _has_path(
        lambda p: p.endswith("/index.html")
    )
    # ``vite`` is a build tool, not a UI framework — it backs both browser
    # apps and publishable libs. Treat it as a web signal only with an app
    # indicator (a routed page or a root ``index.html``); a vite lib has
    # neither and falls through to the library branch below.
    vite_app = "vite" in fw and (has_page or has_index_html)
    if web_fw or vite_app:
        signals.extend(f"framework:{n}" for n in sorted(web_fw))
        if vite_app and "vite" not in web_fw:
            signals.append("framework:vite")
        if has_page:
            signals.append("entry:page")
        # A real UI framework is a high-confidence call; a vite-only app
        # signal is weaker.
        confidence = "high" if web_fw else "medium"
        return "web", confidence, signals

    # --- backend --------------------------------------------------------
    backend_fw = fw & _BACKEND_FRAMEWORKS
    if backend_fw:
        signals.extend(f"framework:{n}" for n in sorted(backend_fw))
        return "backend", "high", signals

    # --- library (fallback): publishable code, no app/server shell ------
    has_module_entry = any(
        e.get("kind") == "module" for e in entry_points
    )
    has_manifest = bool(
        paths_set & {"package.json", "pyproject.toml", "Cargo.toml", "go.mod"}
    )
    if has_module_entry and has_manifest:
        signals.append("entry:module")
        if has_page:
            # Reachable: a routed page with no web framework and no vite
            # app-indicator skips the web branch and lands here.
            signals.append("entry:page")
        return "library", "low", signals

    return "unknown", "unknown", signals


# Env-name extraction patterns. Each maps a path to the environment name
# it implies; the harvester collects the distinct set so a repo with
# ``.env.dev`` + ``.env.prod`` reports ``env_count=2``.
# Capture the first ``.env.<name>`` segment, tolerating trailing dotted
# qualifiers so ``.env.production.local`` still resolves to ``production``
# (and is then kept, while bare ``.env.local`` resolves to ``local`` and
# is dropped by ``_NON_ENV_NAMES``).
_ENV_FILE_RE = re.compile(
    r"(?:^|/)\.env\.(?P<name>[a-z0-9_-]+?)(?:\.[a-z0-9_-]+)*$"
)
_OVERLAY_RE = re.compile(r"(?:^|/)overlays/(?P<name>[a-z0-9_-]+)/")
_HELM_VALUES_RE = re.compile(r"(?:^|/)values-(?P<name>[a-z0-9_-]+)\.ya?ml$")
# ``.env.example`` / ``.env.local`` / templates are not deploy targets.
_NON_ENV_NAMES: frozenset[str] = frozenset(
    {"example", "sample", "template", "local", "dist", "defaults"}
)


def _assess_sdlc_maturity(*, paths: list[str]) -> dict[str, Any]:
    """Derive delivery-maturity flags from the path listing.

    Heuristic and deterministic — every flag is "does a path matching
    this shape exist". Drives the bootstrap readiness assessor, not any
    gate, so false positives are cheap; the assessor re-checks before it
    files a recommendation.
    """
    paths_set = set(paths)

    def _base(p: str) -> str:
        return p.rsplit("/", 1)[-1]

    has_dockerfile = _any(paths_set, lambda p: _base(p).startswith("Dockerfile"))
    has_compose = _any(
        paths_set,
        lambda p: _base(p) in {
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yml",
            "compose.yaml",
        },
    )
    has_ci = _any(
        paths_set,
        lambda p: p.startswith(".github/workflows/")
        and (p.endswith(".yml") or p.endswith(".yaml")),
    )
    has_unit_tests = _any(paths_set, _looks_like_unit_test)
    has_e2e = _any(paths_set, _looks_like_e2e)

    env_names: set[str] = set()
    for p in paths_set:
        for pat in (_ENV_FILE_RE, _OVERLAY_RE, _HELM_VALUES_RE):
            m = pat.search(p)
            if m:
                name = m.group("name").lower()
                if name not in _NON_ENV_NAMES:
                    env_names.add(name)

    # Promotion = a CD pipeline exists AND there's more than one target to
    # promote between. Best-effort: a deploy/release/promote/cd workflow
    # plus ≥2 distinct environments. ``cd`` covers GitOps repos that name
    # the pipeline ``cd.yml`` rather than ``deploy.yml``.
    has_deploy_wf = _any(
        paths_set,
        lambda p: p.startswith(".github/workflows/")
        and any(
            k in _base(p).lower()
            for k in ("deploy", "release", "promote", "cd")
        ),
    )
    has_promotion = has_deploy_wf and len(env_names) >= 2

    return {
        # Bump when flags are added/renamed so the BS1 readiness assessor
        # can tell a partial old-row blob from a current one.
        "schema_version": 1,
        "has_unit_tests": has_unit_tests,
        "has_e2e": has_e2e,
        "has_dockerfile": has_dockerfile,
        "has_compose": has_compose,
        "has_ci": has_ci,
        "env_count": len(env_names),
        "env_names": sorted(env_names),
        "has_promotion": has_promotion,
    }


def _any(paths_set: set[str], pred: Callable[[str], bool]) -> bool:
    return any(pred(p) for p in paths_set)


def _looks_like_unit_test(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    lowered = path.lower()
    if "/e2e/" in lowered or lowered.startswith("e2e/"):
        return False  # counted as e2e, not unit
    if any(seg in lowered for seg in ("/test/", "/tests/", "/__tests__/")):
        return True
    return bool(
        base.startswith("test_")
        and base.endswith(".py")
        or base.endswith("_test.py")
        or base.endswith("_test.go")
        or base.endswith(".test.ts")
        or base.endswith(".test.tsx")
        or base.endswith(".test.js")
        or base.endswith(".spec.ts")
        or base.endswith(".spec.tsx")
        or base.endswith(".spec.js")
        or base.endswith("_spec.rb")
    )


def _looks_like_e2e(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    lowered = path.lower()
    if base.startswith("playwright.config.") or base.startswith("cypress.config."):
        return True
    if base.endswith((".cy.ts", ".cy.tsx", ".cy.js")):
        return True
    return any(
        seg in lowered for seg in ("/e2e/", "cypress/", "/playwright/")
    ) or lowered.startswith("e2e/")


# ---------------------------------------------------------------------------
# Step 6 — commit-style detection
# ---------------------------------------------------------------------------


async def _detect_commit_style(
    *,
    ref: RepoRef,
    branch: str | None,
    fetcher: CommitFetcher,
) -> dict[str, Any]:
    """Sample recent commits and decide conventional-vs-freeform."""
    try:
        commits = await fetcher(ref, branch, _COMMIT_SAMPLE_SIZE)
    except Exception as exc:  # noqa: BLE001 — best-effort step
        logger.warning("commit fetch failed for %s: %s", ref.full_name, exc)
        return {"convention": "unknown", "samples": 0, "error": str(exc)}

    subjects: list[str] = []
    for commit in commits:
        message = commit.get("message") if isinstance(commit, dict) else None
        if not isinstance(message, str):
            continue
        subject = message.splitlines()[0].strip()
        if subject:
            subjects.append(subject)

    if not subjects:
        return {"convention": "unknown", "samples": 0}

    matched_types: dict[str, int] = {}
    matches = 0
    for subject in subjects:
        m = _CONVENTIONAL_RE.match(subject)
        if m is None:
            continue
        prefix = m.group("type").lower()
        if prefix not in _CONVENTIONAL_TYPES:
            continue
        matches += 1
        matched_types[prefix] = matched_types.get(prefix, 0) + 1

    ratio = matches / len(subjects)
    convention = (
        "conventional" if ratio >= _CONVENTIONAL_THRESHOLD else "freeform"
    )
    common_types = [
        t for t, _ in sorted(
            matched_types.items(), key=lambda kv: kv[1], reverse=True
        )
    ][:5]
    avg_subject_len = round(
        sum(len(s) for s in subjects) / len(subjects), 1
    )

    return {
        "convention": convention,
        "common_types": common_types,
        "avg_subject_len": avg_subject_len,
        "samples": len(subjects),
        "match_ratio": round(ratio, 3),
    }


def _default_commit_fetcher(
    *,
    install: GitHubInstallation | None,
    settings: Settings,
    client: httpx.AsyncClient | None,
) -> CommitFetcher:
    """Build a ``CommitFetcher`` backed by GitHub's REST commits API.

    The closure captures the installation token machinery so step 6
    can fan out without juggling adapters. Keeps the public service
    signature untouched for tests, which inject their own fetcher.
    """
    from backend.app.integrations.github.app_auth import (
        GITHUB_API_BASE,
        fetch_installation_token,
    )

    async def _fetch(
        ref: RepoRef, branch: str | None, count: int
    ) -> list[dict[str, Any]]:
        if install is None:
            return []
        token = await fetch_installation_token(
            install.installation_id, settings=settings, client=client
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        params = {"per_page": min(count, 100)}
        if branch:
            params["sha"] = branch
        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
        try:
            response = await http.get(
                f"{GITHUB_API_BASE}"
                f"/repos/{ref.owner}/{ref.repo}/commits",
                headers=headers,
                params=params,
            )
        finally:
            if owns_client:
                await http.aclose()
        response.raise_for_status()
        out: list[dict[str, Any]] = []
        for item in response.json() or []:
            commit = item.get("commit") or {}
            out.append(
                {
                    "sha": item.get("sha"),
                    "message": commit.get("message"),
                }
            )
        return out

    return _fetch


# ---------------------------------------------------------------------------
# Step 7 — package-manager detection
# ---------------------------------------------------------------------------


def _detect_package_managers(paths: list[str]) -> list[str]:
    """Infer package managers from manifest + lockfile presence."""
    out: list[str] = []
    seen: set[str] = set()
    paths_set = set(paths)

    def _add(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        out.append(name)

    if "package.json" in paths_set:
        # Lockfile beats default for the JS ecosystem.
        if "pnpm-lock.yaml" in paths_set:
            _add("pnpm")
        elif "yarn.lock" in paths_set:
            _add("yarn")
        elif "bun.lockb" in paths_set or "bun.lock" in paths_set:
            _add("bun")
        elif "package-lock.json" in paths_set:
            _add("npm")
        else:
            _add("npm")

    if "pyproject.toml" in paths_set:
        if "uv.lock" in paths_set:
            _add("uv")
        elif "poetry.lock" in paths_set:
            _add("poetry")
        else:
            _add("pip")
    elif "requirements.txt" in paths_set:
        _add("pip")

    if "Gemfile" in paths_set:
        _add("bundler")
    if "go.mod" in paths_set:
        _add("go-modules")
    if "Cargo.toml" in paths_set:
        _add("cargo")

    return out


# ---------------------------------------------------------------------------
# Step 7 (bis) — visual tokens (best-effort; frontend repos)
# ---------------------------------------------------------------------------


_TAILWIND_PRIMARY_RE = re.compile(
    r"primary\s*[:=]\s*['\"]?(#[0-9a-fA-F]{3,8})['\"]?"
)
_TAILWIND_FONT_RE = re.compile(
    r"fontFamily\s*[:=]\s*\{[^}]*?(?:sans|primary)[\s:]*\[\s*['\"]([^'\"]+)['\"]"
)
_CSS_PRIMARY_RE = re.compile(
    r"--(?:color-)?primary\s*:\s*(#[0-9a-fA-F]{3,8})"
)
_CSS_FONT_RE = re.compile(
    r"--font(?:-family|-primary)?\s*:\s*([^;]+);"
)


async def _detect_visual_tokens(
    gw: CodeHostGateway,
    *,
    ref: RepoRef,
    repo: WorkspaceRepo,
    paths: list[str],
) -> dict[str, Any]:
    """Best-effort visual-tokens probe. Returns ``{}`` when nothing matches.

    Searches a handful of well-known token sources. Each parse is
    best-effort — a malformed file does not raise.
    """
    paths_set = set(paths)
    candidates = [
        "tailwind.config.ts",
        "tailwind.config.js",
        "tailwind.config.cjs",
        "tailwind.config.mjs",
        "console/tailwind.config.ts",
        "console/tailwind.config.js",
    ]

    out: dict[str, Any] = {}
    for path in candidates:
        if path not in paths_set:
            continue
        body = await _read_text(gw, ref=ref, repo=repo, path=path)
        if body is None:
            continue
        m = _TAILWIND_PRIMARY_RE.search(body)
        if m and "primary_color" not in out:
            out["primary_color"] = m.group(1)
        f = _TAILWIND_FONT_RE.search(body)
        if f and "font_family" not in out:
            out["font_family"] = f.group(1)
        if "primary_color" in out and "font_family" in out:
            return out

    for path in ("tokens.css", "styles/tokens.css", "src/tokens.css"):
        if path not in paths_set:
            continue
        body = await _read_text(gw, ref=ref, repo=repo, path=path)
        if body is None:
            continue
        m = _CSS_PRIMARY_RE.search(body)
        if m and "primary_color" not in out:
            out["primary_color"] = m.group(1)
        f = _CSS_FONT_RE.search(body)
        if f and "font_family" not in out:
            out["font_family"] = f.group(1).strip().split(",", 1)[0].strip(
                "\"' "
            )

    if "theme.json" in paths_set:
        body = await _read_text(gw, ref=ref, repo=repo, path="theme.json")
        if body is not None:
            try:
                data = json.loads(body)
                if isinstance(data, dict):
                    primary = data.get("primary_color") or data.get(
                        "primaryColor"
                    )
                    if isinstance(primary, str) and "primary_color" not in out:
                        out["primary_color"] = primary
                    font = data.get("font_family") or data.get("fontFamily")
                    if isinstance(font, str) and "font_family" not in out:
                        out["font_family"] = font
            except json.JSONDecodeError:
                pass

    return out


# ---------------------------------------------------------------------------
# Step 9 — markdown projection into knowledge_buckets
# ---------------------------------------------------------------------------


async def _write_intel_markdown(
    session: AsyncSession,
    *,
    repo: WorkspaceRepo,
    intel: RepoIntel,
) -> int:
    """Project ``intel`` into workspace buckets via import-source provenance."""

    source = await _get_or_create_intel_import_source(session, repo=repo)
    source.status = KnowledgeSourceStatus.SYNCING
    run = KnowledgeIngestionRun(
        id=uuid.uuid4(),
        workspace_id=repo.workspace_id,
        source_id=source.id,
        status=KnowledgeIngestionStatus.RUNNING,
        trigger="repo_intel_harvest",
        stats={},
        started_at=datetime.now(timezone.utc),
    )
    session.add(run)
    await session.flush()

    articles = _render_repo_context_articles(intel=intel, repo=repo)
    written = 0
    for slug, title, body in articles:
        item = await _upsert_intel_source_item(
            session,
            source=source,
            slug=slug,
            title=title,
            body=body,
            intel=intel,
            repo=repo,
        )
        bucket = await _get_workspace_bucket_for_intel_article(
            session, repo=repo, article_slug=slug
        )
        article_id, changed = await _upsert_context_article(
            session,
            bucket=bucket,
            slug=f"{_slugify_repo(repo.full_name)}-{slug}",
            title=f"{repo.full_name}: {title}",
            body=body,
            intel=intel,
            repo=repo,
            source_item_id=item.id,
        )
        if changed:
            written += 1
            session.add(
                BucketArticleSource(
                    id=uuid.uuid4(),
                    article_id=article_id,
                    source_item_id=item.id,
                    run_id=run.id,
                )
            )

    source.status = KnowledgeSourceStatus.READY
    source.last_synced_at = datetime.now(timezone.utc)
    source.last_error = None
    source.content_fingerprint = _fingerprint_payload(
        {
            "intel_id": str(intel.id),
            "version": intel.version,
            "articles": [(slug, body) for slug, _, body in articles],
        }
    )
    source.sync_cursor = {"intel_id": str(intel.id), "version": intel.version}
    run.status = KnowledgeIngestionStatus.DONE
    run.finished_at = datetime.now(timezone.utc)
    run.stats = {
        "discovered": len(articles),
        "changed": written,
        "skipped": len(articles) - written,
        "articles_created_or_updated": written,
    }
    return written


async def _get_or_create_intel_import_source(
    session: AsyncSession, *, repo: WorkspaceRepo
) -> KnowledgeImportSource:
    existing = (
        await session.execute(
            select(KnowledgeImportSource).where(
                KnowledgeImportSource.workspace_id == repo.workspace_id,
                KnowledgeImportSource.repo_id == repo.id,
                KnowledgeImportSource.kind == KnowledgeImportSourceKind.DOCS_REPO,
                KnowledgeImportSource.name == f"Repository context: {repo.full_name}",
                KnowledgeImportSource.archived_at.is_(None),
            )
        )
    ).scalars().first()
    config = {
        "repo_id": str(repo.id),
        "repo_full_name": repo.full_name,
        "branch": repo.default_branch or "main",
        "harvester": "repo_intel",
        "harvester_version": _HARVESTER_VERSION,
    }
    if existing is not None:
        existing.name = f"Repository context: {repo.full_name}"
        existing.config = config
        return existing

    source = KnowledgeImportSource(
        id=uuid.uuid4(),
        workspace_id=repo.workspace_id,
        repo_id=repo.id,
        kind=KnowledgeImportSourceKind.DOCS_REPO,
        name=f"Repository context: {repo.full_name}",
        config=config,
        sync_interval_minutes=None,
    )
    session.add(source)
    await session.flush()
    return source


async def _upsert_intel_source_item(
    session: AsyncSession,
    *,
    source: KnowledgeImportSource,
    slug: str,
    title: str,
    body: str,
    intel: RepoIntel,
    repo: WorkspaceRepo,
) -> KnowledgeSourceItem:
    external_id = f"{repo.id}:{slug}"
    content_fingerprint = hashlib.sha256(body.encode("utf-8")).hexdigest()
    existing = (
        await session.execute(
            select(KnowledgeSourceItem).where(
                KnowledgeSourceItem.source_id == source.id,
                KnowledgeSourceItem.external_id == external_id,
            )
        )
    ).scalars().first()
    item_ref = {
        "repo_id": str(repo.id),
        "repo_full_name": repo.full_name,
        "intel_id": str(intel.id),
        "intel_version": intel.version,
        "article_slug": slug,
        "harvester_version": _HARVESTER_VERSION,
    }
    if existing is not None:
        existing.title = title
        existing.item_ref = item_ref
        existing.content_fingerprint = content_fingerprint
        existing.cursor = {"intel_id": str(intel.id), "version": intel.version}
        existing.last_seen_at = datetime.now(timezone.utc)
        existing.deleted_at = None
        return existing

    item = KnowledgeSourceItem(
        id=uuid.uuid4(),
        workspace_id=repo.workspace_id,
        source_id=source.id,
        external_id=external_id,
        title=title,
        external_url=repo.html_url,
        item_ref=item_ref,
        content_fingerprint=content_fingerprint,
        cursor={"intel_id": str(intel.id), "version": intel.version},
        last_seen_at=datetime.now(timezone.utc),
    )
    session.add(item)
    await session.flush()
    return item


async def _get_workspace_bucket_for_intel_article(
    session: AsyncSession, *, repo: WorkspaceRepo, article_slug: str
) -> KnowledgeBucket:
    workspace_id = repo.workspace_id
    # The 10 → 6 consolidation retired ``project-map`` and
    # ``source-intelligence``; repo overview / generic source-intel rows
    # fall back to ``product-knowledge`` (closest semantic neighbour for
    # "what is this repo / what's in it"). The mapping otherwise stays
    # tied to the per-section author intent.
    preferred = {
        "overview": "product-knowledge",
        "dev-environment": "engineering-standards",
        "architecture": "architecture-decisions",
        "workflow": "runbooks-operations",
        "visual-style": "product-knowledge",
        "rules": "security-access",
    }.get(article_slug, "product-knowledge")
    bucket = (
        await session.execute(
            select(KnowledgeBucket).where(
                KnowledgeBucket.workspace_id == workspace_id,
                KnowledgeBucket.scope_kind == BucketScope.WORKSPACE,
                KnowledgeBucket.source_kind != BucketSource.REPO_FILES,
                KnowledgeBucket.archived_at.is_(None),
                KnowledgeBucket.slug == preferred,
            )
        )
    ).scalars().first()
    if bucket is not None:
        return bucket

    fallback = (
        await session.execute(
            select(KnowledgeBucket)
            .where(
                KnowledgeBucket.workspace_id == workspace_id,
                KnowledgeBucket.scope_kind == BucketScope.WORKSPACE,
                KnowledgeBucket.source_kind != BucketSource.REPO_FILES,
                KnowledgeBucket.archived_at.is_(None),
            )
            .order_by(KnowledgeBucket.slug)
        )
    ).scalars().first()
    if fallback is not None:
        return fallback

    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.REPO_CONTEXT,
        slug="product-knowledge",
        name="Product Knowledge",
        description="Product behavior, customer-facing concepts, roadmap context, UX decisions, and the data/domain vocabulary the team shares.",
        source_ref={
            "repo_id": str(repo.id),
            "repo_full_name": repo.full_name,
            "generated_by": "repo_intel",
        },
    )
    session.add(bucket)
    await session.flush()
    return bucket


async def _upsert_context_article(
    session: AsyncSession,
    *,
    bucket: KnowledgeBucket,
    slug: str,
    title: str,
    body: str,
    intel: RepoIntel,
    repo: WorkspaceRepo,
    source_item_id: uuid.UUID,
) -> tuple[uuid.UUID, bool]:
    content_sha = hashlib.sha256(body.encode("utf-8")).hexdigest()

    current = (
        await session.execute(
            select(BucketArticle).where(
                BucketArticle.bucket_id == bucket.id,
                BucketArticle.slug == slug,
                BucketArticle.status == BucketArticleStatus.PUBLISHED,
            )
        )
    ).scalars().first()

    if current is not None and current.content_sha == content_sha:
        return current.id, False

    if current is not None:
        current.status = BucketArticleStatus.SUPERSEDED
        next_version = current.version + 1
        supersedes_id: uuid.UUID | None = current.id
    else:
        supersedes_id = None
        max_prev = (
            await session.execute(
                select(func.max(BucketArticle.version)).where(
                    BucketArticle.bucket_id == bucket.id,
                    BucketArticle.slug == slug,
                )
            )
        ).scalar()
        next_version = (int(max_prev) if max_prev is not None else 0) + 1

    article = BucketArticle(
        id=uuid.uuid4(),
        bucket_id=bucket.id,
        slug=slug,
        title=title,
        body_md=body,
        content_sha=content_sha,
        version=next_version,
        status=BucketArticleStatus.PUBLISHED,
        supersedes_id=supersedes_id,
        provenance={
            "source_kind": BucketSource.REPO_CONTEXT,
            "source": "repo_context",
            "intel_id": str(intel.id),
            "intel_version": intel.version,
            "article_version": next_version,
            "repo_id": str(repo.id),
            "repo_full_name": repo.full_name,
            "branch": repo.default_branch or "main",
            "source_item_id": str(source_item_id),
            "harvester_version": _HARVESTER_VERSION,
        },
    )
    session.add(article)
    await session.flush()
    return article.id, True


def _render_repo_context_articles(
    *, intel: RepoIntel, repo: WorkspaceRepo
) -> list[tuple[str, str, str]]:
    """Format repo intel as focused articles inside one base bucket."""

    return [
        ("overview", "Overview", _render_overview_article(intel=intel, repo=repo)),
        (
            "dev-environment",
            "Development environment",
            _render_dev_environment_article(intel=intel, repo=repo),
        ),
        (
            "architecture",
            "Architecture",
            _render_architecture_article(intel=intel, repo=repo),
        ),
        ("workflow", "Workflow", _render_workflow_article(intel=intel, repo=repo)),
        ("visual-style", "Visual style", _render_visual_style_article(intel=intel, repo=repo)),
        ("rules", "Rules", _render_rules_article(intel=intel, repo=repo)),
    ]


def _consolidate_intel_document(
    *, repo: WorkspaceRepo, articles: list[tuple[str, str, str]]
) -> str:
    """Join the rendered repo-context sections into ONE markdown document
    for Lighthouse (one document per repo, not N bucket articles)."""
    sections = [body for _, _, body in articles if body.strip()]
    return "\n\n".join(sections).strip() + "\n"


async def _emit_intel_to_lighthouse(
    *, repo: WorkspaceRepo, intel: RepoIntel
) -> None:
    """Best-effort: ship the repo intel as one markdown document to S3 so
    the per-workspace Lighthouse importer ingests it. No-op when S3 isn't
    configured; never raises — the harvest must not fail because the
    knowledge engine / object store is unreachable."""
    from backend.app.core.config import get_settings
    from backend.app.integrations.lighthouse import build_knowledge_s3_writer

    writer = build_knowledge_s3_writer(get_settings())
    if writer is None:
        return
    document = _consolidate_intel_document(
        repo=repo,
        articles=_render_repo_context_articles(intel=intel, repo=repo),
    )
    try:
        await writer.write_document(
            workspace_id=repo.workspace_id,
            source="repo-intel",
            name=_slugify_repo(repo.full_name),
            markdown=document,
        )
    except Exception:
        logger.warning(
            "lighthouse S3 emit failed for repo=%s — will retry next harvest",
            repo.id,
            exc_info=True,
        )


def _article_header(title: str, *, intel: RepoIntel, repo: WorkspaceRepo) -> list[str]:
    lines = [f"# {title}", ""]
    lines.append(f"- Repo: `{repo.full_name}`")
    lines.append(f"- Branch: `{repo.default_branch or 'main'}`")
    lines.append(f"- Intel version: {intel.version}")
    if intel.harvested_at is not None:
        lines.append(f"- Harvested at: {intel.harvested_at.isoformat()}")
    lines.append("")
    return lines


def _render_overview_article(*, intel: RepoIntel, repo: WorkspaceRepo) -> str:
    lines = _article_header("Repository overview", intel=intel, repo=repo)
    if intel.languages:
        lines.append("## Languages")
        for lang, ratio in intel.languages.items():
            try:
                pct = float(ratio) * 100
            except (TypeError, ValueError):
                continue
            lines.append(f"- `{lang}`: {pct:.1f}%")
        lines.append("")
    if intel.frameworks:
        lines.append("## Frameworks")
        for fw in intel.frameworks:
            lines.append(f"- {fw}")
        lines.append("")
    if intel.package_managers:
        lines.append("## Package managers")
        for pm in intel.package_managers:
            lines.append(f"- {pm}")
        lines.append("")
    return _finish_article(lines)


def _render_dev_environment_article(*, intel: RepoIntel, repo: WorkspaceRepo) -> str:
    lines = _article_header("Development environment", intel=intel, repo=repo)
    lines.append("Use the detected package managers and entry points as the first hints for setup, build, test, and lint commands.")
    lines.append("")
    if intel.package_managers:
        lines.append("## Package managers")
        for pm in intel.package_managers:
            lines.append(f"- {pm}")
        lines.append("")
    if intel.entry_points:
        lines.append("## Entry points")
        for ep in intel.entry_points:
            if isinstance(ep, dict):
                lines.append(f"- `{ep.get('path')}` ({ep.get('kind', 'unknown')})")
        lines.append("")
    return _finish_article(lines)


def _render_architecture_article(*, intel: RepoIntel, repo: WorkspaceRepo) -> str:
    lines = _article_header("Architecture", intel=intel, repo=repo)
    structure = intel.structure or {}
    top = structure.get("top_level_dirs") or []
    if top:
        lines.append("## Top-level directories")
        for dirname in top:
            lines.append(f"- `{dirname}`")
        lines.append("")
    if "file_count" in structure:
        lines.append(f"- File count: {structure['file_count']}")
    if "depth_p50" in structure:
        lines.append(
            f"- Depth p50/p95: {structure.get('depth_p50')}/{structure.get('depth_p95')}"
        )
    return _finish_article(lines)


def _render_workflow_article(*, intel: RepoIntel, repo: WorkspaceRepo) -> str:
    lines = _article_header("Workflow", intel=intel, repo=repo)
    commit_style = intel.commit_style or {}
    if commit_style:
        lines.append("## Commit style")
        lines.append(f"- Convention: {commit_style.get('convention', 'unknown')}")
        common = commit_style.get("common_types") or []
        if common:
            lines.append("- Common types: " + ", ".join(f"`{t}`" for t in common))
        if "avg_subject_len" in commit_style:
            lines.append(f"- Average subject length: {commit_style['avg_subject_len']}")
        lines.append("")
    return _finish_article(lines)


def _render_visual_style_article(*, intel: RepoIntel, repo: WorkspaceRepo) -> str:
    lines = _article_header("Visual style", intel=intel, repo=repo)
    if intel.visual_tokens:
        lines.append("## Tokens")
        for key, value in intel.visual_tokens.items():
            lines.append(f"- {key}: `{value}`")
        lines.append("")
    else:
        lines.append("No visual token files were detected during the latest harvest.")
        lines.append("")
    return _finish_article(lines)


def _render_rules_article(*, intel: RepoIntel, repo: WorkspaceRepo) -> str:
    lines = _article_header("Repository rules", intel=intel, repo=repo)
    lines.append("Ship seeded configuration and agent rules should be treated as repository-local development context.")
    lines.append("")
    lines.append("## Expected locations")
    lines.append("- `.ship/config.yml` for Ship lanes and automation config.")
    lines.append("- `.cursor/rules/` or equivalent editor rule files when installed by the operator.")
    lines.append("- `.github/workflows/` for seeded or repository-native CI workflows.")
    lines.append("")
    return _finish_article(lines)


def _finish_article(lines: list[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Inline-fallback helper
# ---------------------------------------------------------------------------


async def _inline_harvest(
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    triggered_by: str,
) -> None:
    """Run a harvest in a fresh session for the no-redis fallback path.

    Errors are logged but not re-raised: this runs in an
    ``asyncio.create_task`` and a stray exception would otherwise
    surface as an unhandled-task warning at shutdown.
    """
    from backend.app.db.session import get_sessionmaker

    sessionmaker = get_sessionmaker()
    try:
        async with sessionmaker() as session:
            await harvest_repo_intel(
                session=session,
                workspace_id=workspace_id,
                repo_id=repo_id,
                triggered_by=triggered_by,
            )
            await session.commit()
    except Exception:  # noqa: BLE001
        logger.exception(
            "inline repo_intel harvest failed for repo=%s", repo_id
        )


# ---------------------------------------------------------------------------
# Shared blob-read helper
# ---------------------------------------------------------------------------


async def _read_text(
    gw: CodeHostGateway,
    *,
    ref: RepoRef,
    repo: WorkspaceRepo,
    path: str,
) -> str | None:
    """Fetch ``path`` from ``ref`` and return its UTF-8 contents or ``None``.

    Returns ``None`` for missing files, oversized blobs, binaries, or
    fetch errors — every step that calls this is best-effort and
    treats missing data as "skip" rather than "fail".
    """
    try:
        blob = await gw.get_blob(
            ref, path=path, ref_sha=repo.default_branch or None
        )
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("repo_intel _read_text failed for %s: %s", path, exc)
        return None
    if blob.encoding != "utf-8":
        return None
    if isinstance(blob, BlobContent) and blob.size > _MAX_BLOB_BYTES:
        return None
    return blob.content


__all__ = [
    "CommitFetcher",
    "HarvestReport",
    "enqueue_harvest",
    "get_current_intel",
    "harvest_repo_intel",
]
