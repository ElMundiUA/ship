"""Generate the first batch of knowledge-bucket documents from a repo.

When the wizard finishes the workflow + tracker steps, we run this against
the inspected repo to produce three opinionated markdown docs:

* **brandbook** — name, tagline, voice, key links derived from README +
  ``package.json``/``pyproject.toml`` description.
* **code-style** — language, formatter, linter, ``.editorconfig`` snippet
  if present, plus a "house rules" section seeded with widely-accepted
  defaults for the detected stack.
* **testing** — test framework, sample test command, what's covered today
  vs. what's missing, plus a "what we'd add next" recommendation block.

The output lives at ``.ship/knowledge/{slug}.md`` inside the user's repo
and gets committed in a single commit. When the knowledge-bucket backend
lands (RFC-0006 step "Documents"), the indexer will read the same files
and ingest them into pgvector — so what we write today becomes the seed
data tomorrow without a migration step.

Everything is template-driven; no LLM calls. That keeps the wizard fast,
deterministic, and runnable offline.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any  # noqa: F401  (kept for forward compat with future LLM hooks)

from backend.app.services.repo_inspector import RepoProfile, inspect


logger = logging.getLogger(__name__)


@dataclasses.dataclass
class SeededDoc:
    slug: str  # e.g. "brandbook"
    title: str
    path: str  # repo-relative path on disk
    bytes_written: int
    excerpt: str  # first ~400 chars for the wizard preview


@dataclasses.dataclass
class SeedResult:
    repo_path: str
    branch: str | None
    head_before: str | None
    head_after: str | None
    commit_made: bool
    docs: list[SeededDoc]


@dataclasses.dataclass
class SeederError(Exception):
    code: str
    message: str

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.code}: {self.message}"


# ---------------------------------------------------------------------------
# Git helpers (small wrappers; the workflow installer has its own copies and
# we deliberately don't share to keep these services drop-in independent)
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
        return _git(repo, args).stdout.decode("utf-8", "replace").strip() or None
    except subprocess.CalledProcessError:
        return None


def _ensure_git(repo: Path) -> None:
    if not (repo / ".git").exists():
        _git(repo, ["init", "--initial-branch=main"], check=False)
    if not _git_text(repo, ["config", "user.email"]):
        _git(repo, ["config", "user.email", "ship-onboarding@ship.dev"])
    if not _git_text(repo, ["config", "user.name"]):
        _git(repo, ["config", "user.name", "Ship Onboarding"])


def _read(path: Path, max_bytes: int = 64 * 1024) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_bytes]
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Brand inference
# ---------------------------------------------------------------------------


def _readme_first_paragraph(text: str | None) -> str | None:
    if not text:
        return None
    # Drop heading markers + collapse blank runs, then take the first chunk
    # before a blank line.
    cleaned = re.sub(r"^\s*#+\s*", "", text, flags=re.MULTILINE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    para, *_ = cleaned.split("\n\n", 1)
    return para.strip()


def _readme_h1(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        m = re.match(r"^\s*#\s+(.+?)\s*$", line)
        if m:
            return m.group(1).strip()
    return None


def _package_json_meta(repo: Path) -> dict[str, str]:
    pkg = repo / "package.json"
    if not pkg.exists():
        return {}
    try:
        data = json.loads(pkg.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, str] = {}
    for key in ("name", "description", "homepage", "license", "author"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()
    return out


def _pyproject_meta(repo: Path) -> dict[str, str]:
    py = repo / "pyproject.toml"
    if not py.exists():
        return {}
    text = py.read_text(encoding="utf-8", errors="replace")
    out: dict[str, str] = {}
    for key in ("name", "description", "version", "license"):
        m = re.search(rf'^\s*{key}\s*=\s*"([^"]+)"', text, flags=re.MULTILINE)
        if m:
            out[key] = m.group(1).strip()
    return out


def _key_links(text: str | None) -> list[tuple[str, str]]:
    """Return up to 5 (label, href) tuples from the README's first 3kb."""
    if not text:
        return []
    snippet = text[:3000]
    pairs: list[tuple[str, str]] = []
    for label, href in re.findall(r"\[([^\]]{1,80})\]\((https?://[^)\s]+)\)", snippet):
        pairs.append((label.strip(), href.strip()))
        if len(pairs) >= 5:
            break
    return pairs


