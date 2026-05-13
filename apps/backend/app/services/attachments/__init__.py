"""Chat attachment persistence + LLM-pipeline projection.

Three modules:

* :mod:`backend.app.services.attachments.storage` — backend-agnostic
  storage protocol. ``LocalDiskStorage`` is the default; ``S3Storage``
  toggles on via ``SHIP_ATTACHMENT_BACKEND=s3``.

* :mod:`backend.app.services.attachments.policy` — MIME whitelist,
  size caps, kind-from-mime mapping. Single source of truth so the
  route, the storage layer, and the test fixtures agree.

* :mod:`backend.app.services.attachments.service` — high-level
  ``persist`` / ``load`` / ``delete`` that orchestrate storage +
  policy + DB row creation. The chat-stream route calls only this
  module; the lower layers stay testable in isolation.
"""

from backend.app.services.attachments.policy import (
    AttachmentPolicyError,
    ALLOWED_MIME_TYPES,
    MAX_FILES_PER_MESSAGE,
    MAX_SIZE_BYTES_PER_FILE,
    MAX_TOTAL_BYTES_PER_MESSAGE,
    classify_kind,
    validate_upload,
)
from backend.app.services.attachments.service import (
    AttachmentPersistError,
    persist_attachment,
)
from backend.app.services.attachments.storage import (
    AttachmentStorage,
    LocalDiskStorage,
    get_default_storage,
)

__all__ = [
    "AttachmentPolicyError",
    "AttachmentPersistError",
    "AttachmentStorage",
    "ALLOWED_MIME_TYPES",
    "LocalDiskStorage",
    "MAX_FILES_PER_MESSAGE",
    "MAX_SIZE_BYTES_PER_FILE",
    "MAX_TOTAL_BYTES_PER_MESSAGE",
    "classify_kind",
    "get_default_storage",
    "persist_attachment",
    "validate_upload",
]
