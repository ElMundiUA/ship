"""Deploy planner — repo signals + key files → a validated ``DeployPlan``.

The planner is the differentiator of the deploy feature: it turns "what is
this repo?" into "how should it be deployed?". It feeds the model two
things:

1. **Deterministic signals** already harvested into ``RepoIntel``
   (languages, frameworks, project_type, sdlc_maturity, entry points).
2. **Excerpts of the files that actually decide a deploy** (manifests,
   Dockerfile, Procfile, framework configs, entrypoints).

The model returns a provider-agnostic :class:`DeployPlan`; provider
adapters translate it deterministically. The planner is pure w.r.t.
infrastructure — it takes a :class:`CodeHostGateway` + :class:`RepoRef`
so it works across GitHub/GitLab/Azure and is trivial to unit-test with a
fake gateway.
"""

from __future__ import annotations

import logging
import re
from typing import Final

from pydantic import ValidationError

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.repo_intel import RepoIntel
from backend.app.integrations.gateway.code_host import CodeHostGateway, RepoRef
from backend.app.services.deploy.llm import (
    PlannerLLMCredentials,
    PlannerLLMError,
    complete_json,
)
from backend.app.services.deploy.plan import DeployPlan


logger = logging.getLogger(__name__)


class DeployPlanningError(RuntimeError):
    """The planner could not produce a valid plan."""


# Files whose presence/contents materially change how a repo deploys.
# Matched case-insensitively by basename; the planner also reads any
# entry-point paths RepoIntel already surfaced.
_KEY_FILE_BASENAMES: Final[frozenset[str]] = frozenset(
    {
        # Python
        "requirements.txt",
        "pyproject.toml",
        "pipfile",
        "setup.py",
        "runtime.txt",
        ".python-version",
        "streamlit_app.py",
        "app.py",
        "main.py",
        "wsgi.py",
        "asgi.py",
        # Node / JS
        "package.json",
        "next.config.js",
        "next.config.mjs",
        "next.config.ts",
        "vite.config.js",
        "vite.config.ts",
        "nuxt.config.ts",
        "svelte.config.js",
        "angular.json",
        "server.js",
        "index.js",
        ".nvmrc",
        # Frontend entry sources — where a SPA usually references the
        # backend (e.g. import.meta.env.VITE_API_URL, a hardcoded
        # localhost URL). Without these the planner can't wire the
        # frontend→backend connection.
        "app.jsx",
        "app.tsx",
        "main.jsx",
        "main.tsx",
        "index.jsx",
        "index.tsx",
        "app.js",
        "main.js",
        # Generic / infra
        "dockerfile",
        "docker-compose.yml",
        "docker-compose.yaml",
        "procfile",
        "app.yaml",
        # High-signal-for-deploy extras: env var declarations, run docs,
        # task runners, runtime pins, host configs.
        ".env.example",
        ".env.sample",
        ".env.template",
        "readme.md",
        "readme",
        "makefile",
        ".tool-versions",
        "nginx.conf",
        "netlify.toml",
        "vercel.json",
        "go.mod",
        "cargo.toml",
        "composer.json",
        "gemfile",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "pubspec.yaml",
        "deno.json",
    }
)

# Manifests whose *location* marks an independently-deployable unit. The
# directory a manifest sits in is a candidate component (``source_dir``),
# which is exactly how a monorepo decomposes — one component per sub-app.
_DEPLOY_MANIFESTS: Final[frozenset[str]] = frozenset(
    {
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "pipfile",
        "setup.py",
        "go.mod",
        "cargo.toml",
        "composer.json",
        "gemfile",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
        "pubspec.yaml",
        "deno.json",
        "procfile",
        "app.yaml",
    }
)

# Root markers that explicitly declare a JS/Go monorepo with sub-packages.
_WORKSPACE_MARKERS: Final[frozenset[str]] = frozenset(
    {
        "pnpm-workspace.yaml",
        "lerna.json",
        "turbo.json",
        "nx.json",
        "rush.json",
        "go.work",
    }
)

_MAX_FILES: Final[int] = 24
_MAX_FILE_CHARS: Final[int] = 3000
_MAX_TOTAL_CHARS: Final[int] = 48_000

