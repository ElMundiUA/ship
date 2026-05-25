"""K6a — S3 (DO Spaces) knowledge writer + repo_intel emit.

Offline: the writer test mocks aioboto3.Session; the repo_intel emit
tests monkeypatch the writer + renderer so no DB / network / boto.
"""

from __future__ import annotations

import types
import uuid

import pytest

import backend.app.integrations.lighthouse.s3_writer as s3w
import backend.app.services.repo_intel as ri
from backend.app.integrations.lighthouse.s3_writer import (
    KnowledgeS3Writer,
    build_knowledge_s3_writer,
)


# ---- build gate -------------------------------------------------------


def test_build_writer_disabled_without_credentials() -> None:
    cfg = types.SimpleNamespace(
        s3_bucket="ship-documents",
        s3_access_key=None,
        s3_secret_key=None,
        s3_endpoint_url=None,
        s3_region="fra1",
    )
    assert build_knowledge_s3_writer(cfg) is None


def test_build_writer_enabled_with_credentials() -> None:
    cfg = types.SimpleNamespace(
        s3_bucket="ship-documents",
        s3_access_key="AKIA",
        s3_secret_key="secret",
        s3_endpoint_url="https://fra1.digitaloceanspaces.com",
        s3_region="fra1",
    )
    writer = build_knowledge_s3_writer(cfg)
    assert isinstance(writer, KnowledgeS3Writer)


# ---- write_document (mock aioboto3) -----------------------------------


class _FakeS3:
    def __init__(self, captured: dict):
        self._captured = captured

    async def put_object(self, **kwargs):
        self._captured["put"] = kwargs


class _FakeClientCM:
    def __init__(self, captured: dict):
        self._captured = captured

    async def __aenter__(self):
        return _FakeS3(self._captured)

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, captured: dict):
        self._captured = captured

    def client(self, service, **kwargs):
        self._captured["service"] = service
        self._captured["client_kwargs"] = kwargs
        return _FakeClientCM(self._captured)


@pytest.mark.asyncio
async def test_write_document_puts_to_workspace_prefixed_key(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(s3w.aioboto3, "Session", lambda: _FakeSession(captured))

    writer = KnowledgeS3Writer(
        bucket="ship-documents",
        endpoint_url="https://fra1.digitaloceanspaces.com",
        access_key="AKIA",
        secret_key="secret",
        region="fra1",
    )
    ws = uuid.uuid4()
    key = await writer.write_document(
        workspace_id=ws, source="repo-intel", name="ks-api", markdown="# hi\nbody"
    )

    assert key == f"{ws}/repo-intel/ks-api.md"
    assert captured["service"] == "s3"
    assert captured["client_kwargs"]["endpoint_url"] == (
        "https://fra1.digitaloceanspaces.com"
    )
    put = captured["put"]
    assert put["Bucket"] == "ship-documents"
    assert put["Key"] == f"{ws}/repo-intel/ks-api.md"
    assert put["Body"] == b"# hi\nbody"
    assert put["ContentType"].startswith("text/markdown")


# ---- repo_intel emit --------------------------------------------------


class _FakeWriter:
    def __init__(self, *, exc=None):
        self._exc = exc
        self.calls: list[dict] = []

    async def write_document(self, *, workspace_id, source, name, markdown):
        self.calls.append(
            {
                "workspace_id": workspace_id,
                "source": source,
                "name": name,
                "markdown": markdown,
            }
        )
        if self._exc is not None:
            raise self._exc
        return f"{workspace_id}/{source}/{name}.md"


def _fake_repo():
    return types.SimpleNamespace(
        full_name="ks/api", workspace_id=uuid.uuid4(), id=uuid.uuid4()
    )


def _patch_render(monkeypatch):
    monkeypatch.setattr(
        ri,
        "_render_repo_context_articles",
        lambda *, intel, repo: [
            ("overview", "Overview", "# Overview\nalpha"),
            ("architecture", "Architecture", "# Architecture\nbeta"),
        ],
    )


def test_consolidate_joins_sections() -> None:
    doc = ri._consolidate_intel_document(
        repo=_fake_repo(),
        articles=[("a", "A", "# A\nx"), ("b", "B", "# B\ny")],
    )
    assert doc == "# A\nx\n\n# B\ny\n"


@pytest.mark.asyncio
async def test_emit_ships_one_document_when_configured(monkeypatch):
    _patch_render(monkeypatch)
    writer = _FakeWriter()
    monkeypatch.setattr(
        "backend.app.integrations.lighthouse.build_knowledge_s3_writer",
        lambda settings: writer,
    )
    repo = _fake_repo()
    await ri._emit_intel_to_lighthouse(repo=repo, intel=types.SimpleNamespace())

    assert len(writer.calls) == 1
    call = writer.calls[0]
    assert call["source"] == "repo-intel"
    assert call["name"] == ri._slugify_repo("ks/api")
    assert call["workspace_id"] == repo.workspace_id
    # One consolidated document with both sections.
    assert "# Overview" in call["markdown"]
    assert "# Architecture" in call["markdown"]


@pytest.mark.asyncio
async def test_emit_is_noop_when_s3_disabled(monkeypatch):
    _patch_render(monkeypatch)
    monkeypatch.setattr(
        "backend.app.integrations.lighthouse.build_knowledge_s3_writer",
        lambda settings: None,
    )
    # No raise, no writer — just returns.
    await ri._emit_intel_to_lighthouse(
        repo=_fake_repo(), intel=types.SimpleNamespace()
    )


@pytest.mark.asyncio
async def test_emit_swallows_writer_errors(monkeypatch):
    _patch_render(monkeypatch)
    writer = _FakeWriter(exc=RuntimeError("spaces down"))
    monkeypatch.setattr(
        "backend.app.integrations.lighthouse.build_knowledge_s3_writer",
        lambda settings: writer,
    )
    # Harvest must survive a knowledge-engine outage.
    await ri._emit_intel_to_lighthouse(
        repo=_fake_repo(), intel=types.SimpleNamespace()
    )
    assert len(writer.calls) == 1
