"""E17 / ELS-126 — unit tests for the Navigator memory service.

Covers the storage invariants the wrapper has to guarantee
regardless of what mem0 itself does:

1. **Per-user isolation** — user A's facts must never surface in
   user B's search results inside the same workspace.
2. **Workspace boundary** — the same human user, given two
   workspaces, must see independent fact spaces (the composite
   mem0 namespace ``ws:<ws>:u:<user>`` does this; we assert at the
   mirror layer it actually works).
3. **add → search → delete round-trip** — the mirror row exists
   after ``add``, surfaces in ``search``, audits + drops on
   ``delete``.

mem0 itself is stubbed so the suite doesn't burn OpenAI tokens or
require a live PG vector_store. The wrapper's contract with mem0 is
narrow enough that a fake exercises every code path we own.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest

from backend.app.db.models.navigator_memory import NavigatorMemory
from backend.app.db.models.tenancy import AuditLog
from backend.app.services.agent import memory as memory_module
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Fake mem0 client
# ---------------------------------------------------------------------------


class _FakeMem0:
    """In-memory stand-in for ``mem0.Memory``.

    The wrapper only calls three methods: ``add``, ``search``,
    ``delete``. We mirror their return shapes (``{"results": [...]}``)
    just enough to drive the wrapper's branches.
    """

    def __init__(self) -> None:
        # mem0_id → {"memory": text, "user_id": namespace, "metadata": {...}}
        self.store: dict[str, dict] = {}

    def add(self, text, *, user_id, metadata=None):
        new_id = uuid.uuid4().hex
        self.store[new_id] = {
            "memory": text[:200],
            "user_id": user_id,
            "metadata": metadata or {},
        }
        return {"results": [{"id": new_id, "memory": text[:200], "event": "ADD"}]}

    def search(self, query, *, user_id, limit=10):
        # Naive substring match scoped by namespace. mem0 actually
        # vector-searches; for tests we just need a deterministic
        # filter that respects the namespace boundary the wrapper
        # encodes.
        hits = []
        q = (query or "").lower()
        for mid, row in self.store.items():
            if row.get("user_id") != user_id:
                continue
            if q and q in (row.get("memory") or "").lower():
                hits.append({"id": mid, "memory": row["memory"], "score": 0.9})
            elif not q:
                hits.append({"id": mid, "memory": row["memory"], "score": 0.5})
        return {"results": hits[:limit]}

    def delete(self, mem0_id):
        self.store.pop(mem0_id, None)


@pytest.fixture
def _fake_mem0(monkeypatch):
    """Install a fresh ``_FakeMem0`` and force the wrapper to use it.

    The wrapper caches a singleton at module level; we patch the
    accessor so each test gets a clean store.
    """
    fake = _FakeMem0()

    def _accessor(_settings):
        return fake

    monkeypatch.setattr(memory_module, "_get_memory_client", _accessor)
    # Reset the cached singleton so a real call (in some other test)
    # can't leak in.
    monkeypatch.setattr(memory_module, "_MEMORY_CLIENT", None)
    return fake


# ---------------------------------------------------------------------------
# Helpers — minimal workspace + user fixtures (mem0 doesn't care, but
# the mirror table has FKs).
# ---------------------------------------------------------------------------


async def _make_user_and_workspace(db_session) -> tuple[uuid.UUID, uuid.UUID]:
    from backend.app.db.models.tenancy import Org, Workspace, User

    user = User(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        display_name="Test user",
    )
    db_session.add(user)
    await db_session.flush()
    org = Org(slug=f"o-{uuid.uuid4().hex[:8]}", name="Test org", plan="free")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(
        org_id=org.id,
        slug=f"w-{uuid.uuid4().hex[:8]}",
        name="Test ws",
    )
    db_session.add(ws)
    await db_session.flush()
    return user.id, ws.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_creates_mirror_row(db_session, _fake_mem0) -> None:
    user_id, ws_id = await _make_user_and_workspace(db_session)
    added = await memory_module.add(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        message="The PO prefers Monday releases over Friday.",
    )
    assert len(added) == 1
    assert added[0].fact_text.startswith("The PO prefers")

    # Mirror row exists with the right owner / workspace tags.
    row = (
        await db_session.execute(
            select(NavigatorMemory).where(
                NavigatorMemory.mem0_id == added[0].mem0_id
            )
        )
    ).scalar_one()
    assert row.owner_user_id == user_id
    assert row.workspace_id == ws_id
    assert row.fact_text == added[0].fact_text


@pytest.mark.asyncio
async def test_search_returns_user_facts(db_session, _fake_mem0) -> None:
    user_id, ws_id = await _make_user_and_workspace(db_session)
    await memory_module.add(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        message="PO uses Monday releases.",
    )
    hits = await memory_module.search(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        query="releases",
    )
    assert len(hits) == 1
    assert "monday" in hits[0].row.fact_text.lower()


@pytest.mark.asyncio
async def test_user_a_cannot_see_user_b_facts(db_session, _fake_mem0) -> None:
    user_a, ws_id = await _make_user_and_workspace(db_session)
    user_b, _ = await _make_user_and_workspace(db_session)
    # Both users belong to the same workspace for this test — re-use ws_id.
    await memory_module.add(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_a,
        message="User A prefers gpt-4o-mini for cheap tasks.",
    )
    # B searches with a query that would match A's fact if isolation
    # were broken.
    hits = await memory_module.search(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_b,
        query="gpt-4o-mini",
    )
    assert hits == []


@pytest.mark.asyncio
async def test_same_user_isolated_across_workspaces(
    db_session, _fake_mem0
) -> None:
    user_id, ws_a = await _make_user_and_workspace(db_session)
    _, ws_b = await _make_user_and_workspace(db_session)
    # Both rows in the same db session — same user, different ws.
    await memory_module.add(
        db_session,
        workspace_id=ws_a,
        owner_user_id=user_id,
        message="In ws A we ship on Mondays.",
    )
    await memory_module.add(
        db_session,
        workspace_id=ws_b,
        owner_user_id=user_id,
        message="In ws B we ship on Fridays.",
    )
    a_hits = await memory_module.search(
        db_session,
        workspace_id=ws_a,
        owner_user_id=user_id,
        query="ship",
    )
    b_hits = await memory_module.search(
        db_session,
        workspace_id=ws_b,
        owner_user_id=user_id,
        query="ship",
    )
    assert len(a_hits) == 1
    assert "ws a" in a_hits[0].row.fact_text.lower()
    assert len(b_hits) == 1
    assert "ws b" in b_hits[0].row.fact_text.lower()


@pytest.mark.asyncio
async def test_delete_hard_removes_plus_audit(db_session, _fake_mem0) -> None:
    user_id, ws_id = await _make_user_and_workspace(db_session)
    added = await memory_module.add(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        message="A fact to be forgotten.",
    )
    assert len(added) == 1
    fid = added[0].id

    ok = await memory_module.delete(
        db_session,
        memory_id=fid,
        actor_user_id=user_id,
        workspace_id=ws_id,
    )
    assert ok is True

    # Mirror row gone.
    gone = (
        await db_session.execute(
            select(NavigatorMemory).where(NavigatorMemory.id == fid)
        )
    ).scalar_one_or_none()
    assert gone is None

    # Audit row carries the original fact text for forensics.
    audit_rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "navigator.memory.deleted",
                    AuditLog.workspace_id == ws_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1
    assert audit_rows[0].payload["fact_text"] == "A fact to be forgotten."


@pytest.mark.asyncio
async def test_delete_refuses_when_not_owner(
    db_session, _fake_mem0
) -> None:
    """Hard-delete must check the actor IS the owner — a different
    user in the same workspace can't drop someone else's fact."""
    user_a, ws_id = await _make_user_and_workspace(db_session)
    user_b, _ = await _make_user_and_workspace(db_session)
    added = await memory_module.add(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_a,
        message="A's private note.",
    )
    ok = await memory_module.delete(
        db_session,
        memory_id=added[0].id,
        actor_user_id=user_b,
        workspace_id=ws_id,
    )
    assert ok is False
    # Row still there.
    still = (
        await db_session.execute(
            select(NavigatorMemory).where(NavigatorMemory.id == added[0].id)
        )
    ).scalar_one_or_none()
    assert still is not None


@pytest.mark.asyncio
async def test_list_for_user_paginates(db_session, _fake_mem0) -> None:
    user_id, ws_id = await _make_user_and_workspace(db_session)
    for n in range(5):
        await memory_module.add(
            db_session,
            workspace_id=ws_id,
            owner_user_id=user_id,
            message=f"Fact number {n} of the series.",
        )
    page1 = await memory_module.list_for_user(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        limit=2,
        offset=0,
    )
    page2 = await memory_module.list_for_user(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        limit=2,
        offset=2,
    )
    assert len(page1) == 2
    assert len(page2) == 2
    # Distinct rows across pages — pagination didn't return the same items.
    ids_p1 = {r.id for r in page1}
    ids_p2 = {r.id for r in page2}
    assert ids_p1.isdisjoint(ids_p2)


@pytest.mark.asyncio
async def test_add_failure_writes_audit_row(
    db_session, _fake_mem0, monkeypatch
) -> None:
    """mem0 throwing must not bubble up — log + audit + empty return."""
    user_id, ws_id = await _make_user_and_workspace(db_session)

    def _boom(*_a, **_kw):
        raise RuntimeError("mem0 went sideways")

    monkeypatch.setattr(_fake_mem0, "add", _boom)

    added = await memory_module.add(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        message="Some message that mem0 will refuse to ingest.",
    )
    assert added == []

    audit_rows = (
        (
            await db_session.execute(
                select(AuditLog).where(
                    AuditLog.action == "navigator.memory.add_failed",
                    AuditLog.workspace_id == ws_id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(audit_rows) == 1
    assert "mem0 went sideways" in audit_rows[0].payload["error"]


@pytest.mark.asyncio
async def test_project_scope_filter(db_session, _fake_mem0) -> None:
    """``search(project_native_id=X)`` returns only facts tagged X
    plus untagged general-purpose facts (no project tag)."""
    user_id, ws_id = await _make_user_and_workspace(db_session)
    await memory_module.add(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        message="General preference: short PR descriptions.",
    )
    await memory_module.add(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        message="Project X needs feature flags.",
        project_native_id="proj-X",
    )
    await memory_module.add(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        message="Project Y uses canary deploys.",
        project_native_id="proj-Y",
    )
    # The wrapper rejects empty queries (mem0's semantic search has
    # no useful meaning without one); use a token that hits all
    # three seeded facts so the project filter is the only thing
    # narrowing the result set.
    hits = await memory_module.search(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        query="P",
        project_native_id="proj-X",
    )
    fact_texts = {h.row.fact_text for h in hits}
    # General-purpose untagged fact + the project-X fact, but NOT project-Y.
    assert any("short PR descriptions" in t for t in fact_texts)
    assert any("feature flags" in t for t in fact_texts)
    assert not any("canary" in t for t in fact_texts)


# ---------------------------------------------------------------------------
# ELS-127 — pre-filter + toggle + background extraction
# ---------------------------------------------------------------------------


def test_should_extract_short_acks_skipped() -> None:
    """Ack-noise should never reach mem0 — the LLM extractor's
    fixed overhead isn't worth it on "ok" / "thanks"."""
    for body in ("ok", "OK!", "thanks ", "ага", "норм", "yes", "great", "got it"):
        assert memory_module.should_extract_memory(True, body) is False, body