_SYSTEM_PROMPT: Final[str] = (
    "You are a senior release engineer. Produce a deployment plan as a "
    "single JSON object matching the provided schema. Reason through these "
    "steps internally, then output ONLY the JSON:\n"
    "1. Read the repository layout and key files. Identify the runtime(s), "
    "framework(s), and build tooling in use.\n"
    "2. Decide the topology. Is this ONE project, or a MONOREPO with "
    "several independently-deployable projects? It's a monorepo when "
    "manifests live in multiple directories (e.g. frontend/ + backend/) or "
    "a workspace marker is present (pnpm-workspace.yaml, turbo.json, "
    "lerna.json, nx.json, go.work).\n"
    "3. For EACH deployable project emit one component and classify it: "
    "'service' (long-running HTTP), 'static_site' (built frontend served "
    "as static assets), 'worker' (no HTTP), or 'job' (run-to-completion). "
    "Set its 'source_dir' to that project's directory (use '/' or omit for "
    "a single root project).\n"
    "4. For each component infer: 'runtime' (a buildpack like 'python' / "
    "'node-js' / 'go'). Use 'docker' ONLY when a Dockerfile actually "
    "exists in that component's directory — verify it against the file "
    "tree; never assume a Dockerfile. With 'docker', set 'dockerfile_path' "
    "to the real path relative to 'source_dir'. Also set "
    "'build_command', 'run_command', 'output_dir' (static sites), "
    "'http_port' (prefer 8080), 'routes', and a real 'health_check_path' "
    "for services. Declare required env var NAMES only — never invent "
    "secret values.\n"
    "5. CONNECTIVITY. Reason about how the deployed app wires together:\n"
    "   a. ENV CONTRACT — derive it from the SOURCE CODE (process.env.*, "
    "import.meta.env.*), do NOT rely on a .env file existing. Declare every "
    "env var the code reads. Set a 'value' ONLY for non-secret deploy "
    "config you can infer; leave secrets' value null for the operator.\n"
    "   b. BIND — a container must listen on 0.0.0.0, not localhost. If the "
    "service reads its host from env (HOST/HOSTNAME/BIND_ADDR/ADDRESS), set "
    "that env value '0.0.0.0'; set the bind port env to http_port. If the "
    "code hard-codes 127.0.0.1/localhost with no env override, add a "
    "'warnings' entry (env can't fix it).\n"
    "   c. FRONTEND->BACKEND — identify by MEANING, not by a fixed name, "
    "how the browser frontend addresses the backend: ANY env var or "
    "hardcoded URL used as the API/backend base, whatever it is called "
    "(VITE_API_URL, BACKEND_URL, API_BASE, SERVER_URL — and even misspelled "
    "variants). Read the frontend source to find it. Point that env's value "
    "at the special token '$APP_URL' (the deployed app's own public URL) so "
    "the browser hits the same domain, and route the backend so that URL "
    "reaches it. A frontend must NEVER ship pointing at localhost/127.0.0.1 "
    "— if it hardcodes a loopback URL with no env override, add a 'warnings' "
    "entry.\n"
    "6. If a signal is missing or ambiguous, lower 'confidence' and add a "
    "'warnings' entry explaining what you assumed. Never set 'value' for "
    "secret env vars.\n"
    "Output one JSON object only — no prose, no markdown fences."
)


def _is_dockerfile(base: str) -> bool:
    """Match ``Dockerfile`` and variants like ``Dockerfile.web``."""
    return base == "dockerfile" or base.startswith("dockerfile.")


def _is_deploy_manifest(base: str) -> bool:
    return base in _DEPLOY_MANIFESTS or _is_dockerfile(base)


def _dir_of(path: str) -> str:
    """Directory of ``path`` as a component-friendly label ('/' for root)."""
    return path.rsplit("/", 1)[0] if "/" in path else "/"


