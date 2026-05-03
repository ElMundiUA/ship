from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from backend.app.api.v1 import api_router as v1_api_router
from backend.app.core.sentry import init_sentry
from backend.app.db.session import dispose_engine, get_engine


# Sentry must be wired up *before* FastAPI is constructed so the
# StarletteIntegration can monkey-patch the routing layer when the app
# instance is built. Empty SENTRY_DSN turns this into a no-op.
init_sentry(service_name="ship-server")


APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATHS = ("documentation", "README.md")
ARTIFACTS_ROOT = APP_ROOT / "artifacts"
TELEMETRY_DIR = APP_ROOT / "backend" / "telemetry"
TELEMETRY_FILE = TELEMETRY_DIR / "events.jsonl"

REQUIRED_ENTRY_FIELDS = (
    "version",
    "content_sha256",
    "updated_at",
    "channel",
    "min_shipctl",
    "deprecated",
    "replaced_by",
    "yanked",
)

ARTIFACT_KINDS = {
    # ``collection`` is the only legacy artifact kind survivor: ``shipctl
    # init --copy-rules`` still tugs ``collection/agent-rules-<agent>``
    # bodies into the customer repo through the unauthenticated
    # ``/collections`` + ``/fetch`` endpoints below. Phase 2.5 moves
    # agent rule distribution into the wizard seed bundle and retires
    # this kind too.
    "collection": ("collections", "collections"),
}

ALLOWED_TELEMETRY_TYPES = {
    "artifact.fetch",
    "artifact.use",
    "artifact.sync",
    "feedback.submit",
    "doctor.result",
}

TELEMETRY_PAYLOAD_DENYLIST = {"path", "code", "diff", "branch", "remote", "email"}

UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[.+-][0-9A-Za-z.+-]+)?$")


class FetchRequest(BaseModel):
    """Either a repo file (`path`) or a catalog entry (`kind` + `id` / `resource_id`)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    path: str | None = None
    kind: str | None = None
    resource_id: str | None = Field(default=None, alias="id")
    version: str | None = None


class FeedbackArtifactRef(BaseModel):
    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    version: str | None = None


class FeedbackRequest(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    summary: str = Field(min_length=10)
    recommendations: list[str] = Field(default_factory=list)
    source_context: str | None = None
    artifact: FeedbackArtifactRef | None = None


class TelemetryEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    anonymous_id: str
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TelemetryBatch(BaseModel):
    events: list[TelemetryEvent] = Field(default_factory=list)


SENSITIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[redacted-email]"),
    (re.compile(r"\b(sk-[A-Za-z0-9]{20,})\b"), "[redacted-openai-key]"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"), "[redacted-github-token]"),
    (re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b"), "[redacted-slack-token]"),
    (re.compile(r"\b(lin_api_[A-Za-z0-9]{10,})\b"), "[redacted-linear-key]"),
    (re.compile(r"(?i)\b(password|passwd|secret|token)\s*[:=]\s*[^\s,;]+"), r"\1=[redacted]"),
]


def sanitize_text(text: str) -> tuple[str, int]:
    sanitized = text
    redactions = 0
    for pattern, replacement in SENSITIVE_PATTERNS:
        sanitized, count = pattern.subn(replacement, sanitized)
        redactions += count
    return sanitized, redactions


def safe_repo() -> tuple[str, str]:
    repo = os.getenv("SHIP_FEEDBACK_REPO", "ElMundiUA/ship").strip()
    if "/" not in repo:
        raise HTTPException(status_code=500, detail="SHIP_FEEDBACK_REPO must be in owner/repo format.")
    owner, name = repo.split("/", 1)
    return owner, name


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Open the async database engine eagerly so the first request doesn't
    pay for connection setup, and dispose it cleanly on shutdown.

    Phase 2.4 Step D retired ``/search`` and the methodology-corpus
    reindex that fed it; this lifespan no longer warms a pgvector cache.
    ``methodology_chunks`` rows live on as orphan data until a follow-up
    migration drops the table.
    """
    try:
        get_engine()
    except Exception:  # pragma: no cover — best-effort startup
        pass
    # In-process cron scheduler (KB-pipeline + future migrations off
    # ARQ). Best-effort — if APScheduler fails to start, the API still
    # serves requests; the cron worker container (where ARQ legacy
    # crons still live) covers the existing surface meanwhile.
    from backend.app.services.cron import stop_scheduler as _stop_scheduler

    try:
        from backend.app.services.cron import start_scheduler
        from backend.app.services.cron_jobs import register_all

        register_all()
        start_scheduler()
    except Exception:
        logging.getLogger("ship.cron").exception(
            "cron scheduler failed to start; KB-pipeline jobs will not fire "
            "on this replica until restart"
        )
    # Telegram bot long-poll. Lives inside the API process so the cloud
    # deployment doesn't need a separate worker container. A Postgres
    # advisory lock elects a single leader across replicas so Telegram
    # never sees more than one ``getUpdates`` client per bot token.
    bot_task: asyncio.Task[None] | None = None
    try:
        from backend.app.core.config import get_settings as _get_settings
        from backend.app.integrations.telegram.bot import run_with_leader_lock

        bot_task = asyncio.create_task(
            run_with_leader_lock(_get_settings()),
            name="ship.telegram.bot",
        )
    except Exception:
        logging.getLogger("ship.telegram.bot").exception(
            "telegram bot failed to start; API stays up"
        )
    try:
        yield
    finally:
        if bot_task is not None and not bot_task.done():
            bot_task.cancel()
            try:
                await bot_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        try:
            await _stop_scheduler()
        except Exception:  # pragma: no cover
            pass
        try:
            await dispose_engine()
        except Exception:  # pragma: no cover
            pass