def test_should_extract_long_substantive_message() -> None:
    """A real PO statement >= 30 chars passes the filter."""
    body = (
        "The PO prefers Monday releases. Avoid Friday deploys until "
        "the on-call rotation has been fixed."
    )
    assert memory_module.should_extract_memory(True, body) is True


def test_should_extract_respects_memory_enabled_toggle() -> None:
    """Console "Pause memory" button → ``memory_enabled=False`` →
    skip regardless of message length / content."""
    long_meaningful = (
        "The PO confirmed the release date should slip to next "
        "Wednesday because of the conference."
    )
    assert memory_module.should_extract_memory(False, long_meaningful) is False


def test_should_extract_empty_string_skipped() -> None:
    assert memory_module.should_extract_memory(True, "") is False
    assert memory_module.should_extract_memory(True, "    ") is False


def test_should_extract_short_non_ack_still_skipped() -> None:
    """Short messages that aren't a known ack pattern are still
    skipped — mem0 needs context to extract anything useful."""
    body = "do it"  # 5 chars, not in ack tokens but not extract-worthy
    assert memory_module.should_extract_memory(True, body) is False


# ---------------------------------------------------------------------------
# ELS-128 — formatter + recall tool
# ---------------------------------------------------------------------------


def test_format_mem0_facts_emits_compact_block() -> None:
    """``_format_mem0_facts`` produces a system-prompt block the LLM
    sees as one tidy section. Order preserved; metadata folded into
    a short suffix per fact; empty fact rows skipped."""
    from backend.app.services.agent.topic import _format_mem0_facts

    class _Row:
        def __init__(self, fact_text, project=None, intent=None, fid="abc"):
            self.fact_text = fact_text
            self.project_native_id = project
            self.intent_at_capture = intent
            self.id = fid

    block = _format_mem0_facts(
        [
            _Row("PO prefers Monday releases.", project=None),
            _Row("Project X needs feature flags.", project="proj-X"),
            _Row("", project=None),  # empty fact — must be skipped
            _Row(
                "Drafted spec for project Y.",
                project="proj-Y",
                intent="shape_project",
            ),
        ]
    )
    assert "What Ship remembers about you" in block
    assert "Monday releases" in block
    assert "project=proj-X" in block
    assert "captured under intent=shape_project" in block
    # Empty row was filtered.
    assert block.count("- ") == 3