async def _collect_key_files(
    gateway: CodeHostGateway,
    repo_ref: RepoRef,
    *,
    intel: RepoIntel | None,
    ref_sha: str | None,
) -> tuple[dict[str, str], dict[str, list[str]], list[str]]:
    """Read deploy-relevant files + map layout + full path list. Best-effort.

    Returns ``(files, layout, paths)``: ``files`` is ``path -> excerpt``,
    ``layout`` is ``directory -> [manifest basenames]`` across the WHOLE
    repo (uncapped), and ``paths`` is every file path in the repo (the
    skeleton). Sending the full path tree (names only — cheap) gives the
    model complete structural visibility without dumping source contents.

    File selection is **directory-diverse**: we take the top manifest from
    each directory first, then backfill, so a monorepo's sub-app manifests
    aren't crowded out by many root-level files.
    """
    try:
        paths = await gateway.list_files(repo_ref, ref_sha=ref_sha)
    except Exception as exc:  # noqa: BLE001 — gateway/network failures are non-fatal
        logger.warning("deploy planner: list_files failed: %s", exc)
        paths = []

    # Manifest layout across the entire repo (uncapped) — each directory
    # holding a manifest is a candidate deployable component.
    layout: dict[str, list[str]] = {}
    for p in paths:
        base = p.rsplit("/", 1)[-1].lower()
        if _is_deploy_manifest(base) or base in _WORKSPACE_MARKERS:
            layout.setdefault(_dir_of(p), [])
            name = p.rsplit("/", 1)[-1]
            if name not in layout[_dir_of(p)]:
                layout[_dir_of(p)].append(name)

    # Candidate files to actually read: entry points + key basenames +
    # manifests + Dockerfile variants + workspace markers.
    def _is_wanted(base: str) -> bool:
        return (
            base in _KEY_FILE_BASENAMES
            or _is_deploy_manifest(base)
            or base in _WORKSPACE_MARKERS
        )

    candidates: list[str] = []
    seen: set[str] = set()

    def _consider(path: str) -> None:
        if path in seen:
            return
        if _is_wanted(path.rsplit("/", 1)[-1].lower()):
            seen.add(path)
            candidates.append(path)

    for ep in (intel.entry_points if intel else []) or []:
        p = ep.get("path") if isinstance(ep, dict) else None
        if isinstance(p, str):
            _consider(p)
    for p in paths:
        _consider(p)

    # Within a directory, manifests first, then shallower/shorter paths.
    def _prio(path: str) -> tuple[int, int]:
        base = path.rsplit("/", 1)[-1].lower()
        return (0 if _is_deploy_manifest(base) else 1, len(path))

    by_dir: dict[str, list[str]] = {}
    for p in candidates:
        by_dir.setdefault(_dir_of(p), []).append(p)
    for d in by_dir:
        by_dir[d].sort(key=_prio)

    # Round-robin across directories (sorted shallow → deep) so every
    # candidate component dir is represented before we spend budget on a
    # second file from any one dir.
    dirs_sorted = sorted(by_dir, key=lambda d: (d.count("/"), d))
    ordered: list[str] = []
    round_idx = 0
    while len(ordered) < _MAX_FILES:
        added_any = False
        for d in dirs_sorted:
            if round_idx < len(by_dir[d]):
                ordered.append(by_dir[d][round_idx])
                added_any = True
                if len(ordered) >= _MAX_FILES:
                    break
        if not added_any:
            break
        round_idx += 1

    out: dict[str, str] = {}
    total = 0
    for path in ordered[:_MAX_FILES]:
        try:
            blob = await gateway.get_blob(repo_ref, path=path, ref_sha=ref_sha)
        except FileNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("deploy planner: get_blob(%s) failed: %s", path, exc)
            continue
        if blob.encoding != "utf-8":
            continue
        snippet = blob.content[:_MAX_FILE_CHARS]
        if total + len(snippet) > _MAX_TOTAL_CHARS:
            break
        out[path] = snippet
        total += len(snippet)
    return out, layout, paths


def _render_layout(layout: dict[str, list[str]]) -> str:
    """Human-readable manifest map: each line is a candidate component dir."""
    if not layout:
        return "(no build/deploy manifests detected)"
    # Shallow dirs first; root labelled clearly.
    lines = []
    for d in sorted(layout, key=lambda x: (x.count("/"), x)):
        label = "/ (repo root)" if d == "/" else d
        lines.append(f"- {label}: {', '.join(sorted(layout[d]))}")
    return "\n".join(lines)


# Cap the file tree we inline so a giant repo can't blow the prompt. Names
# only (no content) are cheap, so this is generous — most repos fit whole.
_MAX_TREE_PATHS: Final[int] = 600


def _render_tree(paths: list[str]) -> str:
    """Bounded newline list of repo file paths (the skeleton, names only)."""
    if not paths:
        return "(file list unavailable)"
    shown = sorted(paths)[:_MAX_TREE_PATHS]
    suffix = (
        f"\n… (+{len(paths) - _MAX_TREE_PATHS} more files)"
        if len(paths) > _MAX_TREE_PATHS
        else ""
    )
    return "\n".join(shown) + suffix


