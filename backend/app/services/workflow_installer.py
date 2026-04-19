"""Install Ship workflow artifacts into a target repo.

Each install:

1. Reads the workflow ARTIFACT.md from our local catalog (artifact_loader).
2. Renders an executable stub at ``spec.install_target`` (defaulting to
   ``.github/workflows/{id}.yml`` when the artifact doesn't pin one).
3. Drops a copy of the artifact body at ``.ship/workflows/{id}.md`` so the
   target repo carries the human-readable contract alongside the YAML.
4. Updates ``.ship/lock.yaml`` with the installed id + version + sha256, the
   same way ``shipctl install`` will once the CLI catches up.
5. Commits everything in one commit titled ``ship: install N workflow(s)``.

We only mutate the working tree under :func:`pathlib.Path` we were handed and
under ``.ship/`` + ``.github/workflows/`` — never anything else. Callers are
responsible for sanity-checking that the path actually points at the user's
repo (the route handler does this by gating on workspace membership).
"""

from __future__ import annotations

import dataclasses
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


logger = logging.getLogger(__name__)


def _catalog_root() -> Path:
    """Where the global workflow artifacts live on disk.

    In docker we mount the repo's ``artifacts/`` at ``/app/artifacts``; for
    local dev (running pytest from the repo root) we walk up to the actual
    repo. Either way we return a directory that contains ``workflows/``.
    """
    candidates = [
        Path("/app/artifacts"),
        Path(__file__).resolve().parents[3] / "artifacts",
    ]
    for c in candidates:
        if (c / "workflows").is_dir():
            return c
    # Last resort: return the docker path so the caller hits a clear error.
    return Path("/app/artifacts")


def _read_workflow_artifact(catalog_root: Path, aid: str) -> dict[str, Any] | None:
    """Read one workflow artifact's frontmatter + body. Returns ``None`` if missing.

    Reuses :func:`backend.app.main._normalize_inline_lists` so the same
    "@-prefixed flow lists are valid" rule the public API uses also applies
    here. Without it, frontmatter like ``authors: [@elmundi/ship-core]``
    fails YAML parsing and every install would silently skip every artifact.
    """
    # Imported lazily to keep this module free of circular imports.
    from backend.app.main import _normalize_inline_lists

    path = catalog_root / "workflows" / aid / "ARTIFACT.md"
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    raw = text[4:end]
    body = text[end + len("\n---\n") :]
    try:
        meta = yaml.safe_load(_normalize_inline_lists(raw)) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    if meta.get("id") != aid:
        return None
    return {"meta": meta, "body": body}


@dataclasses.dataclass
class InstalledFile:
    path: str  # repo-relative
    bytes_written: int
    overwrote_existing: bool


@dataclasses.dataclass
class InstallResult:
    repo_path: str
    branch: str | None
    head_before: str | None
    head_after: str | None
    commit_made: bool
    files: list[InstalledFile]
    installed: list[dict[str, Any]]  # one per artifact
    skipped: list[dict[str, Any]]  # missing-from-catalog ids


@dataclasses.dataclass
class InstallerError(Exception):
    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.code}: {self.message}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        timeout=20,
    )


def _git_text(repo: Path, args: list[str]) -> str | None:
    try:
        out = _git(repo, args)
        return out.stdout.decode("utf-8", "replace").strip() or None
    except subprocess.CalledProcessError:
        return None


def _ensure_git_identity(repo: Path) -> None:
    """git commit refuses to run without an author; set one if missing.

    We only set per-repo config (``git config user.email …`` without
    ``--global``), so we never trample the operator's global identity.
    """
    if not _git_text(repo, ["config", "user.email"]):
        _git(repo, ["config", "user.email", "ship-onboarding@ship.dev"])
    if not _git_text(repo, ["config", "user.name"]):
        _git(repo, ["config", "user.name", "Ship Onboarding"])


def _ensure_git_repo(repo: Path) -> None:
    """Init a git repo on the spot if the user pointed us at a plain folder.

    Without this, every commit step would fail for users whose 'repo' is
    really a fresh sandbox or scaffold.
    """
    if (repo / ".git").exists():
        return
    _git(repo, ["init", "--initial-branch=main"], check=False)


def _write(target: Path, content: str) -> InstalledFile:
    target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    data = content.encode("utf-8")
    target.write_bytes(data)
    return InstalledFile(
        path=str(target),
        bytes_written=len(data),
        overwrote_existing=existed,
    )


