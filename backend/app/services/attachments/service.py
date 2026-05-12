"""High-level attachment lifecycle helpers.

The chat-stream route should never have to touch storage, policy,
text-extraction, or DB rows directly — it calls
:func:`persist_attachment` with the multipart bytes and gets back
a populated :class:`ChatAttachment` row attached to the active
chat message.

Extraction is opt-in per kind:

* **image**: no extraction in v1. Claude's vision pathway reads
  the pixels directly. A later OCR pass can backfill
  ``extracted_text`` so chat history becomes text-searchable.
* **pdf**: pypdf inline extraction at upload time. Cheap (<200ms
  for typical 10-page PDF), no external deps. The text becomes
  the body the LLM sees on non-vision providers.
* **text**: identity copy.
"""

from __future__ import annotations

import io
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.agent_surface import ChatAttachment
from backend.app.services.attachments.policy import (
    AttachmentPolicyError,
    classify_kind,
    validate_upload,
)
from backend.app.services.attachments.storage import (
    AttachmentStorage,
    get_default_storage,
)


logger = logging.getLogger(__name__)


class AttachmentPersistError(RuntimeError):
    """Raised when the upload passed policy but persistence failed
    (storage I/O error, DB constraint, etc.). The route maps this
    to a 500-class response."""


async def persist_attachment(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    message_id: uuid.UUID,
    filename: str,
    mime: str,
    data: bytes,
    storage: AttachmentStorage | None = None,
) -> ChatAttachment:
    """Validate, store, extract, and INSERT the row.

    Caller is responsible for the message-level ``MAX_FILES_*`` /
    total-size caps — those need cross-file context this helper
    doesn't have. ``validate_upload`` here covers the per-file
    rules.

    Returns the unflushed ChatAttachment ORM row; the caller owns
    the outer transaction (chat-stream route).
    """
    validate_upload(filename=filename, mime=mime, size_bytes=len(data))
    kind = classify_kind(mime)

    backend = storage or get_default_storage()
    attachment_id = uuid.uuid4()
    try:
        storage_path = await backend.write(
            workspace_id=workspace_id,
            attachment_id=attachment_id,
            data=data,
        )
    except OSError as exc:
        raise AttachmentPersistError(f"storage write failed: {exc}") from exc

    extracted_text: str | None = None
    extracted_text_source: str | None = None
    if kind == "pdf":
        extracted_text, extracted_text_source = _extract_pdf_text(data)
    elif kind == "text":
        # Strict UTF-8 with errors='replace' so a stray byte doesn't
        # block the upload. We're attaching prose / code / config —
        # invalid sequences are operator-side noise, not security
        # bytes worth refusing.
        extracted_text = data.decode("utf-8", errors="replace")
        extracted_text_source = "identity"

    row = ChatAttachment(
        id=attachment_id,
        message_id=message_id,
        workspace_id=workspace_id,
        kind=kind,
        mime=mime,
        filename=filename[:255],
        size_bytes=len(data),
        storage_path=storage_path,
        extracted_text=extracted_text,
        extracted_text_source=extracted_text_source,
    )
    session.add(row)
    return row


def _extract_pdf_text(data: bytes) -> tuple[str | None, str | None]:
    """Best-effort PDF → text via pypdf.

    Encrypted / image-only / malformed PDFs return ``(None, None)``
    so the upload still succeeds (the LLM still has the bytes via
    vision on Claude); the chat just doesn't have searchable text
    for that attachment.
    """
    try:
        # Local import — pypdf pulls in a sizable C extension and we
        # don't want it on the hot import path of every backend
        # module.
        from pypdf import PdfReader
    except ImportError:
        logger.warning(
            "pypdf is not installed; PDF text extraction disabled."
        )
        return None, None

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return None, None
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 — pypdf bubbles odd errors
                continue
        text = "\n\n".join(p.strip() for p in pages if p.strip())
        if not text:
            return None, None
        return text, "pypdf"
    except Exception as exc:  # noqa: BLE001
        logger.warning("pypdf extraction failed: %s", exc)
        return None, None


__all__ = ["AttachmentPersistError", "persist_attachment"]
