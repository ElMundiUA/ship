from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.app.api.v1.routes.knowledge_import_sources import _canonical_config
from backend.app.db.models.agent_memory import (
    BucketScope,
    BucketSource,
    KnowledgeBucket,
    KnowledgeImportSource,
    KnowledgeSourceItem,
)
from backend.app.db.models.agent_surface import Improvement
from backend.app.db.models.tenancy import AuditLog


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def test_canonical_config_is_order_invariant_for_resource_refs() -> None:
    """The dedup hash treats resource_refs as a set, not a sequence.

    Two wizard submissions that pick the same pages in different order
    should be considered the same source — otherwise an operator who
    re-sorts the list during a re-pick produces a phantom duplicate
    that survives indefinitely.
    """
    a = {
        "resource_refs": [
            {"page_id": "p-1"},
            {"page_id": "p-2"},
        ],
        "target_bucket_slug": "product-knowledge",
    }
    b = {
        "resource_refs": [
            {"page_id": "p-2"},
            {"page_id": "p-1"},
        ],
        "target_bucket_slug": "product-knowledge",
    }
    assert _canonical_config(a) == _canonical_config(b)


def test_canonical_config_distinguishes_distinct_ref_sets() -> None:
    a = {"resource_refs": [{"page_id": "p-1"}]}
    b = {"resource_refs": [{"page_id": "p-1"}, {"page_id": "p-2"}]}
    assert _canonical_config(a) != _canonical_config(b)


def test_canonical_config_treats_other_keys_dict_stably() -> None:
    a = {"resource_refs": [], "limit": 25, "include_subdomains": False}
    b = {"include_subdomains": False, "limit": 25, "resource_refs": []}
    assert _canonical_config(a) == _canonical_config(b)