# ---------------------------------------------------------------------------
# Document templates
# ---------------------------------------------------------------------------


def _brandbook(profile: RepoProfile, repo: Path) -> str:
    readme = _read(repo / "README.md") or _read(repo / "README")
    pkg = _package_json_meta(repo)
    py = _pyproject_meta(repo)

    title = (
        _readme_h1(readme)
        or pkg.get("name")
        or py.get("name")
        or profile.suggested_name
    )
    description = (
        pkg.get("description")
        or py.get("description")
        or _readme_first_paragraph(readme)
        or "_No description detected. Edit this section so the team and the agents share one elevator pitch._"
    )
    voice = _readme_first_paragraph(readme) or description
    links = _key_links(readme)
    homepage = pkg.get("homepage") or profile.remote_url

    lines = [
        f"# {title} · brandbook",
        "",
        "_Auto-seeded by Ship onboarding from the repo's README + package metadata. Edit freely; this file is the source of truth for downstream agents and the daily/retro lanes._",
        "",
        "## What we make",
        "",
        description,
        "",
        "## Voice & tone",
        "",
        "- **Default register:** product-led, concrete, no marketing fluff.",
        "- **Avoid:** buzzwords, hype, second-person commands without context.",
        f"- **Sample paragraph (from the repo README):** \n\n  > {voice[:600]}",
        "",
        "## Identity",
        "",
        f"- **Project name:** {title}",
        f"- **Workspace slug suggestion:** `{profile.suggested_slug}`",
    ]
    if homepage:
        lines.append(f"- **Homepage / origin:** {homepage}")
    if pkg.get("license") or py.get("license"):
        lines.append(f"- **License:** {pkg.get('license') or py.get('license')}")
    lines.extend(["", "## Key links", ""])
    if links:
        for label, href in links:
            lines.append(f"- [{label}]({href})")
    else:
        lines.append("_No links detected in the README. Add the ones the team uses daily here._")
    lines.extend(
        [
            "",
            "## How agents should refer to us",
            "",
            f"When generating PRs, copy, daily digests, or comms, use **{title}** as the canonical name. Don't invent variations.",
            "",
            f"_Seeded {datetime.now(timezone.utc).isoformat(timespec='seconds')} from {profile.source}._",
            "",
        ]
    )
    return "\n".join(lines)


def _code_style(profile: RepoProfile, repo: Path) -> str:
    primary = profile.primary_language or "polyglot"
    formatters: list[str] = []
    linters: list[str] = []
    for cfg in profile.code_style_configs:
        name = cfg.lower()
        if "prettier" in name:
            formatters.append("Prettier")
        if "eslint" in name:
            linters.append("ESLint")
        if "biome" in name:
            formatters.append("Biome")
            linters.append("Biome")
        if "ruff" in name:
            linters.append("Ruff")
            formatters.append("Ruff (formatter)")
        if "rustfmt" in name:
            formatters.append("rustfmt")
        if "golangci" in name:
            linters.append("golangci-lint")
        if "rubocop" in name:
            linters.append("RuboCop")
            formatters.append("RuboCop (autocorrect)")
        if "clang-format" in name:
            formatters.append("clang-format")
        if "swiftformat" in name:
            formatters.append("SwiftFormat")
    formatters = list(dict.fromkeys(formatters)) or _default_formatters(primary)
    linters = list(dict.fromkeys(linters)) or _default_linters(primary)

    editorconfig = _read(repo / ".editorconfig", max_bytes=2_000)

    lines = [
        f"# {profile.suggested_name} · code style",
        "",
        "_Auto-seeded by Ship onboarding from configs detected in the repo. Treat this as a working draft for the team to ratify in a 30-minute review._",
        "",
        "## Stack",
        "",
        f"- **Primary language:** {primary}",
    ]
    if profile.frameworks:
        lines.append(f"- **Frameworks:** {', '.join(profile.frameworks)}")
    if profile.package_managers:
        lines.append(f"- **Package managers:** {', '.join(profile.package_managers)}")
    lines.extend(
        [
            "",
            "## Tooling",
            "",
            f"- **Formatters:** {', '.join(formatters) or '_none configured yet_'}",
            f"- **Linters:** {', '.join(linters) or '_none configured yet_'}",
            "",
        ]
    )
    if profile.code_style_configs:
        lines.append("**Configs found in the repo:**")
        lines.append("")
        for cfg in profile.code_style_configs:
            lines.append(f"- `{cfg}`")
        lines.append("")

    if editorconfig:
        lines.append("## .editorconfig (live in repo)")
        lines.append("")
        lines.append("```ini")
        lines.append(editorconfig.strip())
        lines.append("```")
        lines.append("")

    lines.extend(
        [
            "## House rules",
            "",
            "1. **Format on save** with the configured formatter — no separate formatting commits.",
            "2. **Lint warnings are todos**, not noise. Fix or `// TODO(handle): note` with a tracker link.",
            "3. **No drive-by reformats** in product PRs; do them in dedicated cleanup PRs labelled `chore: format`.",
            "4. **Imports: sorted, grouped** (stdlib, third-party, internal). Configured via the tooling above.",
            "5. **Public APIs are documented**; private helpers explain *why*, not *what*.",
            "",
            "## What agents should do",
            "",
            "- Run the configured formatter + linter as part of every generated PR.",
            "- When uncertain about a style choice, follow the closest existing example in the repo, not generic conventions.",
            "- Surface lint failures as part of the PR description; do not silently fix unrelated warnings.",
            "",
            f"_Seeded {datetime.now(timezone.utc).isoformat(timespec='seconds')} from {profile.source}._",
            "",
        ]
    )
    return "\n".join(lines)


