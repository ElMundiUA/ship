"""Deprecation contract for :mod:`backend.app.services.bucket_repo_files_sync`.

KB-5 (ELS-39, 2026-05-01) removed repo-scoped buckets as a class.
``sync_repo_files`` is now a no-op stub returning an empty
:class:`SyncReport`. The original behavioural test suite is skipped
at module level until the next cleanup PR deletes the service file
entirely.
"""

from __future__ import annotations

import pytest

pytest.skip(
    "bucket_repo_files_sync is deprecated under KB-5 / ELS-39; the "
    "service is now a no-op stub. Behavioural coverage was retired "
    "with the production callers in github_app.py.",
    allow_module_level=True,
)
