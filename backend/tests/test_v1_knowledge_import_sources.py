from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.app.db.models.agent_memory import (
    BucketScope,
    BucketSource,
    KnowledgeBucket,
    KnowledgeImportSource,
    KnowledgeSourceItem,
)
from backend.app.db.models.agent_surface import Improvement


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


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