def _default_formatters(language: str) -> list[str]:
    return {
        "python": ["Ruff (formatter)"],
        "typescript": ["Prettier"],
        "javascript": ["Prettier"],
        "go": ["gofmt", "goimports"],
        "rust": ["rustfmt"],
        "java": ["google-java-format"],
        "kotlin": ["ktlint"],
    }.get(language, [])


def _default_linters(language: str) -> list[str]:
    return {
        "python": ["Ruff", "mypy"],
        "typescript": ["ESLint"],
        "javascript": ["ESLint"],
        "go": ["golangci-lint"],
        "rust": ["clippy"],
        "java": ["SpotBugs", "Checkstyle"],
        "kotlin": ["detekt"],
    }.get(language, [])


def _testing(profile: RepoProfile, repo: Path) -> str:
    primary = profile.primary_language or "polyglot"
    fwks = profile.test_frameworks or _default_test_frameworks(primary)
    has_e2e = any(fw in fwks for fw in ("Playwright", "Cypress"))

    sample_command = _default_test_command(primary, fwks)

    lines = [
        f"# {profile.suggested_name} · testing approach",
        "",
        "_Auto-seeded by Ship onboarding from the repo's test files + configs. Use this as the contract that CI and AI-generated PRs both honour._",
        "",
        "## Today",
        "",
        f"- **Test frameworks:** {', '.join(fwks) or '_none detected_'}",
        f"- **Tests folder present:** {'yes' if profile.has_tests else 'no'}",
        f"- **CI runs them:** {'yes (' + ', '.join(profile.ci_systems) + ')' if profile.has_ci else 'not yet'}",
        f"- **Sample command:** `{sample_command}`",
        "",
        "## Pyramid we aim for",
        "",
        "| Layer | What | Owner | Run on |",
        "|-------|------|-------|--------|",
        "| Unit | Pure-function logic, fast (<1s each) | author | every commit |",
        "| Integration | Real DB / queue / filesystem, hermetic | author | every PR |",
        f"| {'E2E (UI/API)' if has_e2e else 'Smoke (HTTP)'} | One happy path through the user-visible flow | reviewer | every PR + nightly |",
        "| Scheduled | Cross-service regressions, perf, contract | quality lane | nightly |",
        "",
    ]

    if has_e2e:
        lines.extend(
            [
                "## End-to-end",
                "",
                "Since Playwright/Cypress is already in the repo, the *Hosted E2E regression* workflow is the high-leverage next step — schedule it in `.github/workflows/hosted-e2e-regression.yml` and post the trace artifacts on failure.",
                "",
            ]
        )

    lines.extend(
        [
            "## What agents should do",
            "",
            "- **Touch a public function → write a unit test** in the same PR.",
            "- **Touch an HTTP route → add a smoke test** that exercises the route end to end with a stubbed boundary.",
            "- **Flaky test? Fix it or quarantine** with a tracker link in the same PR; do not skip silently.",
            "- **Coverage is a smell, not a goal:** target the *behaviour* a code path enables, not lines.",
            "",
            f"_Seeded {datetime.now(timezone.utc).isoformat(timespec='seconds')} from {profile.source}._",
            "",
        ]
    )
    return "\n".join(lines)


