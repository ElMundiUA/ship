from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import chromadb
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


APP_ROOT = Path(__file__).resolve().parents[2]
CHROMA_DIR = APP_ROOT / "backend" / ".chroma"
MANIFEST_PATH = CHROMA_DIR / "manifest.json"
COLLECTION_NAME = "ship_methodology"
DEFAULT_PATHS = ("documentation", "prompts", "README.md")
PATTERNS_MANIFEST_PATH = APP_ROOT / "patterns" / "manifest.json"


class SearchRequest(BaseModel):
    query: str = Field(min_length=3)
    top_k: int = Field(default=8, ge=1, le=30)


class FetchRequest(BaseModel):
    path: str


class FeedbackRequest(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    summary: str = Field(min_length=10)
    recommendations: list[str] = Field(default_factory=list)
    source_context: str | None = None


class IndexStore:
    def __init__(self) -> None:
        self.client: chromadb.ClientAPI | None = None
        self.collection: Any | None = None

    def _embedding_function(self):
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise HTTPException(status_code=500, detail="OPENAI_API_KEY is required for vector search.")
        return chromadb.utils.embedding_functions.OpenAIEmbeddingFunction(
            api_key=api_key,
            model_name=os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
        )

    def _allowed_files(self) -> list[Path]:
        files: list[Path] = []
        for entry in DEFAULT_PATHS:
            candidate = APP_ROOT / entry
            if candidate.is_file():
                files.append(candidate)
            elif candidate.is_dir():
                files.extend(sorted(candidate.rglob("*.md")))
        return [p for p in files if p.is_file()]

    def _fingerprint(self, path: Path) -> str:
        data = path.read_bytes()
        return hashlib.sha256(data).hexdigest()

    def _build_manifest(self, files: list[Path]) -> dict[str, str]:
        return {str(f.relative_to(APP_ROOT)): self._fingerprint(f) for f in files}

    def _chunk_text(self, text: str, chunk_size: int = 1200, overlap: int = 180) -> list[str]:
        clean = re.sub(r"\s+\n", "\n", text).strip()
        if len(clean) <= chunk_size:
            return [clean] if clean else []
        chunks: list[str] = []
        start = 0
        while start < len(clean):
            end = min(len(clean), start + chunk_size)
            chunks.append(clean[start:end])
            if end == len(clean):
                break
            start = max(0, end - overlap)
        return chunks

    def _needs_reindex(self, new_manifest: dict[str, str]) -> bool:
        if os.getenv("FORCE_REINDEX", "").lower() in {"1", "true", "yes"}:
            return True
        if not MANIFEST_PATH.exists():
            return True
        try:
            old_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return True
        return old_manifest != new_manifest

    def _write_manifest(self, manifest: dict[str, str]) -> None:
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")

    def ensure_ready(self) -> None:
        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(CHROMA_DIR))
        embedding = self._embedding_function()
        files = self._allowed_files()
        manifest = self._build_manifest(files)

        if self._needs_reindex(manifest):
            try:
                self.client.delete_collection(COLLECTION_NAME)
            except Exception:
                pass
            self.collection = self.client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=embedding,
                metadata={"hnsw:space": "cosine"},
            )
            ids: list[str] = []
            docs: list[str] = []
            metas: list[dict[str, Any]] = []
            for file_path in files:
                rel = str(file_path.relative_to(APP_ROOT))
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                for idx, chunk in enumerate(self._chunk_text(text)):
                    ids.append(f"{rel}::chunk-{idx}")
                    docs.append(chunk)
                    metas.append({"path": rel, "chunk_index": idx})
            if docs:
                self.collection.add(ids=ids, documents=docs, metadatas=metas)
            self._write_manifest(manifest)
        else:
            self.collection = self.client.get_or_create_collection(
                name=COLLECTION_NAME,
                embedding_function=embedding,
                metadata={"hnsw:space": "cosine"},
            )

    def search(self, query: str, top_k: int) -> list[dict[str, Any]]:
        if not self.collection:
            raise HTTPException(status_code=500, detail="Vector index is not initialized.")
        raw = self.collection.query(
            query_texts=[query],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )
        results: list[dict[str, Any]] = []
        docs = raw.get("documents", [[]])[0]
        metas = raw.get("metadatas", [[]])[0]
        distances = raw.get("distances", [[]])[0]
        ids = raw.get("ids", [[]])[0]
        for i, doc in enumerate(docs):
            meta = metas[i] if i < len(metas) else {}
            distance = distances[i] if i < len(distances) else None
            rid = ids[i] if i < len(ids) else None
            snippet = doc[:260].replace("\n", " ").strip()
            results.append(
                {
                    "id": rid,
                    "path": meta.get("path"),
                    "chunk_index": meta.get("chunk_index"),
                    "distance": distance,
                    "snippet": snippet,
                }
            )
        return results


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


