"""Repo inspection for the onboarding wizard.

Given a path or URL the user typed into the wizard, produce a structured
:class:`RepoProfile` that downstream steps can reason about (suggest a
workspace name, recommend workflows, seed knowledge buckets).

Two source kinds are supported:

* ``file:///abs/path`` (or a bare absolute path) — read the working tree
  directly. This is the laptop / "I already cloned it" case.
* ``https://…`` / ``git@…`` / ``ssh://…`` — clone shallowly into a
  per-workspace cache directory. Subsequent inspections reuse the clone.

Everything here is read-only with respect to the user's repo. Mutations live
in :mod:`backend.app.services.workflow_installer` and
:mod:`backend.app.services.knowledge_seeder`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse


logger = logging.getLogger(__name__)


# Where remote repos get cloned. Per-process tmp by default; override with the
# env var when you want clones to survive container restarts (e.g. mount a
# named volume there in docker-compose).
WORKBENCH_ROOT = Path(os.environ.get("SHIP_REPO_WORKBENCH", "/tmp/ship-repos"))

# Folders / files we always skip when walking. Keeps inspection sub-second
# even on monorepos by not descending into giant generated trees.
IGNORED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "dist",
        "build",
        "target",
        ".next",
        ".turbo",
        ".cache",
        "coverage",
        ".idea",
        ".vscode",
    }
)

# Soft cap. We rarely need more than this and walking 100k files is rude.
MAX_FILES_SCANNED = 5000

CODE_STYLE_FILES = {
    ".editorconfig",
    ".prettierrc",
    ".prettierrc.json",
    ".prettierrc.yaml",
    ".prettierrc.yml",
    ".prettierrc.js",
    ".prettierrc.cjs",
    "prettier.config.js",
    "prettier.config.cjs",
    ".eslintrc",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.json",
    ".eslintrc.yaml",
    ".eslintrc.yml",
    "eslint.config.js",
    "eslint.config.cjs",
    "eslint.config.mjs",
    "eslint.config.ts",
    "biome.json",
    "biome.jsonc",
    ".biome.json",
    ".ruff.toml",
    "ruff.toml",
    ".flake8",
    ".isort.cfg",
    "pyproject.toml",
    ".rustfmt.toml",
    "rustfmt.toml",
    ".gofmt",
    ".golangci.yml",
    ".golangci.yaml",
    ".clang-format",
    ".swiftformat",
    ".rubocop.yml",
    ".scalafmt.conf",
}

CI_FILES = {
    ".gitlab-ci.yml",
    "Jenkinsfile",
    ".circleci/config.yml",
    "azure-pipelines.yml",
    "buildkite.yml",
    ".buildkite/pipeline.yml",
}

TEST_DIR_HINTS = ("tests", "test", "__tests__", "spec", "specs", "e2e")
TEST_FILE_HINTS = re.compile(r"(^|/)(test_[^/]+|.+\.test\.[a-z]+|.+\.spec\.[a-z]+)$")

LANG_BY_EXT = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "c#",
    ".scala": "scala",
}


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RepoProfile:
    """What we learned about a repo. Pydantic-friendly via :func:`asdict`."""

    source: str  # the URL or path the user gave us
    source_kind: str  # "file" | "remote"
    local_path: str  # absolute path on disk where we read it
    cached: bool  # was a previous clone reused?

    # Suggested workspace identity (the wizard will let the user override).
    suggested_name: str
    suggested_slug: str

    # Repo basics.
    head_branch: str | None
    head_sha: str | None
    remote_url: str | None
    file_count: int
    truncated: bool  # we hit MAX_FILES_SCANNED

    # Stack detection.
    languages: dict[str, int]  # ext-derived line-count proxy (file count)
    primary_language: str | None
    frameworks: list[str]
    package_managers: list[str]

    # Quality + automation evidence.
    has_readme: bool
    readme_excerpt: str | None
    has_tests: bool
    test_frameworks: list[str]
    has_ci: bool
    ci_systems: list[str]
    code_style_configs: list[str]

    # Recommendations the inspector hands the wizard for the next step.
    recommended_workflows: list[str]


@dataclasses.dataclass
class RepoSourceError(Exception):
    """Raised when the user-supplied source can't be turned into a usable tree."""

    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover — trivial
        return f"{self.code}: {self.message}"


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def _is_remote(source: str) -> bool:
    if source.startswith(("http://", "https://", "ssh://", "git+", "git@")):
        return True
    parsed = urlparse(source)
    return parsed.scheme in {"http", "https", "ssh", "git", "git+ssh", "git+https"}