def _render_github_actions(artifact: dict[str, Any]) -> str:
    """Generate a runnable GitHub Actions stub for a workflow artifact.

    The body is intentionally simple: it documents the artifact and shells
    out to ``shipctl run {id}`` so the source of truth for the actual lane
    behaviour stays in the artifact, not duplicated as YAML.
    """
    meta = artifact.get("meta", {})
    aid = meta.get("id", "ship-lane")
    name = meta.get("name", aid)
    description = (meta.get("description") or "").strip()
    schedule = (
        meta.get("spec", {}).get("schedule")
        if isinstance(meta.get("spec"), dict)
        else None
    )
    cron_block = (
        f"  schedule:\n    - cron: {schedule!r}\n" if schedule else ""
    )
    return (
        f"# Generated by Ship onboarding — artifact {aid}@{meta.get('version', '0.0.0')}\n"
        f"# {description}\n"
        f"#\n"
        f"# Edit cadence / approvals here; the lane semantics stay in\n"
        f"# .ship/workflows/{aid}.md (the artifact contract).\n"
        f"name: {name}\n\n"
        f"on:\n"
        f"  workflow_dispatch: {{}}\n"
        f"  pull_request: {{}}\n"
        f"{cron_block}\n"
        f"jobs:\n"
        f"  ship-{aid}:\n"
        f"    runs-on: ubuntu-latest\n"
        f"    steps:\n"
        f"      - uses: actions/checkout@v4\n"
        f"      - uses: actions/setup-node@v4\n"
        f"        with:\n"
        f"          node-version: '20'\n"
        f"      - name: Install shipctl\n"
        f"        run: npm i -g @elmundi/ship-cli\n"
        f"      - name: Run lane\n"
        f"        env:\n"
        f"          SHIP_TOKEN: ${{{{ secrets.SHIP_TOKEN }}}}\n"
        f"        run: shipctl run {aid}\n"
    )


def _default_install_target(artifact: dict[str, Any]) -> str:
    spec = artifact.get("meta", {}).get("spec", {})
    if isinstance(spec, dict) and spec.get("install_target"):
        return str(spec["install_target"])
    aid = artifact.get("meta", {}).get("id", "lane")
    return f".github/workflows/{aid}.yml"


def _load_lock(repo: Path) -> dict[str, Any]:
    lock_path = repo / ".ship" / "lock.yaml"
    if not lock_path.exists():
        return {"version": 1, "workflows": {}, "patterns": {}, "tools": {}}
    try:
        data = yaml.safe_load(lock_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        data = {}
    data.setdefault("version", 1)
    data.setdefault("workflows", {})
    data.setdefault("patterns", {})
    data.setdefault("tools", {})
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def install_workflows(
    *,
    repo_path: Path | str,
    workflow_ids: list[str],
    actor: str | None = None,
) -> InstallResult:
    """Install the named workflow artifacts into the repo at ``repo_path``."""
    repo = Path(repo_path).expanduser().resolve()
    if not repo.exists() or not repo.is_dir():
        raise InstallerError("bad_path", f"{repo} is not a directory")

    catalog_root = _catalog_root()

    _ensure_git_repo(repo)
    _ensure_git_identity(repo)
    head_before = _git_text(repo, ["rev-parse", "HEAD"])
    branch = _git_text(repo, ["rev-parse", "--abbrev-ref", "HEAD"])

    files: list[InstalledFile] = []
    installed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    lock = _load_lock(repo)
    timestamp = datetime.now(timezone.utc).isoformat()

    for aid in workflow_ids:
        artifact = _read_workflow_artifact(catalog_root, aid)
        if artifact is None:
            skipped.append({"id": aid, "reason": "not_in_catalog"})
            continue
        meta = artifact["meta"]
        body = artifact.get("body", "")

        # 1. Write the executable stub at the artifact's install_target.
        target_path = repo / _default_install_target(artifact)
        files.append(_write(target_path, _render_github_actions(artifact)))

        # 2. Drop the artifact contract under .ship/workflows/.
        contract_path = repo / ".ship" / "workflows" / f"{aid}.md"
        files.append(_write(contract_path, body))

        # 3. Lock entry.
        lock["workflows"][aid] = {
            "version": meta.get("version"),
            "channel": meta.get("channel"),
            "content_sha256": meta.get("content_sha256"),
            "installed_at": timestamp,
            "installed_by": actor,
        }
        installed.append(
            {
                "id": aid,
                "name": meta.get("name"),
                "version": meta.get("version"),
                "install_target": _default_install_target(artifact),
                "contract_path": f".ship/workflows/{aid}.md",
            }
        )

    # 4. Persist the lockfile if anything actually installed.
    if installed:
        lock_path = repo / ".ship" / "lock.yaml"
        files.append(
            _write(
                lock_path,
                yaml.safe_dump(lock, sort_keys=True, default_flow_style=False),
            )
        )

    # 5. Stage + commit if there's anything to record.
    commit_made = False
    if installed:
        _git(repo, ["add", "--", ".ship", ".github/workflows"], check=False)
        # `git diff --cached --quiet` returns 0 when there are no staged
        # changes; we use that to skip an empty commit.
        cached = _git(repo, ["diff", "--cached", "--quiet"], check=False)
        if cached.returncode != 0:
            msg = (
                f"ship: install {len(installed)} workflow"
                f"{'s' if len(installed) != 1 else ''}\n\n"
                + "\n".join(f"- {a['id']}@{a['version']}" for a in installed)
                + ("\n\nactor: " + actor if actor else "")
            )
            try:
                _git(repo, ["commit", "-m", msg])
                commit_made = True
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.decode("utf-8", "replace")
                raise InstallerError("commit_failed", stderr.strip()) from exc

    head_after = _git_text(repo, ["rev-parse", "HEAD"])
    return InstallResult(
        repo_path=str(repo),
        branch=branch,
        head_before=head_before,
        head_after=head_after,
        commit_made=commit_made,
        files=files,
        installed=installed,
        skipped=skipped,
    )
