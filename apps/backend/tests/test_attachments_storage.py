"""Phase 3a — attachment storage + policy unit tests.

DB-bound paths (persist_attachment writing a row, route layer
applying message-level caps) belong with the multipart route tests
once a session fixture is wired in CI. Here we only exercise the
in-process pieces:

* :class:`LocalDiskStorage` round-trips bytes through the disk and
  the URI shape stays stable.
* :func:`validate_upload` rejects oversize / empty / wrong-MIME
  files with the documented stable codes.
* :func:`classify_kind` maps every whitelisted MIME to one of the
  three CHECK-constraint values.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest


def test_local_disk_storage_writes_and_reads(tmp_path) -> None:
    """End-to-end: write bytes, get a file:// URI, read them back
    via the same URI."""
    from backend.app.services.attachments.storage import LocalDiskStorage

    storage = LocalDiskStorage(base_dir=tmp_path)
    ws_id = uuid.uuid4()
    att_id = uuid.uuid4()
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 4096  # PNG-ish bytes

    async def run() -> tuple[str, bytes]:
        uri = await storage.write(workspace_id=ws_id, attachment_id=att_id, data=payload)
        back = await storage.read(uri)
        return uri, back

    uri, back = asyncio.run(run())
    assert uri.startswith("file://")
    assert str(ws_id) in uri
    assert str(att_id) in uri
    assert back == payload


def test_local_disk_storage_delete_is_idempotent(tmp_path) -> None:
    from backend.app.services.attachments.storage import LocalDiskStorage

    storage = LocalDiskStorage(base_dir=tmp_path)
    ws_id = uuid.uuid4()
    att_id = uuid.uuid4()

    async def run() -> None:
        uri = await storage.write(workspace_id=ws_id, attachment_id=att_id, data=b"x")
        await storage.delete(uri)
        # Second delete must NOT raise — the cron sweeper expects
        # this idempotent.
        await storage.delete(uri)

    asyncio.run(run())


@pytest.mark.parametrize(
    "mime,expected_kind",
    [
        ("image/jpeg", "image"),
        ("image/png", "image"),
        ("image/webp", "image"),
        ("image/gif", "image"),
        ("application/pdf", "pdf"),
        ("text/plain", "text"),
        ("text/markdown", "text"),
        ("text/csv", "text"),
        ("application/json", "text"),
        ("application/yaml", "text"),
    ],
)
def test_classify_kind_maps_every_whitelisted_mime(mime: str, expected_kind: str) -> None:
    from backend.app.services.attachments.policy import classify_kind

    assert classify_kind(mime) == expected_kind


def test_validate_upload_rejects_unsupported_mime() -> None:
    from backend.app.services.attachments.policy import (
        AttachmentPolicyError,
        validate_upload,
    )

    with pytest.raises(AttachmentPolicyError) as exc_info:
        validate_upload(filename="x.heic", mime="image/heic", size_bytes=100)
    assert exc_info.value.code == "unsupported_mime"


def test_validate_upload_rejects_oversize() -> None:
    from backend.app.services.attachments.policy import (
        AttachmentPolicyError,
        MAX_SIZE_BYTES_PER_FILE,
        validate_upload,
    )

    with pytest.raises(AttachmentPolicyError) as exc_info:
        validate_upload(
            filename="big.png",
            mime="image/png",
            size_bytes=MAX_SIZE_BYTES_PER_FILE + 1,
        )
    assert exc_info.value.code == "file_too_large"


def test_validate_upload_rejects_empty() -> None:
    from backend.app.services.attachments.policy import (
        AttachmentPolicyError,
        validate_upload,
    )

    with pytest.raises(AttachmentPolicyError) as exc_info:
        validate_upload(filename="empty.png", mime="image/png", size_bytes=0)
    assert exc_info.value.code == "empty_file"


def test_validate_upload_rejects_bad_filename() -> None:
    from backend.app.services.attachments.policy import (
        AttachmentPolicyError,
        validate_upload,
    )

    with pytest.raises(AttachmentPolicyError) as exc_info:
        validate_upload(filename="", mime="image/png", size_bytes=100)
    assert exc_info.value.code == "bad_filename"


@pytest.mark.parametrize(
    "filename,reported,expected",
    [
        ("session-summary.md", "", "text/markdown"),
        ("session-summary.md", None, "text/markdown"),
        ("notes.md", "application/octet-stream", "text/markdown"),
        ("doc.md", "text/markdown", "text/markdown"),
        ("README.MD", "", "text/markdown"),
        ("archive.tar.md", "", "text/markdown"),
        ("doc.md", "image/heic", "text/markdown"),
        ("photo.heic", "", "application/octet-stream"),
        ("photo.png", "image/png", "image/png"),
    ],
)
def test_resolve_mime(filename: str, reported: str | None, expected: str) -> None:
    from backend.app.services.attachments.policy import resolve_mime

    assert resolve_mime(filename, reported) == expected


def test_resolve_mime_then_validate_accepts_empty_md() -> None:
    from backend.app.services.attachments.policy import resolve_mime, validate_upload

    mime = resolve_mime("session-summary.md", "")
    validate_upload(filename="session-summary.md", mime=mime, size_bytes=100)


def test_resolve_mime_then_validate_rejects_heic() -> None:
    from backend.app.services.attachments.policy import (
        AttachmentPolicyError,
        resolve_mime,
        validate_upload,
    )

    mime = resolve_mime("photo.heic", "")
    with pytest.raises(AttachmentPolicyError) as exc_info:
        validate_upload(filename="photo.heic", mime=mime, size_bytes=100)
    assert exc_info.value.code == "unsupported_mime"


def test_get_default_storage_refuses_unknown_backend(monkeypatch) -> None:
    """A half-configured ``SHIP_ATTACHMENT_BACKEND`` must fail
    loudly, not silently fall back to local — otherwise an operator
    who set ``=s3`` thinking it's wired ends up dropping bytes onto
    a node-local disk and being surprised when they vanish."""
    from backend.app.services.attachments.storage import get_default_storage

    monkeypatch.setenv("SHIP_ATTACHMENT_BACKEND", "azure")
    with pytest.raises(ValueError, match="Unknown SHIP_ATTACHMENT_BACKEND"):
        get_default_storage()


def test_get_default_storage_refuses_s3_until_implemented(monkeypatch) -> None:
    """S3 backend is stubbed; raise rather than silently fall back."""
    from backend.app.services.attachments.storage import get_default_storage

    monkeypatch.setenv("SHIP_ATTACHMENT_BACKEND", "s3")
    with pytest.raises(NotImplementedError):
        get_default_storage()