@pytest.mark.asyncio
async def test_static_import_source_sync_emits_knowledge_note_and_skips_unchanged(
    v1_client, seed_workspace, db_session
) -> None:
    """Source sync produces ``Improvement(kind='knowledge_note')`` rows for
    the harvester pipeline (KB-2/3) — it does NOT write directly to a bucket.
    """
    _, raw, workspace = seed_workspace
    bucket = KnowledgeBucket(
        id=uuid.uuid4(),
        workspace_id=workspace.id,
        slug="product-knowledge",
        name="Product Knowledge",
        description="Product behavior and customer-facing concepts.",
        scope_kind=BucketScope.WORKSPACE,
        source_kind=BucketSource.EXTERNAL_STATIC,
    )
    db_session.add(bucket)
    await db_session.flush()

    create_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/sources",
        headers=_auth(raw),
        json={
            "kind": "static_upload",
            "name": "Pilot docs",
            "config": {
                "documents": [
                    {
                        "title": "Customer onboarding",
                        "filename": "onboarding.md",
                        "body_md": "# Customer onboarding\n\nProduct users start here.",
                    }
                ]
            },
            "sync_interval_minutes": None,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    source_id = create_resp.json()["id"]

    sync_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/{source_id}/sync",
        headers=_auth(raw),
    )
    assert sync_resp.status_code == 200, sync_resp.text
    stats = sync_resp.json()["stats"]
    assert stats["discovered"] == 1
    assert stats["changed"] == 1
    assert stats["notes_created"] == 1

    notes = list(
        (
            await db_session.execute(
                select(Improvement).where(
                    Improvement.workspace_id == workspace.id,
                    Improvement.kind == "knowledge_note",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(notes) == 1
    note = notes[0]
    assert note.context["source_kind"] == "import_source"
    assert note.context["import_source_kind"] == "static_upload"
    assert note.context["import_source_id"] == source_id
    assert note.context["routed_bucket_id"] is None

    item = (
        await db_session.execute(
            select(KnowledgeSourceItem).where(KnowledgeSourceItem.source_id == uuid.UUID(source_id))
        )
    ).scalar_one()
    assert item.content_fingerprint

    second_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/{source_id}/sync",
        headers=_auth(raw),
    )
    assert second_resp.status_code == 200, second_resp.text
    second_stats = second_resp.json()["stats"]
    assert second_stats["skipped"] == 1
    assert second_stats["notes_created"] == 0

    stored_source = await db_session.get(KnowledgeImportSource, uuid.UUID(source_id))
    assert stored_source is not None
    assert stored_source.status == "ready"


@pytest.mark.asyncio
async def test_archive_import_source_hides_it_from_list_and_audits(
    v1_client, seed_workspace, db_session
) -> None:
    """Archive marks ``archived_at``, drops the source from the list
    response, and writes a ``knowledge.import_source.archive`` audit row."""
    _, raw, workspace = seed_workspace

    create_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/sources",
        headers=_auth(raw),
        json={
            "kind": "static_upload",
            "name": "To be archived",
            "config": {
                "documents": [
                    {
                        "title": "Doomed",
                        "filename": "doomed.md",
                        "body_md": "# Bye",
                    }
                ]
            },
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    source_id = create_resp.json()["id"]

    archive_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/{source_id}/archive",
        headers=_auth(raw),
    )
    assert archive_resp.status_code == 200, archive_resp.text
    body = archive_resp.json()
    assert body["archived_at"] is not None

    # List excludes archived rows.
    list_resp = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/knowledge/sources",
        headers=_auth(raw),
    )
    assert list_resp.status_code == 200
    assert all(row["id"] != source_id for row in list_resp.json())

    audits = list(
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == source_id,
                    AuditLog.action == "knowledge.import_source.archive",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1
    assert audits[0].payload["kind"] == "static_upload"


@pytest.mark.asyncio
async def test_archive_is_idempotent(v1_client, seed_workspace, db_session) -> None:
    """Hitting archive twice doesn't double the audit row."""
    _, raw, workspace = seed_workspace

    create_resp = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/sources",
        headers=_auth(raw),
        json={
            "kind": "static_upload",
            "name": "Idempotent target",
            "config": {
                "documents": [
                    {"title": "T", "filename": "t.md", "body_md": "# T"}
                ]
            },
        },
    )
    assert create_resp.status_code == 201
    source_id = create_resp.json()["id"]

    for _ in range(2):
        resp = await v1_client.post(
            f"/v1/workspaces/{workspace.id}/knowledge/sources/{source_id}/archive",
            headers=_auth(raw),
        )
        assert resp.status_code == 200

    audits = list(
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.target_id == source_id,
                    AuditLog.action == "knowledge.import_source.archive",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_create_rejects_duplicate_active_source(
    v1_client, seed_workspace, db_session
) -> None:
    """Two non-archived rows with identical config under the same
    workspace + kind + integration would both pull the same content
    forever. The second create must 409 and point the operator at
    the existing row.
    """
    _, raw, workspace = seed_workspace
    config = {
        "documents": [
            {"title": "T", "filename": "t.md", "body_md": "# T"}
        ]
    }

    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/sources",
        headers=_auth(raw),
        json={
            "kind": "static_upload",
            "name": "Original",
            "config": config,
        },
    )
    assert first.status_code == 201
    first_id = first.json()["id"]

    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/sources",
        headers=_auth(raw),
        json={
            "kind": "static_upload",
            "name": "Accidental dupe",
            "config": config,
        },
    )
    assert second.status_code == 409, second.text
    assert first_id in second.json()["detail"]


@pytest.mark.asyncio
async def test_create_after_archive_is_allowed(
    v1_client, seed_workspace, db_session
) -> None:
    """Archiving frees the slot — operators sometimes archive then
    re-add to reset a misconfigured source after fixing share perms
    upstream. The dedup guard scopes to ``archived_at IS NULL`` so
    this stays open.
    """
    _, raw, workspace = seed_workspace
    config = {
        "documents": [
            {"title": "T", "filename": "t.md", "body_md": "# T"}
        ]
    }
    first = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/sources",
        headers=_auth(raw),
        json={"kind": "static_upload", "name": "First", "config": config},
    )
    assert first.status_code == 201
    archived = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/sources/"
        f"{first.json()['id']}/archive",
        headers=_auth(raw),
    )
    assert archived.status_code == 200

    second = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/knowledge/sources",
        headers=_auth(raw),
        json={"kind": "static_upload", "name": "Re-added", "config": config},
    )
    assert second.status_code == 201, second.text
