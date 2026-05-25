"""K6b — agent records → Lighthouse documents.

Offline: the emitter test monkeypatches the writer; the document-builder
tests use lightweight stand-ins (no DB / network / boto).
"""

from __future__ import annotations

import types
import uuid

import pytest

import backend.app.integrations.lighthouse.s3_writer as s3w
from backend.app.api.v1.routes.clarifications import _clarification_document
from backend.app.api.v1.routes.inbox import _inbox_comment_document


class _FakeWriter:
    def __init__(self, *, exc=None):
        self._exc = exc
        self.calls: list[tuple] = []

    async def write_document(self, *, workspace_id, source, name, markdown):
        self.calls.append((str(workspace_id), source, name, markdown))
        if self._exc is not None:
            raise self._exc
        return f"{workspace_id}/{source}/{name}.md"


# ---- shared emitter ---------------------------------------------------


@pytest.mark.asyncio
async def test_emit_noop_when_s3_disabled(monkeypatch):
    monkeypatch.setattr(s3w, "build_knowledge_s3_writer", lambda settings: None)
    ok = await s3w.emit_knowledge_document(
        workspace_id=uuid.uuid4(),
        source="clarifications",
        name="x",
        markdown="# x",
        settings=object(),
    )
    assert ok is False


@pytest.mark.asyncio
async def test_emit_writes_when_configured(monkeypatch):
    fake = _FakeWriter()
    monkeypatch.setattr(s3w, "build_knowledge_s3_writer", lambda settings: fake)
    ws = uuid.uuid4()
    ok = await s3w.emit_knowledge_document(
        workspace_id=ws,
        source="clarifications",
        name="abc",
        markdown="# doc",
        settings=object(),
    )
    assert ok is True
    assert fake.calls == [(str(ws), "clarifications", "abc", "# doc")]


@pytest.mark.asyncio
async def test_emit_swallows_writer_errors(monkeypatch):
    monkeypatch.setattr(
        s3w,
        "build_knowledge_s3_writer",
        lambda settings: _FakeWriter(exc=RuntimeError("spaces down")),
    )
    ok = await s3w.emit_knowledge_document(
        workspace_id=uuid.uuid4(),
        source="inbox-comments",
        name="n",
        markdown="m",
        settings=object(),
    )
    assert ok is False


# ---- document builders ------------------------------------------------


def test_clarification_document_has_qa_and_provenance() -> None:
    row = types.SimpleNamespace(
        ticket_ref="ENG-42",
        tracker_issue_url="https://linear.app/x/ENG-42",
        answered_at=None,
        question="How do we auth SPAs?",
        answer="Use PKCE; no implicit flow.",
    )
    doc = _clarification_document(row)
    assert doc.startswith("# Clarification")
    assert "ENG-42" in doc
    assert "https://linear.app/x/ENG-42" in doc
    assert "## Question" in doc and "How do we auth SPAs?" in doc
    assert "## Answer" in doc and "Use PKCE" in doc


def test_inbox_comment_document_includes_title_and_body() -> None:
    item = types.SimpleNamespace(id=uuid.uuid4(), title="Deploy failed on prod")
    doc = _inbox_comment_document(item, "Restarted the runner; root cause was OOM.")
    assert doc.startswith("# Inbox comment")
    assert str(item.id) in doc
    assert "Deploy failed on prod" in doc
    assert "Restarted the runner" in doc