def _render_user_prompt(
    *,
    repo_ref: RepoRef,
    private: bool,
    default_branch: str,
    intel: RepoIntel | None,
    files: dict[str, str],
    layout: dict[str, list[str]],
    paths: list[str],
) -> str:
    import json

    signals = {
        "repo": repo_ref.full_name,
        "host": repo_ref.kind,
        "private": private,
        "default_branch": default_branch,
        "languages": (intel.languages if intel else {}),
        "frameworks": (intel.frameworks if intel else []),
        "package_managers": (intel.package_managers if intel else []),
        "project_type": (intel.project_type if intel else None),
        "sdlc_maturity": (intel.sdlc_maturity if intel else {}),
        "entry_points": (intel.entry_points if intel else []),
        "structure": (intel.structure if intel else {}),
    }
    schema = DeployPlan.llm_json_schema()
    file_blocks = "\n\n".join(
        f"### FILE: {path}\n```\n{body}\n```" for path, body in files.items()
    ) or "(no key files could be read)"
    multi = len(layout) > 1
    return (
        "Detected repository signals (JSON):\n"
        f"{json.dumps(signals, indent=2, default=str)}\n\n"
        "Repository file tree (all paths, names only):\n"
        f"{_render_tree(paths)}\n\n"
        "Repository layout — directories containing build/deploy manifests "
        "(each is a candidate deployable component; its directory is the "
        "component 'source_dir'):\n"
        f"{_render_layout(layout)}\n\n"
        + (
            "This repo has manifests in multiple directories — treat it as a "
            "MONOREPO and emit one component per directory above, each with "
            "its 'source_dir' set to that directory.\n\n"
            if multi
            else ""
        )
        + "Key file contents:\n"
        f"{file_blocks}\n\n"
        "Target platform notes:\n"
        "- The deploy target builds each service from source; pick a "
        "buildpack runtime ('python', 'node-js', ...) or 'docker' when a "
        "Dockerfile is the right driver.\n"
        "- Set each component's 'source_dir' to the directory of its "
        "manifest (omit or '/' for a single root project).\n"
        "- Streamlit apps are a single 'service' with run_command "
        "'streamlit run <entry>.py --server.port 8080 --server.address "
        "0.0.0.0' and health_check_path '/_stcore/health'.\n"
        "- If the repo is private, add a warning that the deploy target "
        "must be authorized to access the repository.\n\n"
        "Return ONLY a JSON object matching this JSON schema:\n"
        f"{json.dumps(schema)}"
    )


_LOOPBACK_URL_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1)(?::\d+)?", re.I)


def _verify_plan(
    plan: DeployPlan, files: dict[str, str], paths: list[str]
) -> list[str]:
    """Deterministic safety net: check the plan against KNOWN repo facts.

    Catches the common LLM misses by SYMPTOM (not by guessing names), so a
    retry can be issued with a concrete correction:

    * a ``docker`` component pointing at a Dockerfile that isn't in the repo,
    * a ``source_dir`` that doesn't exist,
    * a frontend whose source still hardcodes a localhost backend URL that
      wasn't redirected to ``$APP_URL`` (name-agnostic — we look for the
      loopback URL itself, not for a specific env var name).
    """
    pathset = set(paths)
    issues: list[str] = []

    for c in plan.components:
        sd = (c.source_dir or "").strip("/")
        if c.runtime == "docker" or c.dockerfile_path:
            df = (c.dockerfile_path or "Dockerfile").strip("/")
            full = df if (not sd or df == sd or df.startswith(sd + "/")) else f"{sd}/{df}"
            if full not in pathset:
                issues.append(
                    f"component '{c.name}' uses Dockerfile '{full}' which is "
                    f"not in the repo — use a buildpack runtime (node-js/"
                    f"python/...) with build_command/run_command, or set the "
                    f"correct existing dockerfile path"
                )
        if sd and not any(p == sd or p.startswith(sd + "/") for p in paths):
            issues.append(
                f"component '{c.name}' has source_dir '{sd}' but no files "
                f"exist under it — set source_dir to a real directory"
            )

    # Name-agnostic frontend→backend wiring check: did the planner leave a
    # shippable loopback URL un-wired?
    loopback_files = [p for p, body in files.items() if _LOOPBACK_URL_RE.search(body)]
    if loopback_files:
        wired = any(
            "$APP_URL" in ((getattr(ev, "value", None) or ""))
            for c in plan.components
            for ev in (c.env or [])
        )
        warned = any(
            ("localhost" in w.lower() or "127.0.0.1" in w)
            for w in (plan.warnings or [])
        )
        if not wired and not warned:
            issues.append(
                f"the frontend source ({loopback_files[0]}) addresses the "
                f"backend via a localhost URL. A deployed frontend must not "
                f"point at localhost. Set the frontend's API-base env value "
                f"to the token '$APP_URL' (or, if it's hardcoded with no env, "
                f"add a 'warnings' entry)"
            )
    return issues


