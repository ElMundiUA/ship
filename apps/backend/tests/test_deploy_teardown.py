from __future__ import annotations

import uuid

import pytest

from backend.app.db.models.deploy import Deployment, DeploymentStatus as DS
from backend.app.services.deploy import teardown as teardown_mod


class _FakeSession:
    def __init__(self) -> None:
        self.flushed = False

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_teardown_soft_deletes_rows_after_provider_delete(monkeypatch) -> None:
    workspace_id = uuid.uuid4()
    dep = Deployment(
        workspace_id=workspace_id,
        repo_id=uuid.uuid4(),
        provider="digitalocean",
        status=DS.ACTIVE,
        provider_ref={"app_id": "app-1", "deployment_id": "dep-1"},
        live_url="https://example.com",
        healthy=True,
    )

    async def _token(*_: object) -> str:
        return "token"

    monkeypatch.setattr(teardown_mod, "get_provider_token", _token)

    async def _delete(provider: str, app_id: str, token: str) -> bool:
        return provider == "digitalocean" and app_id == "app-1" and token == "token"

    monkeypatch.setattr(teardown_mod, "_delete_provider_app", _delete)
    session = _FakeSession()

    result = await teardown_mod._teardown(
        session,
        workspace_id,
        [dep],
        delete_rows=True,
    )

    assert result.ok is True
    assert result.deleted_app_ids == ["app-1"]
    assert result.rows_soft_deleted == 1
    assert session.flushed is True
    assert dep.status == DS.DELETED
    assert dep.status_detail == "DELETED"
    assert dep.live_url is None
    assert dep.healthy is None
    assert dep.provider_ref["app_id"] == "app-1"
    assert dep.provider_ref["deleted_at"]


@pytest.mark.asyncio
async def test_teardown_keeps_active_rows_when_provider_delete_fails(monkeypatch) -> None:
    workspace_id = uuid.uuid4()
    dep = Deployment(
        workspace_id=workspace_id,
        repo_id=uuid.uuid4(),
        provider="digitalocean",
        status=DS.ACTIVE,
        provider_ref={"app_id": "app-1"},
    )

    async def _token(*_: object) -> str:
        return "token"

    monkeypatch.setattr(teardown_mod, "get_provider_token", _token)

    async def _delete(provider: str, app_id: str, token: str) -> bool:
        return False

    monkeypatch.setattr(teardown_mod, "_delete_provider_app", _delete)
    session = _FakeSession()

    result = await teardown_mod._teardown(
        session,
        workspace_id,
        [dep],
        delete_rows=True,
    )

    assert result.ok is False
    assert result.failed_app_ids == ["app-1"]
    assert result.rows_soft_deleted == 0
    assert session.flushed is False
    assert dep.status == DS.ACTIVE