index_store = IndexStore()
app = FastAPI(title="Ship Methodology API", version="0.1.0")

_patterns_cache: dict[str, Any] | None = None


def load_patterns_manifest() -> dict[str, Any]:
    global _patterns_cache
    if _patterns_cache is not None:
        return _patterns_cache
    if not PATTERNS_MANIFEST_PATH.is_file():
        _patterns_cache = {"version": 0, "patterns": [], "description": ""}
        return _patterns_cache
    try:
        data = json.loads(PATTERNS_MANIFEST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="patterns/manifest.json is invalid JSON.") from exc
    if not isinstance(data.get("patterns"), list):
        raise HTTPException(status_code=500, detail="patterns manifest must contain a patterns array.")
    _patterns_cache = data
    return _patterns_cache


def pattern_by_id(pattern_id: str) -> dict[str, Any] | None:
    data = load_patterns_manifest()
    for p in data["patterns"]:
        if isinstance(p, dict) and p.get("id") == pattern_id:
            return p
    return None


def read_pattern_markdown(rel_path: str) -> str:
    candidate = (APP_ROOT / rel_path).resolve()
    try:
        candidate.relative_to(APP_ROOT.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path escapes repository root.") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Pattern file not found.")
    if candidate.suffix.lower() not in {".md", ".txt"}:
        raise HTTPException(status_code=400, detail="Only markdown or text patterns are allowed.")
    return candidate.read_text(encoding="utf-8", errors="ignore")


@app.on_event("startup")
def startup() -> None:
    # Allow API boot without OPENAI_API_KEY; /search will fail with explicit error until configured.
    try:
        index_store.ensure_ready()
    except HTTPException:
        pass


@app.get("/patterns")
def list_patterns() -> dict[str, Any]:
    """Curated org patterns (manifest entries without full file bodies)."""
    data = load_patterns_manifest()
    slim = []
    for p in data.get("patterns", []):
        if not isinstance(p, dict):
            continue
        slim.append(
            {
                "id": p.get("id"),
                "title": p.get("title"),
                "summary": p.get("summary"),
                "path": p.get("path"),
                "tags": p.get("tags") or [],
                "group": p.get("group"),
            }
        )
    return {"version": data.get("version", 1), "description": data.get("description", ""), "patterns": slim}


@app.get("/patterns/{pattern_id}")
def get_pattern(pattern_id: str) -> dict[str, Any]:
    """Single pattern metadata plus full markdown body."""
    meta = pattern_by_id(pattern_id)
    if not meta:
        raise HTTPException(status_code=404, detail="Unknown pattern id.")
    rel = meta.get("path")
    if not isinstance(rel, str) or not rel.strip():
        raise HTTPException(status_code=500, detail="Pattern entry has no path.")
    content = read_pattern_markdown(rel)
    return {
        "id": meta.get("id"),
        "title": meta.get("title"),
        "summary": meta.get("summary"),
        "path": rel,
        "tags": meta.get("tags") or [],
        "group": meta.get("group"),
        "content": content,
    }


@app.post("/search")
def search(req: SearchRequest) -> dict[str, Any]:
    if not index_store.collection:
        index_store.ensure_ready()
    return {"query": req.query, "results": index_store.search(req.query, req.top_k)}


@app.post("/fetch")
def fetch(req: FetchRequest) -> dict[str, Any]:
    candidate = (APP_ROOT / req.path).resolve()
    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    try:
        candidate.relative_to(APP_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Path is outside repository.") from exc
    if candidate.suffix.lower() not in {".md", ".txt"}:
        raise HTTPException(status_code=400, detail="Only markdown/text files are fetchable.")
    content = candidate.read_text(encoding="utf-8", errors="ignore")
    return {"path": str(candidate.relative_to(APP_ROOT)), "content": content}


@app.post("/feedback")
async def feedback(req: FeedbackRequest) -> dict[str, Any]:
    github_token = os.getenv("GITHUB_TOKEN", "").strip()
    if not github_token:
        raise HTTPException(status_code=500, detail="GITHUB_TOKEN is required for feedback endpoint.")

    owner, repo = safe_repo()

    rec_block = "\n".join([f"- {item}" for item in req.recommendations]) or "- No recommendations provided."
    raw_body = (
        "## Retro feedback from Ship backend\n\n"
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

    url = f"https://api.github.com/repos/{owner}/{repo}/issues"
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"title": safe_title, "body": safe_body, "labels": ["feedback", "retro"]}

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
    if resp.status_code >= 300:
        raise HTTPException(status_code=502, detail=f"GitHub issue creation failed: {resp.text}")
    data = resp.json()
    return {
        "issue_url": data.get("html_url"),
        "issue_number": data.get("number"),
        "redactions_applied": redactions,
    }
