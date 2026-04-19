"""Materialise remote artifact repos onto disk.

For every :class:`backend.app.db.models.tenancy.ArtifactRepo` whose ``url``
is **not** a ``file://`` path, the cron worker (or a manual sync-now request)
calls :func:`sync_repo` here. The function:

1. Computes the repo's cache directory under
   ``<repo_cache_root>/<workspace_id>/<repo_id>``.
2. Either ``git clone --depth 1 --branch <default_branch>`` if the directory
   is empty, or ``git fetch --depth 1`` + ``git reset --hard origin/<branch>``
   if a previous clone exists.
3. Writes the resulting commit sha, sync timestamp and any error back onto
   the ArtifactRepo row.

The resolver (:mod:`backend.app.services.artifact_resolver`) consults the
same cache root, so a successful sync makes the repo visible to the catalog
and knowledge endpoints on the next request — no in-process state to
invalidate.

Authentication: HTTPS URLs that already embed credentials
(``https://x-access-token:<TOKEN>@github.com/owner/repo.git``) work as-is.
A higher-level helper that mints a token from a GitHub integration on the
fly will land alongside RFC-0007.
"""

from __future__ import annotations

import dataclasses
import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from backend.app.core.config import get_settings
from backend.app.db.models.tenancy import ArtifactRepo


log = logging.getLogger("ship.git_sync")

GIT_TIMEOUT_SECONDS = 90  # bigger than the worker tick because clones are slow


@dataclasses.dataclass(frozen=True, slots=True)
class SyncOutcome:
    repo_id: str
    cache_path: str
    head_sha: str | None
    cloned: bool  # True on the first sync, False on subsequent fetches
    error: str | None  # human-readable, populated when the sync failed


def _repo_cache_path(repo: ArtifactRepo) -> Path:
    root = Path(get_settings().repo_cache_root).expanduser()
    return root / str(repo.workspace_id) / str(repo.id)


def repo_cache_path(repo: ArtifactRepo) -> Path:
    """Public alias the resolver uses to find synced content on disk."""
    return _repo_cache_path(repo)


def is_remote_url(url: str) -> bool:
    """Return True for URLs the worker should clone (anything but ``file://``)."""
    parsed = urlparse(url)
    if parsed.scheme in ("", "file"):
        return False
    return True


def _git(args: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        timeout=GIT_TIMEOUT_SECONDS,
        check=False,
    )


def _stderr(proc: subprocess.CompletedProcess) -> str:
    """Decode + truncate stderr to fit the 2KB ``last_sync_error`` column."""
    text = proc.stderr.decode("utf-8", "replace").strip()
    return text[:1900] + ("…" if len(text) > 1900 else "")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_clone(repo: ArtifactRepo, cache_path: Path) -> SyncOutcome:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    branch = repo.default_branch or "main"
    proc = _git(
        [
            "clone",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            branch,
            repo.url,
            str(cache_path),
        ]
    )
    if proc.returncode != 0:
        # If clone failed because the workdir is partially populated from a
        # previous attempt, scrub it so the next tick gets a clean slate.
        if cache_path.exists():
            shutil.rmtree(cache_path, ignore_errors=True)
        return SyncOutcome(
            repo_id=str(repo.id),
            cache_path=str(cache_path),
            head_sha=None,
            cloned=False,
            error=f"git clone failed: {_stderr(proc)}",
        )
    head = _git(["rev-parse", "HEAD"], cwd=cache_path)
    sha = head.stdout.decode("utf-8", "replace").strip() if head.returncode == 0 else None
    return SyncOutcome(
        repo_id=str(repo.id),
        cache_path=str(cache_path),
        head_sha=sha,
        cloned=True,
        error=None,
    )


def _refresh_clone(repo: ArtifactRepo, cache_path: Path) -> SyncOutcome:
    branch = repo.default_branch or "main"
    fetch = _git(["fetch", "--depth", "1", "origin", branch], cwd=cache_path)
    if fetch.returncode != 0:
        return SyncOutcome(
            repo_id=str(repo.id),
            cache_path=str(cache_path),
            head_sha=None,
            cloned=False,
            error=f"git fetch failed: {_stderr(fetch)}",
        )
    reset = _git(["reset", "--hard", f"origin/{branch}"], cwd=cache_path)
    if reset.returncode != 0:
        return SyncOutcome(
            repo_id=str(repo.id),
            cache_path=str(cache_path),
            head_sha=None,
            cloned=False,
            error=f"git reset failed: {_stderr(reset)}",
        )
    head = _git(["rev-parse", "HEAD"], cwd=cache_path)
    sha = head.stdout.decode("utf-8", "replace").strip() if head.returncode == 0 else None
    return SyncOutcome(
        repo_id=str(repo.id),
        cache_path=str(cache_path),
        head_sha=sha,
        cloned=False,
        error=None,
    )


def sync_repo(repo: ArtifactRepo) -> SyncOutcome:
    """Materialise ``repo`` on disk. Idempotent; safe to call repeatedly.

    Returns a :class:`SyncOutcome` describing what happened. The caller is
    responsible for persisting the result back onto the database row (we
    deliberately don't take a session here so the function stays usable from
    both async request paths and the sync subprocess that runs ``git``).
    """
    if not is_remote_url(repo.url):
        # Pure file:// URLs are read inline by the resolver — nothing to do.
        return SyncOutcome(
            repo_id=str(repo.id),
            cache_path="",
            head_sha=None,
            cloned=False,
            error=None,
        )

    cache_path = _repo_cache_path(repo)
    if (cache_path / ".git").is_dir():
        return _refresh_clone(repo, cache_path)
    # Either nothing on disk, or a leftover non-git directory. Wipe it so
    # ``git clone`` starts fresh.
    if cache_path.exists():
        shutil.rmtree(cache_path, ignore_errors=True)
    return _ensure_clone(repo, cache_path)


def apply_outcome(repo: ArtifactRepo, outcome: SyncOutcome) -> None:
    """Copy a :class:`SyncOutcome` onto the ArtifactRepo row in-place."""
    repo.last_sync_at = _now()
    if outcome.error is None:
        repo.last_sync_sha = outcome.head_sha
        repo.last_sync_error = None
    else:
        repo.last_sync_error = outcome.error


__all__ = [
    "GIT_TIMEOUT_SECONDS",
    "SyncOutcome",
    "apply_outcome",
    "is_remote_url",
    "repo_cache_path",
    "sync_repo",
]
