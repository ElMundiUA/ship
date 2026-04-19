from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the repo root importable so `backend.app.main` resolves.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _telemetry_sandbox(tmp_path, monkeypatch):
    """Isolate every test from the shared on-disk telemetry jsonl + rate limits."""
    from backend.app import main as backend_main

    events_file = tmp_path / "telemetry" / "events.jsonl"
    events_file.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(backend_main, "TELEMETRY_DIR", events_file.parent)
    monkeypatch.setattr(backend_main, "TELEMETRY_FILE", events_file)
    backend_main._reset_rate_limits()
    yield
    backend_main._reset_rate_limits()


@pytest.fixture(autouse=True)
def _manifest_cache_reset():
    from backend.app import main as backend_main

    backend_main._clear_manifest_cache()
    yield
    backend_main._clear_manifest_cache()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def github_env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test_token_for_testing_only_123456")
    monkeypatch.setenv("SHIP_FEEDBACK_REPO", "ElMundiUA/ship")
    yield


# Postgres-backed fixtures for the v1 API tests (RFC-0006). Imported here so
# the fixtures are discoverable from any test in this directory; tests that
# don't touch them are unaffected.
from backend.tests.db_conftest import (  # noqa: E402, F401  (re-export as fixtures)
    _engine,
    _migrated,
    db_session,
    seed_user,
    seed_user_with_token,
    seed_workspace,
    v1_client,
)