async def plan_deployment(
    *,
    gateway: CodeHostGateway,
    repo_ref: RepoRef,
    private: bool,
    default_branch: str,
    intel: RepoIntel | None,
    ref_sha: str | None = None,
    settings: Settings | None = None,
    llm_credentials: PlannerLLMCredentials | None = None,
) -> DeployPlan:
    """Produce a validated :class:`DeployPlan` for ``repo_ref``.

    Raises :class:`DeployPlanningError` if no LLM is available or the model
    output fails validation.
    """
    settings = settings or get_settings()
    files, layout, paths = await _collect_key_files(
        gateway, repo_ref, intel=intel, ref_sha=ref_sha
    )
    user_prompt = _render_user_prompt(
        repo_ref=repo_ref,
        private=private,
        default_branch=default_branch,
        intel=intel,
        files=files,
        layout=layout,
        paths=paths,
    )
    # Retry the completion a few times. LLMs (especially fast/flash tiers)
    # occasionally emit malformed JSON or a schema-violating plan. Since we
    # run at temperature 0, a plain retry would reproduce the same bad
    # output — so on later attempts we append a corrective instruction to
    # the prompt, which changes the input enough to get a clean response.
    _MAX_ATTEMPTS = 3
    _RETRY_HINT = (
        "\n\nIMPORTANT: your previous reply could not be parsed as the "
        "required JSON. Return ONLY one valid JSON object matching the "
        "schema — no prose, no comments, no markdown fences, no trailing "
        "commas, every string quoted and every property comma-separated."
    )
    last_err: Exception | None = None
    correction = ""  # appended to the prompt on retries (JSON or verify fixes)
    for attempt in range(_MAX_ATTEMPTS):
        try:
            payload = await complete_json(
                system=_SYSTEM_PROMPT,
                user=user_prompt + correction,
                # Output cap (the plan JSON), not input. Plan size scales
                # with the number of deployable components, not lines of
                # code, so even big monorepos rarely exceed a few thousand
                # tokens. 8192 is comfortable headroom and the safe ceiling
                # across providers (gemini/mistral/anthropic flash tiers cap
                # output at 8192; going higher risks an API error there).
                max_tokens=8192,
                settings=settings,
                credentials=llm_credentials,
            )
            plan = DeployPlan.model_validate(payload)
        except (PlannerLLMError, ValidationError) as exc:
            last_err = exc
            correction = _RETRY_HINT
            logger.warning(
                "deploy planner attempt %d/%d unparseable/invalid: %s",
                attempt + 1,
                _MAX_ATTEMPTS,
                exc,
            )
            continue

        # Deterministic safety net — verify the plan against repo facts and,
        # if something's off, retry with a concrete correction (since temp=0,
        # the changed prompt is what makes the next attempt differ).
        issues = _verify_plan(plan, files, paths)
        if not issues:
            return plan
        last_err = DeployPlanningError("; ".join(issues))
        correction = (
            "\n\nYour previous plan had these problems — FIX them and return "
            "the corrected JSON:\n- " + "\n- ".join(issues)
        )
        logger.warning(
            "deploy planner attempt %d/%d verify issues: %s",
            attempt + 1,
            _MAX_ATTEMPTS,
            issues,
        )
    raise DeployPlanningError(
        "the deploy planner model couldn't return a valid plan after "
        f"{_MAX_ATTEMPTS} tries. Try a stronger model (e.g. a pro tier) "
        f"in the planner picker. Last error: {last_err}"
    )


__all__ = ["DeployPlanningError", "plan_deployment"]
