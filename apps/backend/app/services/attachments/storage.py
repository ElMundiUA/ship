"""Storage backend protocol + LocalDisk implementation.

The chat-stream route hands the storage layer ``(workspace_id,
attachment_id, bytes)``; the storage helpers return a URI the DB
row will carry as ``storage_path``. On read the same URI gets
resolved back to bytes.

Two backends are anticipated:

* **LocalDiskStorage** — default. Files land under
  ``$SHIP_ATTACHMENT_DIR / <workspace_id> / <attachment_id>``.
  Single-node-only, but zero-setup and plenty for the pilot.
* **S3Storage** — future. Set ``SHIP_ATTACHMENT_BACKEND=s3`` +
  ``SHIP_ATTACHMENT_S3_BUCKET`` + AWS creds; the resolver here
  swaps backends and ``storage_path`` becomes ``s3://...``. We
  stub the protocol now so adding it later is a single class.

The protocol is async even though the local-disk implementation is
sync internally — keeps the call sites identical for the eventual
S3 path and lets us add streaming later without churn.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Protocol


_LOCAL_URI_PREFIX = "file://"


class AttachmentStorage(Protocol):
    """Backend-agnostic storage protocol. Callers should treat the
    returned URI as opaque; only :meth:`open_for_read` knows how to
    resolve it."""

    async def write(
        self,
        *,
        workspace_id: uuid.UUID,
        attachment_id: uuid.UUID,
        data: bytes,
    ) -> str:
        """Persist ``data`` and return the URI to store on the row."""

    async def read(self, storage_path: str) -> bytes:
        """Resolve ``storage_path`` back to its bytes."""

    async def delete(self, storage_path: str) -> None:
        """Best-effort delete. Missing rows are a no-op so a stale
        DB row pointing at a vanished file doesn't blow up on
        cascade delete."""


class LocalDiskStorage:
    """Local filesystem backend.

    Layout: ``<base_dir>/<workspace_id>/<attachment_id>``. We
    deliberately drop the file extension on disk — the row already
    carries ``mime`` and ``filename``, and shaving the extension
    avoids accidental shell-glob hits during dev.
    """

    def __init__(self, base_dir: str | os.PathLike[str]) -> None:
        self._base_dir = Path(base_dir).expanduser().resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    async def write(
        self,
        *,
        workspace_id: uuid.UUID,
        attachment_id: uuid.UUID,
        data: bytes,
    ) -> str:
        ws_dir = self._base_dir / str(workspace_id)
        ws_dir.mkdir(parents=True, exist_ok=True)
        path = ws_dir / str(attachment_id)
        # Atomic write — same-FS rename. Avoids a half-written file
        # leaking out if the request was killed mid-stream.
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.rename(path)
        return f"{_LOCAL_URI_PREFIX}{path}"

    async def read(self, storage_path: str) -> bytes:
        if not storage_path.startswith(_LOCAL_URI_PREFIX):
            raise ValueError(
                f"LocalDiskStorage can't read {storage_path!r} — not a file:// URI"
            )
        return Path(storage_path[len(_LOCAL_URI_PREFIX):]).read_bytes()

    async def delete(self, storage_path: str) -> None:
        if not storage_path.startswith(_LOCAL_URI_PREFIX):
            return
        path = Path(storage_path[len(_LOCAL_URI_PREFIX):])
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Best-effort — a stale row pointing at a missing file
            # shouldn't block a chat-thread purge. The cron GC
            # sweeper will reconcile.
            pass


_DEFAULT_BASE_DIR_ENV = "SHIP_ATTACHMENT_DIR"
_DEFAULT_BASE_DIR_FALLBACK = "/var/ship/attachments"


def get_default_storage() -> AttachmentStorage:
    """Resolve the configured storage backend.

    Today only LocalDisk is wired. ``SHIP_ATTACHMENT_BACKEND=s3``
    becomes a real branch when the S3 implementation lands; we
    refuse on that value now so a half-configured deploy doesn't
    silently drop bytes into local disk.
    """
    backend = (os.environ.get("SHIP_ATTACHMENT_BACKEND") or "local").lower()
    if backend == "local":
        base = os.environ.get(_DEFAULT_BASE_DIR_ENV) or _DEFAULT_BASE_DIR_FALLBACK
        return LocalDiskStorage(base_dir=base)
    if backend == "s3":
        raise NotImplementedError(
            "S3 attachment backend isn't wired yet. Unset "
            "SHIP_ATTACHMENT_BACKEND (or set to 'local') for the pilot."
        )
    raise ValueError(
        f"Unknown SHIP_ATTACHMENT_BACKEND={backend!r}. "
        "Expected 'local' or 's3'."
    )


__all__ = ["AttachmentStorage", "LocalDiskStorage", "get_default_storage"]
