"""Phase 3b — attachment → LLM content-block tests.

The route-level multipart flow is exercised end-to-end with a real
session in CI once attachments make it past pytest-async collection;
here we pin the pure builder logic so the wire shape can't drift
without a test review.

Three concerns:

* Each kind (image / pdf / text) renders into the right block type
  for Anthropic AND for OpenAI.
* Text uploads inline the body (with the ``[Attached file: ...]``
  frame so the LLM knows the bytes are operator-supplied, not
  inline prose).
* Unknown kinds emit no block — the message still goes through, the
  attachment is just dropped. (The route layer wouldn't accept an
  unknown kind in the first place, but the builder shouldn't crash
  on one if it ever leaks through.)
"""

from __future__ import annotations

import base64


def _att(kind: str, mime: str, data: bytes, text: str | None = None):
    from backend.app.services.agent.client import MessageAttachment

    return MessageAttachment(
        kind=kind,
        mime=mime,
        filename="example",
        data=data,
        extracted_text=text,
    )


def test_anthropic_renders_image_as_base64_block() -> None:
    from backend.app.services.agent.client import _anthropic_attachment_block

    block = _anthropic_attachment_block(_att("image", "image/png", b"PNG-BYTES"))
    assert block is not None
    assert block["type"] == "image"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "image/png"
    assert (
        base64.b64decode(block["source"]["data"]) == b"PNG-BYTES"
    )


def test_anthropic_renders_pdf_as_document_block() -> None:
    from backend.app.services.agent.client import _anthropic_attachment_block

    block = _anthropic_attachment_block(_att("pdf", "application/pdf", b"%PDF-1.4..."))
    assert block is not None
    assert block["type"] == "document"
    assert block["source"]["media_type"] == "application/pdf"


def test_anthropic_renders_text_as_inline_text_block() -> None:
    """Text uploads inline with a clear ``[Attached file: ...]``
    header so the LLM doesn't confuse them with the operator's
    typed message body."""
    from backend.app.services.agent.client import _anthropic_attachment_block

    block = _anthropic_attachment_block(
        _att("text", "text/markdown", b"# hello\nworld", text="# hello\nworld")
    )
    assert block is not None
    assert block["type"] == "text"
    assert "[Attached file:" in block["text"]
    assert "hello" in block["text"]


def test_anthropic_returns_none_for_unknown_kind() -> None:
    """Bug-defence — an unknown kind shouldn't crash the message
    assembly; we just drop the block."""
    from backend.app.services.agent.client import _anthropic_attachment_block

    block = _anthropic_attachment_block(_att("video", "video/mp4", b"\x00\x00"))
    assert block is None


def test_openai_renders_image_as_image_url_part() -> None:
    from backend.app.services.agent.client import _openai_attachment_block

    block = _openai_attachment_block(_att("image", "image/jpeg", b"JPEG-BYTES"))
    assert block is not None
    assert block["type"] == "image_url"
    url = block["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    payload = url.split(",", 1)[1]
    assert base64.b64decode(payload) == b"JPEG-BYTES"


def test_openai_renders_pdf_as_text_with_extracted_body() -> None:
    """OpenAI chat.completions has no document type; we fall back to
    a text part carrying the pypdf-extracted body. The frame
    ensures the LLM knows the content came from a file."""
    from backend.app.services.agent.client import _openai_attachment_block

    block = _openai_attachment_block(
        _att("pdf", "application/pdf", b"%PDF...", text="page 1 content\npage 2 content")
    )
    assert block is not None
    assert block["type"] == "text"
    assert "[Attached file:" in block["text"]
    assert "page 1 content" in block["text"]


def test_openai_pdf_without_extracted_text_yields_stub() -> None:
    """Encrypted / image-only PDF → pypdf returns nothing. Builder
    surfaces a stub so the LLM at least knows an upload was
    attempted — silently swallowing it would let the model deny
    the user uploaded anything."""
    from backend.app.services.agent.client import _openai_attachment_block

    block = _openai_attachment_block(_att("pdf", "application/pdf", b"%PDF..."))
    assert block is not None
    assert block["type"] == "text"
    assert "no text extractable" in block["text"]


def test_anthropic_messages_with_attachments_emits_blocks_array() -> None:
    """Round-trip through ``_anthropic_messages`` — a user turn with
    one image + a body produces a single Anthropic message whose
    ``content`` is a list with [image block, text block]."""
    from backend.app.services.agent.client import (
        ChatMessage,
        _anthropic_messages,
    )

    msg = ChatMessage(
        role="user",
        content="look at this",
        attachments=[_att("image", "image/png", b"PNG")],
    )
    _system, history = _anthropic_messages([msg])
    assert len(history) == 1
    blocks = history[0]["content"]
    assert isinstance(blocks, list)
    assert blocks[0]["type"] == "image"
    assert blocks[1] == {"type": "text", "text": "look at this"}


def test_openai_messages_with_attachments_emits_parts_array() -> None:
    from backend.app.services.agent.client import (
        ChatMessage,
        _openai_messages,
    )

    msg = ChatMessage(
        role="user",
        content="look at this",
        attachments=[_att("image", "image/png", b"PNG")],
    )
    rendered = _openai_messages([msg])
    assert len(rendered) == 1
    parts = rendered[0]["content"]
    assert isinstance(parts, list)
    assert parts[0]["type"] == "image_url"
    assert parts[1] == {"type": "text", "text": "look at this"}
