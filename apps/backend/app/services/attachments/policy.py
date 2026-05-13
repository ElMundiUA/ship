"""Chat attachment upload policy — MIME whitelist + size caps.

Single source of truth so the route, the storage layer, and tests
all agree on what's accepted.

What we allow today (v1):

* **Images**: ``image/jpeg``, ``image/png``, ``image/webp``,
  ``image/gif``. Claude's vision and OpenAI's image_url both
  handle these natively. ``image/heic`` deliberately excluded
  until we add a conversion pass — Claude doesn't accept it.

* **Documents**: ``application/pdf``. Claude has native PDF
  support via the ``document`` content block; OpenAI gets a
  text-extracted body via ``pypdf`` server-side.

* **Text**: ``text/markdown``, ``text/plain``, ``text/csv``,
  ``application/json``, ``application/yaml``,
  ``application/x-yaml``. All embedded inline as text blocks.

Caps:

* per file:    10 MiB  — image with detail or short PDF
* per message: 30 MiB  — total across all attachments in one turn
* file count:  5       — keeps the LLM prompt sane

Reject early at the route layer so the upload bytes never hit
disk. The storage helpers re-check on read to defend against
race-y rename / disk-full scenarios.
"""

from __future__ import annotations


class AttachmentPolicyError(ValueError):
    """Policy-rejected upload. ``code`` is the stable wire code the
    chat-stream route maps to an HTTP error body so the console
    can render a specific message ("file too big" vs "wrong
    type") instead of a generic 422."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_IMAGE_MIMES: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)
_PDF_MIMES: frozenset[str] = frozenset({"application/pdf"})
_TEXT_MIMES: frozenset[str] = frozenset(
    {
        "text/markdown",
        "text/plain",
        "text/csv",
        "application/json",
        "application/yaml",
        "application/x-yaml",
    }
)

ALLOWED_MIME_TYPES: frozenset[str] = _IMAGE_MIMES | _PDF_MIMES | _TEXT_MIMES

MAX_SIZE_BYTES_PER_FILE: int = 10 * 1024 * 1024      # 10 MiB
MAX_TOTAL_BYTES_PER_MESSAGE: int = 30 * 1024 * 1024  # 30 MiB
MAX_FILES_PER_MESSAGE: int = 5


def classify_kind(mime: str) -> str:
    """Map a MIME type to the coarse ``kind`` we store on the row.
    Caller is expected to have already passed the type through
    :func:`validate_upload` (or the equivalent) so an out-of-band
    type here would be a bug — we raise rather than silently
    bucketing into ``text``."""
    if mime in _IMAGE_MIMES:
        return "image"
    if mime in _PDF_MIMES:
        return "pdf"
    if mime in _TEXT_MIMES:
        return "text"
    raise AttachmentPolicyError(
        "unsupported_mime",
        f"MIME type {mime!r} is not on the attachment whitelist.",
    )


def validate_upload(
    *,
    filename: str,
    mime: str,
    size_bytes: int,
) -> None:
    """Pre-flight check before the bytes touch disk. Raises
    :class:`AttachmentPolicyError` on policy violations; returns
    nothing on accept."""
    if not filename or len(filename) > 255:
        raise AttachmentPolicyError(
            "bad_filename",
            "filename must be 1-255 chars.",
        )
    if mime not in ALLOWED_MIME_TYPES:
        raise AttachmentPolicyError(
            "unsupported_mime",
            (
                f"MIME type {mime!r} is not on the attachment whitelist. "
                f"Allowed: {sorted(ALLOWED_MIME_TYPES)}."
            ),
        )
    if size_bytes <= 0:
        raise AttachmentPolicyError(
            "empty_file",
            "file is empty.",
        )
    if size_bytes > MAX_SIZE_BYTES_PER_FILE:
        raise AttachmentPolicyError(
            "file_too_large",
            (
                f"file is {size_bytes} bytes; per-file cap is "
                f"{MAX_SIZE_BYTES_PER_FILE}."
            ),
        )


__all__ = [
    "ALLOWED_MIME_TYPES",
    "AttachmentPolicyError",
    "MAX_FILES_PER_MESSAGE",
    "MAX_SIZE_BYTES_PER_FILE",
    "MAX_TOTAL_BYTES_PER_MESSAGE",
    "classify_kind",
    "validate_upload",
]