def _file_path(source: str) -> Path:
    if source.startswith("file://"):
        return Path(source[len("file://") :])
    return Path(source)


def _cache_dir_for(source: str) -> Path:
    """Stable per-URL clone target so a re-inspection doesn't re-clone."""
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:16]
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", source)[:64].strip("-") or "repo"
    return WORKBENCH_ROOT / f"{safe}-{digest}"


def _clone(source: str) -> tuple[Path, bool]:
    """Shallow-clone ``source`` into the workbench. Returns (path, cached)."""
    target = _cache_dir_for(source)
    if (target / ".git").exists():
        # Cheap freshness: pull origin. If pull fails (auth, no network), we
        # surface the cached tree anyway — better than blocking the wizard.
        try:
            subprocess.run(
                ["git", "fetch", "--depth", "1", "origin"],
                cwd=target,
                check=True,
                capture_output=True,
                timeout=30,
            )
        except Exception as exc:  # pragma: no cover — network / auth dependent
            logger.warning("repo refetch failed for %s: %s", source, exc)
        return target, True

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", source, str(target)],
            check=True,
            capture_output=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", "replace") if exc.stderr else ""
        raise RepoSourceError(
            "clone_failed",
            f"git clone failed: {stderr.strip() or exc!s}",
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RepoSourceError(
            "clone_timeout", f"git clone timed out after 60s for {source}"
        ) from exc
    return target, False


def resolve_source(source: str) -> tuple[Path, str, bool]:
    """Materialise ``source`` to a local directory.

    Returns ``(path, kind, cached)`` where kind is ``"file"`` or ``"remote"``.
    """
    if _is_remote(source):
        path, cached = _clone(source)
        return path, "remote", cached

    path = _file_path(source).expanduser().resolve()
    if not path.exists():
        raise RepoSourceError("not_found", f"{path} does not exist")
    if not path.is_dir():
        raise RepoSourceError("not_a_dir", f"{path} is not a directory")
    return path, "file", False


# ---------------------------------------------------------------------------
# Walk + analyse
# ---------------------------------------------------------------------------


def _walk(root: Path) -> Iterable[Path]:
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # In-place mutation of dirnames is the supported way to prune.
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
        for fname in sorted(filenames):
            seen += 1
            if seen > MAX_FILES_SCANNED:
                return
            yield Path(dirpath) / fname


def _git_meta(root: Path) -> tuple[str | None, str | None, str | None]:
    if not (root / ".git").exists():
        return None, None, None

    def _run(args: list[str]) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=root,
                check=True,
                capture_output=True,
                timeout=5,
            )
            return out.stdout.decode("utf-8", "replace").strip() or None
        except Exception:
            return None

    branch = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    sha = _run(["rev-parse", "HEAD"])
    remote = _run(["config", "--get", "remote.origin.url"])
    return branch, sha, remote


def _read_text(path: Path, max_bytes: int = 64 * 1024) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    except OSError:
        return None


def _suggested_identity(root: Path, remote: str | None) -> tuple[str, str]:
    """Pick a friendly workspace name + slug for the wizard to pre-fill."""
    candidate = None
    if remote:
        # Strip .git suffix and pull the trailing path segment.
        cleaned = remote.rstrip("/").removesuffix(".git")
        candidate = cleaned.rsplit("/", 1)[-1] or cleaned.rsplit(":", 1)[-1]
    candidate = candidate or root.name or "workspace"
    slug = re.sub(r"[^a-z0-9]+", "-", candidate.lower()).strip("-")
    if not slug or not slug[0].isalnum():
        slug = "ws"
    slug = slug[:40]
    name = candidate.replace("-", " ").replace("_", " ").strip().title() or "Workspace"
    return name, slug