def test_format_mem0_facts_empty_input_returns_header_only() -> None:
    from backend.app.services.agent.topic import _format_mem0_facts

    block = _format_mem0_facts([])
    # Single-line header — no bullet points.
    assert "What Ship remembers about you" in block
    assert "- " not in block


@pytest.mark.asyncio
async def test_recall_tool_returns_user_facts_only(
    db_session, _fake_mem0
) -> None:
    """The ``recall`` tool uses ``memory.search`` under the hood, so
    the per-user isolation already verified in ELS-126 implies the
    tool can't leak someone else's fact. This test confirms the
    wiring: same user, same workspace, ``recall(query='Monday')``
    returns the right fact in the right shape."""
    user_id, ws_id = await _make_user_and_workspace(db_session)
    await memory_module.add(
        db_session,
        workspace_id=ws_id,
        owner_user_id=user_id,
        message="PO confirmed Monday releases are mandatory for prod.",
    )

    # Build a minimal ToolBox harness — we only need ``_tool_recall``.
    from backend.app.services.agent.tools import ToolBox
    box = ToolBox.__new__(ToolBox)  # bypass __init__'s heavy deps
    box._session = db_session
    box._workspace_id = ws_id
    box._user_id = user_id

    import json
    result_json = await box._tool_recall(
        {"query": "Monday releases", "limit": 5}
    )
    result = json.loads(result_json)
    assert isinstance(result, list)
    assert len(result) == 1
    assert "Monday releases" in result[0]["fact_text"]
    assert "id" in result[0]
    assert "score" in result[0]