app = FastAPI(title="Ship Methodology API", version="0.14.3", lifespan=lifespan)
app.include_router(v1_api_router)


@app.get("/healthz", tags=["meta"], include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Cheap, dependency-free liveness probe.

    Used by Caddy / k8s / docker-compose to decide "is this process up at
    all?". Distinct from ``/v1/health`` which round-trips Postgres and is
    therefore a *readiness* probe — useful for gating dependent services
    but too expensive to run every second.
    """
    return {"status": "ok"}


_KIND_DESCRIPTIONS = {
    "collection": "Agent rule collections sourced from artifacts/collections/agent-rules-<agent>/ARTIFACT.md.",
}


_kind_cache: dict[str, tuple[tuple[str, float, int], dict[str, Any]]] = {}


def _clear_manifest_cache() -> None:
    """Test helper: drop cached kind data so filesystem changes take effect."""
    _kind_cache.clear()


# YAML reserved indicators that PyYAML's scanner rejects as the start of a
# plain scalar. Authors frequently write things like `authors: [@scope/x]`,
# which is not strictly valid YAML, so we quote those tokens before parsing.
_YAML_RESERVED_PREFIXES = ("@", "`", "%")


def _normalize_inline_lists(raw: str) -> str:
    """Quote unquoted tokens inside flow lists that begin with reserved chars.

    Only touches values of the form `key: [a, @b, "c"]` (inline). Block-style
    sequences and nested mappings are left untouched.
    """

    def repl(match: re.Match[str]) -> str:
        head = match.group(1)
        body = match.group(2)
        items = []
        for raw_item in body.split(","):
            item = raw_item.strip()
            if not item:
                items.append("")
                continue
            if item[0] in {'"', "'"}:
                items.append(item)
                continue
            if item[0] in _YAML_RESERVED_PREFIXES:
                items.append('"' + item.replace('"', '\\"') + '"')
                continue
            items.append(item)
        return f"{head}[{', '.join(items)}]"

    return re.sub(r"^(\s*[\w.-]+\s*:\s*)\[([^\[\]\n]*)\]\s*$", repl, raw, flags=re.MULTILINE)


def _split_frontmatter(text: str, artifact_path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise HTTPException(
            status_code=500,
            detail=f"{artifact_path.relative_to(APP_ROOT)} is missing YAML frontmatter.",
        )
    end = text.find("\n---\n", 4)
    if end == -1:
        raise HTTPException(
            status_code=500,
            detail=f"{artifact_path.relative_to(APP_ROOT)} has unterminated frontmatter.",
        )
    raw = text[4:end]
    body = text[end + len("\n---\n"):]
    try:
        meta = yaml.safe_load(_normalize_inline_lists(raw)) or {}
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"{artifact_path.relative_to(APP_ROOT)} has invalid YAML frontmatter: {exc}",
        ) from exc
    if not isinstance(meta, dict):
        raise HTTPException(
            status_code=500,
            detail=f"{artifact_path.relative_to(APP_ROOT)} frontmatter must be a mapping.",
        )
    return meta, body


def _summary_from_description(description: str) -> str:
    text = (description or "").strip()
    if not text:
        return ""
    sentinel = ". "
    idx = text.find(sentinel)
    if idx == -1:
        return text
    return text[:idx].rstrip()


def _build_entry(meta: dict[str, Any], body: str, full: str, plural: str, artifact_path: Path) -> dict[str, Any]:
    artifact_id = meta.get("id")
    missing = [f for f in REQUIRED_ENTRY_FIELDS if f not in meta]
    if missing:
        raise HTTPException(
            status_code=500,
            detail=(
                f"artifacts/{plural}/{artifact_id}/ARTIFACT.md is missing "
                f"required fields: {', '.join(missing)}. "
                "Run scripts/stamp_artifact_versions.py."
            ),
        )
    description = meta.get("description") or ""
    if not isinstance(description, str):
        description = str(description)
    rel_path = f"artifacts/{plural}/{artifact_id}/ARTIFACT.md"
    spec = meta.get("spec") if isinstance(meta.get("spec"), dict) else {}
    return {
        "id": artifact_id,
        "title": meta.get("name"),
        "summary": _summary_from_description(description),
        "description": description,
        "path": rel_path,
        "tags": list(meta.get("tags") or []),
        "group": meta.get("group"),
        "version": meta.get("version"),
        "content_sha256": meta.get("content_sha256"),
        "updated_at": meta.get("updated_at"),
        "channel": meta.get("channel"),
        "min_shipctl": meta.get("min_shipctl"),
        "deprecated": bool(meta.get("deprecated", False)),
        "replaced_by": meta.get("replaced_by"),
        "yanked": bool(meta.get("yanked", False)),
        "spec": spec,
        "_body": body,
        "_full": full,
    }


def _kind_dir_signature(plural: str) -> tuple[str, float, int]:
    base = APP_ROOT / "artifacts" / plural
    if not base.is_dir():
        return (plural, 0.0, 0)
    try:
        listing = sorted(p.name for p in base.iterdir() if p.is_dir())
    except OSError:
        return (plural, 0.0, 0)
    mtime = base.stat().st_mtime
    for name in listing:
        artifact = base / name / "ARTIFACT.md"
        if artifact.is_file():
            mtime = max(mtime, artifact.stat().st_mtime)
    return (plural, mtime, len(listing))


def _load_kind(kind: str) -> dict[str, Any]:
    if kind not in ARTIFACT_KINDS:
        raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")
    plural = ARTIFACT_KINDS[kind][1]
    signature = _kind_dir_signature(plural)
    cached = _kind_cache.get(kind)
    if cached is not None and cached[0] == signature:
        return cached[1]

    base = APP_ROOT / "artifacts" / plural
    entries: list[dict[str, Any]] = []
    if base.is_dir():
        for child in sorted(base.iterdir(), key=lambda p: p.name):
            if not child.is_dir():
                continue
            artifact_path = child / "ARTIFACT.md"
            if not artifact_path.is_file():
                continue
            full = artifact_path.read_text(encoding="utf-8")
            meta, body = _split_frontmatter(full, artifact_path)
            if meta.get("id") != child.name:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"artifacts/{plural}/{child.name}/ARTIFACT.md frontmatter id "
                        f"`{meta.get('id')}` does not match folder name."
                    ),
                )
            entries.append(_build_entry(meta, body, full, plural, artifact_path))

    data = {
        "version": 2,
        "description": _KIND_DESCRIPTIONS.get(kind, ""),
        plural: entries,
    }
    _kind_cache[kind] = (signature, data)
    return data


def load_collections_manifest() -> dict[str, Any]:
    return _load_kind("collection")


MANIFEST_LOADERS = {
    "collection": load_collections_manifest,
}


def _catalog_item_by_id(items: Any, item_id: str) -> dict[str, Any] | None:
    if not isinstance(items, list):
        return None
    for entry in items:
        if isinstance(entry, dict) and entry.get("id") == item_id:
            return entry
    return None


def _filter_entries_by_channel(entries: list[dict[str, Any]], channel: str) -> list[dict[str, Any]]:
    channel = (channel or "stable").lower()
    if channel == "edge":
        return list(entries)
    return [e for e in entries if (e.get("channel") or "stable").lower() == "stable"]


def _entry_summary(entry: dict[str, Any], kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "id": entry.get("id"),
        "title": entry.get("title"),
        "summary": entry.get("summary"),
        "path": entry.get("path"),
        "tags": entry.get("tags") or [],
        "group": entry.get("group"),
        "version": entry.get("version"),
        "content_sha256": entry.get("content_sha256"),
        "updated_at": entry.get("updated_at"),
        "channel": entry.get("channel"),
        "min_shipctl": entry.get("min_shipctl"),
        "deprecated": bool(entry.get("deprecated", False)),
        "replaced_by": entry.get("replaced_by"),
        "yanked": bool(entry.get("yanked", False)),
    }


def read_repo_markdown(rel_path: str) -> str:
    """Read a single markdown/text file under APP_ROOT (manifest `path` targets)."""
    candidate = (APP_ROOT / rel_path).resolve()
    try:
        candidate.relative_to(APP_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path escapes repository root.") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    if candidate.suffix.lower() not in {".md", ".txt"}:
        raise HTTPException(status_code=400, detail="Only markdown or text files are allowed.")
    return candidate.read_text(encoding="utf-8", errors="ignore")


def _resolve_entry_with_version(
    kind: str,
    item_id: str,
    version: str | None,
) -> dict[str, Any]:
    loader = MANIFEST_LOADERS.get(kind)
    if loader is None:
        raise HTTPException(status_code=400, detail=f"Unknown kind: {kind}")
    _, array_key = ARTIFACT_KINDS[kind]
    data = loader()
    entry = _catalog_item_by_id(data.get(array_key), item_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown {kind} id.")
    current_version = entry.get("version")
    if version and version != current_version:
        raise HTTPException(
            status_code=404,
            detail=f"unknown version; current is {current_version}",
        )
    if entry.get("yanked"):
        replaced = entry.get("replaced_by")
        detail = f"replaced_by={replaced}" if replaced else "artifact yanked"
        raise HTTPException(status_code=410, detail=detail)
    return entry


def _full_entry_response(entry: dict[str, Any], kind: str) -> dict[str, Any]:
    full = entry.get("_full")
    if not isinstance(full, str) or not full:
        raise HTTPException(status_code=500, detail=f"{kind} entry has no body.")
    out = _entry_summary(entry, kind)
    out["content"] = full
    if entry.get("deprecated"):
        replaced = entry.get("replaced_by")
        out["deprecation_notice"] = (
            f"replaced_by={replaced}" if replaced else "deprecated"
        )
    return out


@app.get("/collections")
def list_collections(channel: str = Query(default="stable")) -> dict[str, Any]:
    data = load_collections_manifest()
    entries = [e for e in data.get("collections", []) if isinstance(e, dict)]
    filtered = _filter_entries_by_channel(entries, channel)
    return {
        "version": data.get("version", 1),
        "description": data.get("description", ""),
        "collections": [_entry_summary(e, "collection") for e in filtered],
    }


@app.get("/collections/{item_id}")
def get_collection(item_id: str, version: str | None = Query(default=None)) -> dict[str, Any]:
    entry = _resolve_entry_with_version("collection", item_id, version)
    return _full_entry_response(entry, "collection")


@app.get("/collections/{item_id}/versions")
def list_collection_versions(item_id: str) -> dict[str, Any]:
    return _versions_for_kind("collection", item_id)


def _versions_for_kind(kind: str, item_id: str) -> dict[str, Any]:
    loader = MANIFEST_LOADERS[kind]
    _, array_key = ARTIFACT_KINDS[kind]
    data = loader()
    entry = _catalog_item_by_id(data.get(array_key), item_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown {kind} id.")
    return {
        "id": entry.get("id"),
        "versions": [
            {
                "version": entry.get("version"),
                "updated_at": entry.get("updated_at"),
                "channel": entry.get("channel"),
                "deprecated": bool(entry.get("deprecated", False)),
                "yanked": bool(entry.get("yanked", False)),
            }
        ],
    }


@app.post("/fetch")
def fetch(req: FetchRequest) -> dict[str, Any]:
    """Full text: repo-relative markdown (`path`) or one catalog body (`kind` + `id`)."""
    path = (req.path or "").strip()
    rid = (req.resource_id or "").strip()
    kind = (req.kind or "").strip().lower() if req.kind else ""
    version = (req.version or "").strip() or None

    if kind and rid:
        if kind not in ARTIFACT_KINDS:
            raise HTTPException(
                status_code=400,
                detail=f"kind must be one of: {', '.join(sorted(ARTIFACT_KINDS))}",
            )
        entry = _resolve_entry_with_version(kind, rid, version)
        return _full_entry_response(entry, kind)

    if path:
        candidate = (APP_ROOT / path).resolve()
        try:
            candidate.relative_to(APP_ROOT.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Path is outside repository.") from exc
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail="File not found.")
        if candidate.suffix.lower() not in {".md", ".txt"}:
            raise HTTPException(status_code=400, detail="Only markdown/text files are fetchable.")
        content = candidate.read_text(encoding="utf-8", errors="ignore")
        return {
            "kind": "file",
            "path": str(candidate.relative_to(APP_ROOT)),
            "content": content,
        }

    raise HTTPException(
        status_code=400,
        detail='Provide "path" (repo-relative .md/.txt) or "kind" + "id" (catalog: pattern, tool, collection).',
    )


# --- Feedback --------------------------------------------------------------

# Injected by tests / configurable at runtime; falls back to a real AsyncClient.
_FEEDBACK_HTTP_CLIENT_FACTORY: Any = None


def _feedback_http_client() -> httpx.AsyncClient:
    if _FEEDBACK_HTTP_CLIENT_FACTORY is not None:
        return _FEEDBACK_HTTP_CLIENT_FACTORY()
    return httpx.AsyncClient(timeout=20.0)


def _github_labels_for_artifact(artifact: FeedbackArtifactRef | None) -> list[str]:
    labels = ["feedback", "retro"]
    if artifact is None:
        return labels
    labels.append(f"artifact:{artifact.kind}:{artifact.id}")
    if artifact.version:
        labels.append(f"version:{artifact.version}")
    return labels


@app.post("/feedback")
async def feedback(req: FeedbackRequest) -> dict[str, Any]:
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if not github_token:
        raise HTTPException(status_code=500, detail="GITHUB_TOKEN is required for feedback endpoint.")

    owner, repo = safe_repo()

    rec_block = "\n".join([f"- {item}" for item in req.recommendations]) or "- No recommendations provided."
    artifact_block = ""
    meta_footer = ""
    labels = _github_labels_for_artifact(req.artifact)
    if req.artifact:
        artifact_block = (
            "### Artifact\n"
            f"{req.artifact.kind}:{req.artifact.id}"
            + (f"@{req.artifact.version}" if req.artifact.version else "")
            + "\n\n"
        )
        meta_footer = (
            "\n\n---\n<!-- ship-feedback-meta: "
            + json.dumps(
                {
                    "kind": req.artifact.kind,
                    "id": req.artifact.id,
                    "version": req.artifact.version,
                },
                ensure_ascii=False,
            )
            + " -->\n"
        )

    raw_body = (
        "## Retro feedback from Ship backend\n\n"
        f"{artifact_block}"
        f"### Summary\n{req.summary}\n\n"
        f"### Recommendations\n{rec_block}\n\n"
        f"### Source context\n{req.source_context or 'N/A'}\n"
    )

    safe_title, t_red = sanitize_text(req.title)
    safe_body, b_red = sanitize_text(raw_body)
    redactions = t_red + b_red
    if redactions:
        safe_body = (
            "_Sensitive fragments were detected and generalized before issue creation._\n\n"
            + safe_body
        )
    safe_body = safe_body + meta_footer

    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    async with _feedback_http_client() as client:
        # Dedup: look for an existing open issue carrying the artifact+version labels.
        dedup_labels = [lab for lab in labels if lab.startswith("artifact:") or lab.startswith("version:")]
        deduplicated = False
        existing_issue: dict[str, Any] | None = None
        if req.artifact and dedup_labels:
            list_url = f"https://api.github.com/repos/{owner}/{repo}/issues"
            resp = await client.get(
                list_url,
                headers=headers,
                params={"labels": ",".join(dedup_labels), "state": "open"},
            )
            if resp.status_code < 300:
                try:
                    items = resp.json()
                except ValueError:
                    items = []
                if isinstance(items, list) and items:
                    existing_issue = items[0]

        if existing_issue is not None:
            issue_number = existing_issue.get("number")
            comment_url = (
                f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
            )
            resp = await client.post(
                comment_url,
                headers=headers,
                json={"body": safe_body},
            )
            if resp.status_code >= 300:
                raise HTTPException(status_code=502, detail=f"GitHub comment failed: {resp.text}")
            deduplicated = True
            return {
                "issue_url": existing_issue.get("html_url"),
                "issue_number": issue_number,
                "labels": existing_issue.get("labels") or labels,
                "redactions_applied": redactions,
                "deduplicated": True,
            }

        create_url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        payload = {"title": safe_title, "body": safe_body, "labels": labels}
        resp = await client.post(create_url, headers=headers, json=payload)
        if resp.status_code >= 300:
            raise HTTPException(status_code=502, detail=f"GitHub issue creation failed: {resp.text}")
        data = resp.json()

    return {
        "issue_url": data.get("html_url"),
        "issue_number": data.get("number"),
        "labels": labels,
        "redactions_applied": redactions,
        "deduplicated": deduplicated,
    }


# --- Telemetry -------------------------------------------------------------

_TELEMETRY_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_TELEMETRY_RATE_WINDOW_SEC = 60.0
_TELEMETRY_RATE_LIMIT = 60
_TELEMETRY_MAX_BATCH = 100


def _rate_limit_check(anon_id: str) -> bool:
    now = time.monotonic()
    bucket = _TELEMETRY_BUCKETS[anon_id]
    while bucket and (now - bucket[0]) > _TELEMETRY_RATE_WINDOW_SEC:
        bucket.popleft()
    if len(bucket) >= _TELEMETRY_RATE_LIMIT:
        return False
    bucket.append(now)
    return True


def _reset_rate_limits() -> None:
    """Test helper."""
    _TELEMETRY_BUCKETS.clear()


def _find_denied_keys(obj: Any) -> list[str]:
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and key.lower() in TELEMETRY_PAYLOAD_DENYLIST:
                    found.append(key)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return found


def _telemetry_file_path() -> Path:
    override = os.getenv("SHIP_TELEMETRY_DIR", "").strip()
    if override:
        base = Path(override)
        if not base.is_absolute():
            base = APP_ROOT / base
        base.mkdir(parents=True, exist_ok=True)
        return base / "events.jsonl"
    TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    return TELEMETRY_FILE


@app.post("/telemetry")
def telemetry(batch: TelemetryBatch, response: Response) -> dict[str, Any]:
    if len(batch.events) == 0:
        return {"accepted": 0, "rejected": 0, "reasons": []}
    if len(batch.events) > _TELEMETRY_MAX_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"max {_TELEMETRY_MAX_BATCH} events per request",
        )

    # Validate anonymous_id + rate limit on the first anon id in the batch.
    # All events are expected to share the same anonymous_id; enforce it.
    anon_ids = {ev.anonymous_id for ev in batch.events}
    if len(anon_ids) != 1:
        raise HTTPException(status_code=400, detail="all events must share one anonymous_id")
    anon_id = next(iter(anon_ids))
    if not UUID4_RE.match(anon_id or ""):
        raise HTTPException(status_code=400, detail="anonymous_id must be a UUIDv4")

    if not _rate_limit_check(anon_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")

    accepted = 0
    rejected = 0
    reasons: list[str] = []
    to_write: list[dict[str, Any]] = []
    received_at = datetime.now(tz=timezone.utc).isoformat()

    for event in batch.events:
        if event.type not in ALLOWED_TELEMETRY_TYPES:
            rejected += 1
            reasons.append(f"type:{event.type}:unknown")
            continue
        denied = _find_denied_keys(event.payload)
        if denied:
            raise HTTPException(
                status_code=400,
                detail=f"payload contains denied keys: {', '.join(sorted(set(denied)))}",
            )
        record = {
            "type": event.type,
            "anonymous_id": event.anonymous_id,
            "timestamp": event.timestamp,
            "payload": event.payload,
            "received_at": received_at,
        }
        to_write.append(record)
        accepted += 1

    if to_write:
        path = _telemetry_file_path()
        with path.open("a", encoding="utf-8") as fh:
            for record in to_write:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    response.status_code = 202
    return {"accepted": accepted, "rejected": rejected, "reasons": reasons}


@app.delete("/telemetry/{anonymous_id}")
def telemetry_delete(
    anonymous_id: str,
    x_ship_confirm: str | None = Header(default=None, alias="X-Ship-Confirm"),
) -> dict[str, Any]:
    if not UUID4_RE.match(anonymous_id or ""):
        raise HTTPException(status_code=400, detail="anonymous_id must be a UUIDv4")
    if (x_ship_confirm or "").lower() != "yes":
        raise HTTPException(status_code=400, detail="X-Ship-Confirm: yes header required")

    path = _telemetry_file_path()
    if not path.is_file():
        return {"deleted": 0}

    kept: list[str] = []
    deleted = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if record.get("anonymous_id") == anonymous_id:
                deleted += 1
            else:
                kept.append(line)

    with path.open("w", encoding="utf-8") as fh:
        for line in kept:
            fh.write(line + "\n")
    return {"deleted": deleted}


@app.get("/telemetry/{anonymous_id}/export")
def telemetry_export(anonymous_id: str) -> dict[str, Any]:
    if not UUID4_RE.match(anonymous_id or ""):
        raise HTTPException(status_code=400, detail="anonymous_id must be a UUIDv4")
    path = _telemetry_file_path()
    if not path.is_file():
        return {"events": []}
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("anonymous_id") == anonymous_id:
                events.append(record)
    return {"events": events}