def _detect_node(root: Path) -> tuple[list[str], list[str], list[str]]:
    """Look at package.json for frameworks + test runners."""
    pkg = root / "package.json"
    if not pkg.exists():
        return [], [], []
    try:
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return [], [], []
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    frameworks: list[str] = []
    for key, label in [
        ("next", "Next.js"),
        ("react", "React"),
        ("vue", "Vue"),
        ("svelte", "Svelte"),
        ("@angular/core", "Angular"),
        ("nuxt", "Nuxt"),
        ("remix", "Remix"),
        ("astro", "Astro"),
        ("express", "Express"),
        ("fastify", "Fastify"),
        ("nest", "NestJS"),
        ("@nestjs/core", "NestJS"),
    ]:
        if key in deps:
            frameworks.append(label)
    test_fwks: list[str] = []
    for key, label in [
        ("jest", "Jest"),
        ("vitest", "Vitest"),
        ("@playwright/test", "Playwright"),
        ("cypress", "Cypress"),
        ("mocha", "Mocha"),
    ]:
        if key in deps:
            test_fwks.append(label)
    return frameworks, test_fwks, ["npm"]


def _detect_python(root: Path) -> tuple[list[str], list[str], list[str]]:
    frameworks: list[str] = []
    test_fwks: list[str] = []
    package_managers: list[str] = []

    pyproject = root / "pyproject.toml"
    requirements = root / "requirements.txt"

    if pyproject.exists():
        package_managers.append("pyproject")
        text = pyproject.read_text(encoding="utf-8", errors="replace").lower()
    elif requirements.exists():
        package_managers.append("requirements.txt")
        text = requirements.read_text(encoding="utf-8", errors="replace").lower()
    else:
        return [], [], []

    for needle, label in [
        ("fastapi", "FastAPI"),
        ("django", "Django"),
        ("flask", "Flask"),
        ("starlette", "Starlette"),
        ("sanic", "Sanic"),
        ("aiohttp", "aiohttp"),
    ]:
        if needle in text:
            frameworks.append(label)
    for needle, label in [
        ("pytest", "pytest"),
        ("unittest", "unittest"),
        ("nose", "nose"),
    ]:
        if needle in text:
            test_fwks.append(label)
    return frameworks, test_fwks, package_managers


def _detect_other(root: Path) -> tuple[list[str], list[str], list[str]]:
    frameworks: list[str] = []
    test_fwks: list[str] = []
    package_managers: list[str] = []
    if (root / "Cargo.toml").exists():
        package_managers.append("cargo")
    if (root / "go.mod").exists():
        package_managers.append("go modules")
    if (root / "Gemfile").exists():
        package_managers.append("bundler")
    if (root / "composer.json").exists():
        package_managers.append("composer")
    return frameworks, test_fwks, package_managers