def _default_test_frameworks(language: str) -> list[str]:
    return {
        "python": ["pytest"],
        "typescript": ["Vitest"],
        "javascript": ["Vitest"],
        "go": ["go test"],
        "rust": ["cargo test"],
        "java": ["JUnit 5"],
    }.get(language, [])


def _default_test_command(language: str, fwks: list[str]) -> str:
    if "pytest" in fwks:
        return "pytest"
    if "Vitest" in fwks:
        return "npm test -- --run"
    if "Jest" in fwks:
        return "npm test"
    if "Playwright" in fwks:
        return "npx playwright test"
    if language == "go":
        return "go test ./..."
    if language == "rust":
        return "cargo test"
    return "make test"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_Generator = Callable[[RepoProfile, Path], str]

# Slug → (title, generator) so callers can pick a subset.
GENERATORS: dict[str, tuple[str, _Generator]] = {
    "brandbook": ("Brandbook", _brandbook),
    "code-style": ("Code style", _code_style),
    "testing": ("Testing approach", _testing),
}


def seed(
    *,
    profile: RepoProfile | None = None,
    source: str | None = None,
    bucket_slugs: list[str] | None = None,
    actor: str | None = None,
) -> SeedResult:
    """Generate seed knowledge docs and commit them into the user's repo.

    Pass either a pre-computed ``profile`` or a ``source`` string we'll
    re-inspect on the fly. ``bucket_slugs`` defaults to all three.
    """
    if profile is None:
        if source is None:
            raise SeederError("missing_source", "either profile= or source= is required")
        profile = inspect(source)

    repo = Path(profile.local_path).resolve()
    if not repo.exists() or not repo.is_dir():
        raise SeederError("bad_path", f"{repo} is not a directory")

    slugs = bucket_slugs or list(GENERATORS.keys())
    unknown = [s for s in slugs if s not in GENERATORS]
    if unknown:
        raise SeederError("unknown_bucket", f"unknown bucket slugs: {unknown}")

    _ensure_git(repo)
    head_before = _git_text(repo, ["rev-parse", "HEAD"])
    branch = _git_text(repo, ["rev-parse", "--abbrev-ref", "HEAD"])

    docs: list[SeededDoc] = []
    knowledge_dir = repo / ".ship" / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    for slug in slugs:
        title, gen = GENERATORS[slug]
        content = gen(profile, repo)
        path = knowledge_dir / f"{slug}.md"
        data = content.encode("utf-8")
        path.write_bytes(data)
        docs.append(
            SeededDoc(
                slug=slug,
                title=title,
                path=str(path),
                bytes_written=len(data),
                excerpt=content[:400] + ("…" if len(content) > 400 else ""),
            )
        )

    _git(repo, ["add", "--", ".ship/knowledge"], check=False)
    cached = _git(repo, ["diff", "--cached", "--quiet"], check=False)
    commit_made = False
    if cached.returncode != 0:
        msg = (
            f"ship: seed {len(docs)} knowledge doc{'s' if len(docs) != 1 else ''}\n\n"
            + "\n".join(f"- {d.slug}: {d.path}" for d in docs)
            + ("\n\nactor: " + actor if actor else "")
        )
        try:
            _git(repo, ["commit", "-m", msg])
            commit_made = True
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode("utf-8", "replace")
            raise SeederError("commit_failed", stderr.strip()) from exc

    head_after = _git_text(repo, ["rev-parse", "HEAD"])
    return SeedResult(
        repo_path=str(repo),
        branch=branch,
        head_before=head_before,
        head_after=head_after,
        commit_made=commit_made,
        docs=docs,
    )