# ---------------------------------------------------------------------------
# ELS-129 — planning flow fix (intent reset + project-tagged fact)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_create_resets_intent_and_tags_fact(
    db_session, _fake_mem0, monkeypatch
) -> None:
    """End-to-end of the ELS-129 wiring:

    1. A chat thread sits in ``intent='shape_project'``.
    2. ``_tool_project_create`` runs (with a stubbed tracker).
    3. After the call, ``thread.intent`` is NULL.
    4. A mem0 fact exists with ``project_native_id`` + ``intent_at_capture=shape_project``.
    """
    from backend.app.db.models.agent_surface import ChatThread
    from backend.app.db.models.navigator_memory import NavigatorMemory
    from backend.app.services.agent.tools import ToolBox

    user_id, ws_id = await _make_user_and_workspace(db_session)
    thread = ChatThread(
        workspace_id=ws_id,
        created_by_user_id=user_id,
        title="Drafting a project",
        status="active",
        intent="shape_project",
    )
    db_session.add(thread)
    await db_session.flush()

    # Build the harness — only the fields ``_tool_project_create``
    # touches need to be set.
    box = ToolBox.__new__(ToolBox)
    box._session = db_session
    box._settings = None  # mem0 client is monkey-patched to a fake
    box._workspace_id = ws_id
    box._user_id = user_id
    box._thread_id = thread.id
    box._thread_intent = "shape_project"
    box._subagent_active = False

    # Fake tracker: returns a stubbed project with a deterministic id
    # and no-op'd anchor / priorities helpers.
    class _FakeTracker:
        async def create_project(self, *, name, body, description=None):
            return {
                "id": "proj-ELS129-fixture",
                "name": name,
                "body": body,
                "url": "https://linear.app/test/project/proj-ELS129-fixture",
            }

        async def get_planning_anchor(self, project_id):
            return None

        async def create_planning_anchor(self, project_id, *, title, body, labels=None):
            return None

    async def _resolve_tracker(_kind, _hint):
        return _FakeTracker()

    async def _ensure_drafts(*, project_native_id):
        return None

    box._resolve_tracker = _resolve_tracker  # type: ignore[assignment]
    box._ensure_drafts_priorities_row = _ensure_drafts  # type: ignore[assignment]

    import json
    out_json = await box._tool_project_create(
        {
            "name": "E17 test project",
            "body": "## Goal\nValidate the ELS-129 wiring end-to-end.",
        }
    )
    out = json.loads(out_json)
    assert out["id"] == "proj-ELS129-fixture"

    # 1. Thread intent reset.
    await db_session.refresh(thread)
    assert thread.intent is None, "shape_project intent should be cleared after create"
    assert box._thread_intent is None, "in-memory mirror should also reset"

    # 2. mem0 fact tagged with the new project.
    facts = (
        (
            await db_session.execute(
                select(NavigatorMemory).where(
                    NavigatorMemory.owner_user_id == user_id,
                    NavigatorMemory.workspace_id == ws_id,
                    NavigatorMemory.project_native_id == "proj-ELS129-fixture",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(facts) == 1
    f = facts[0]
    assert f.intent_at_capture == "shape_project"
    assert "E17 test project" in f.fact_text
    assert "proj-ELS129-fixture" in f.fact_text
    assert "Validate the ELS-129 wiring" in f.fact_text


@pytest.mark.asyncio
async def test_project_create_no_reset_when_intent_already_clear(
    db_session, _fake_mem0
) -> None:
    """If the thread wasn't in shape_project (e.g. a normal chat
    that happened to call create_project from a follow-up), the
    reset is a no-op and no mem0 fact gets written."""
    from backend.app.db.models.agent_surface import ChatThread
    from backend.app.db.models.navigator_memory import NavigatorMemory
    from backend.app.services.agent.tools import ToolBox

    user_id, ws_id = await _make_user_and_workspace(db_session)
    thread = ChatThread(
        workspace_id=ws_id,
        created_by_user_id=user_id,
        title="Regular chat",
        status="active",
        intent=None,
    )
    db_session.add(thread)
    await db_session.flush()

    box = ToolBox.__new__(ToolBox)
    box._session = db_session
    box._settings = None
    box._workspace_id = ws_id
    box._user_id = user_id
    box._thread_id = thread.id
    box._thread_intent = None
    box._subagent_active = False

    class _FakeTracker:
        async def create_project(self, *, name, body, description=None):
            return {"id": "no-tag-fixture", "name": name, "body": body, "url": "x"}

        async def get_planning_anchor(self, project_id):
            return None

        async def create_planning_anchor(self, project_id, *, title, body, labels=None):
            return None

    async def _resolve_tracker(_kind, _hint):
        return _FakeTracker()

    async def _ensure_drafts(*, project_native_id):
        return None

    box._resolve_tracker = _resolve_tracker  # type: ignore[assignment]
    box._ensure_drafts_priorities_row = _ensure_drafts  # type: ignore[assignment]

    await box._tool_project_create({"name": "Regular project", "body": "..."})

    facts = (
        (
            await db_session.execute(
                select(NavigatorMemory).where(
                    NavigatorMemory.workspace_id == ws_id,
                    NavigatorMemory.project_native_id == "no-tag-fixture",
                )
            )
        )
        .scalars()
        .all()
    )
    assert facts == [], "no shape_project fact should be written outside drafting mode"


# ---------------------------------------------------------------------------
# ELS-130 — Console-facing REST endpoints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_endpoint_list_returns_only_own_facts(
    v1_client, db_session, seed_workspace, _fake_mem0
) -> None:
    """``GET /navigator-memories`` is owner-scoped — viewing user
    sees their own facts, not facts written by other users in the
    same workspace."""
    user, raw_token, workspace = seed_workspace
    # Add facts for THIS user.
    await memory_module.add(
        db_session,
        workspace_id=workspace.id,
        owner_user_id=user.id,
        message="The PO confirmed Monday releases are mandatory.",
    )
    # Plus a fact for a DIFFERENT user in the same workspace —
    # mustn't show up in the listing for user A.
    other_user, _ = await _make_user_and_workspace(db_session)
    await memory_module.add(
        db_session,
        workspace_id=workspace.id,
        owner_user_id=other_user,
        message="User B prefers Tuesday deploys.",
    )
    await db_session.commit()

    response = await v1_client.get(
        f"/v1/workspaces/{workspace.id}/navigator-memories",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    fact_texts = {it["fact_text"] for it in body["items"]}
    assert any("Monday" in t for t in fact_texts)
    assert not any("Tuesday" in t for t in fact_texts)


@pytest.mark.asyncio
async def test_endpoint_delete_owner_check(
    v1_client, db_session, seed_workspace, _fake_mem0
) -> None:
    """A user cannot delete another user's fact even with admin role.
    Backend returns 404 (not 403) so the fact's existence isn't
    leaked."""
    user, raw_token, workspace = seed_workspace
    other_user, _ = await _make_user_and_workspace(db_session)
    added = await memory_module.add(
        db_session,
        workspace_id=workspace.id,
        owner_user_id=other_user,
        message="Some private fact about user B.",
    )
    await db_session.commit()
    other_fact_id = added[0].id

    # User A tries to delete user B's fact — should 404.
    response = await v1_client.delete(
        f"/v1/workspaces/{workspace.id}/navigator-memories/{other_fact_id}",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_endpoint_bulk_forget_window(
    v1_client, db_session, seed_workspace, _fake_mem0
) -> None:
    """``POST /forget`` deletes facts captured in the last N days
    for the calling user only. Other users' facts in the same
    workspace stay."""
    user, raw_token, workspace = seed_workspace
    await memory_module.add(
        db_session,
        workspace_id=workspace.id,
        owner_user_id=user.id,
        message="Recent fact about user A's flow.",
    )
    other_user, _ = await _make_user_and_workspace(db_session)
    await memory_module.add(
        db_session,
        workspace_id=workspace.id,
        owner_user_id=other_user,
        message="Recent fact about user B's flow.",
    )
    await db_session.commit()

    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/navigator-memories/forget",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"days": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == 1  # only A's fact, not B's


@pytest.mark.asyncio
async def test_endpoint_bulk_forget_validates_days_range(
    v1_client, seed_workspace, _fake_mem0
) -> None:
    """``days`` must be 1-90 — guards against an accidental
    "forget everything" button."""
    user, raw_token, workspace = seed_workspace
    response = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/navigator-memories/forget",
        headers={"Authorization": f"Bearer {raw_token}"},
        json={"days": 365},
    )
    assert response.status_code == 422, response.text