def _readme_excerpt(text: str | None) -> str | None:
    if not text:
        return None
    # Trim heading marks + collapse whitespace, take the first ~600 chars.
    cleaned = re.sub(r"^\s*#+\s*", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned[:600] + ("…" if len(cleaned) > 600 else "")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def inspect(source: str) -> RepoProfile:
    """Walk the repo at ``source`` and produce a :class:`RepoProfile`."""
    path, kind, cached = resolve_source(source)
    branch, sha, remote = _git_meta(path)
    name, slug = _suggested_identity(path, remote)

    languages: dict[str, int] = {}
    code_style: list[str] = []
    ci_systems: list[str] = []
    has_tests = False
    test_dirs: list[str] = []
    file_count = 0
    truncated = False

    for fpath in _walk(path):
        file_count += 1
        rel = fpath.relative_to(path).as_posix()

        # Language tally.
        ext = fpath.suffix.lower()
        if ext in LANG_BY_EXT:
            lang = LANG_BY_EXT[ext]
            languages[lang] = languages.get(lang, 0) + 1

        name_lower = fpath.name.lower()
        rel_lower = rel.lower()

        if fpath.name in CODE_STYLE_FILES:
            code_style.append(rel)

        if rel.startswith(".github/workflows/") and rel.endswith((".yml", ".yaml")):
            if "github-actions" not in ci_systems:
                ci_systems.append("github-actions")
        if name_lower in CI_FILES or rel in CI_FILES:
            label = name_lower
            if rel.startswith(".gitlab"):
                label = "gitlab-ci"
            elif "circleci" in rel_lower:
                label = "circleci"
            elif "buildkite" in rel_lower:
                label = "buildkite"
            elif "jenkins" in rel_lower:
                label = "jenkins"
            elif "azure" in rel_lower:
                label = "azure-pipelines"
            if label not in ci_systems:
                ci_systems.append(label)

        if not has_tests:
            top = rel.split("/", 1)[0]
            if top in TEST_DIR_HINTS:
                has_tests = True
                test_dirs.append(top)
            elif TEST_FILE_HINTS.search(rel):
                has_tests = True

    if file_count > MAX_FILES_SCANNED:
        truncated = True

    node_fwks, node_tests, node_pm = _detect_node(path)
    py_fwks, py_tests, py_pm = _detect_python(path)
    other_fwks, other_tests, other_pm = _detect_other(path)

    frameworks = list(dict.fromkeys(node_fwks + py_fwks + other_fwks))
    test_frameworks = list(dict.fromkeys(node_tests + py_tests + other_tests))
    package_managers = list(dict.fromkeys(node_pm + py_pm + other_pm))

    primary = max(languages, key=languages.get) if languages else None
    has_ci = bool(ci_systems)
    has_readme = (path / "README.md").exists() or (path / "README").exists()
    readme = _read_text(path / "README.md") or _read_text(path / "README")
    readme_excerpt = _readme_excerpt(readme)

    recommended = _recommend_workflows(
        primary=primary,
        frameworks=frameworks,
        has_ci=has_ci,
        ci_systems=ci_systems,
        has_tests=has_tests,
        test_frameworks=test_frameworks,
    )

    return RepoProfile(
        source=source,
        source_kind=kind,
        local_path=str(path),
        cached=cached,
        suggested_name=name,
        suggested_slug=slug,
        head_branch=branch,
        head_sha=sha,
        remote_url=remote,
        file_count=file_count,
        truncated=truncated,
        languages=languages,
        primary_language=primary,
        frameworks=frameworks,
        package_managers=package_managers,
        has_readme=has_readme,
        readme_excerpt=readme_excerpt,
        has_tests=has_tests,
        test_frameworks=test_frameworks,
        has_ci=has_ci,
        ci_systems=ci_systems,
        code_style_configs=sorted(code_style),
        recommended_workflows=recommended,
    )


def _recommend_workflows(
    *,
    primary: str | None,
    frameworks: list[str],
    has_ci: bool,
    ci_systems: list[str],
    has_tests: bool,
    test_frameworks: list[str],
) -> list[str]:
    """Map detected facts to workflow artifact ids in our catalog.

    Conservative on purpose: we only recommend the artifact when we have a
    concrete reason, so the wizard can highlight them without over-promising.
    """
    recs: list[str] = []
    # If they already use GitHub Actions, the PR gate is a high-leverage add.
    if "github-actions" in ci_systems or has_ci:
        recs.append("pr-and-ci-gate")
    # Playwright/Cypress projects benefit from the hosted regression lane.
    if any(fw in test_frameworks for fw in ("Playwright", "Cypress")):
        recs.append("hosted-e2e-regression")
    # Anything sizeable benefits from the SDLC + audit lanes.
    if has_ci or len(frameworks) >= 1:
        recs.append("scheduled-sdlc-lane")
        recs.append("parallel-audit-lanes")
    # Self-heal is universal — diagnostics for whichever pipeline you have.
    recs.append("pipeline-self-heal")
    # De-dup while preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for r in recs:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def reset_workbench() -> None:
    """Wipe the clone cache. Used by tests; safe in production too."""
    if WORKBENCH_ROOT.exists():
        shutil.rmtree(WORKBENCH_ROOT, ignore_errors=True)
