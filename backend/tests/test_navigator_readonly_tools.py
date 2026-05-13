"""Read-only Navigator tools added in PR-C1 of the tool review (ELS-78).

Three tools, all read-only and DB-backed:

- ``ticket_get(ticket_ref)`` — single-ticket lookup; routes through the
  bound tracker's ``get_ticket_snapshot``. Tested with a stub tracker
  so we don't need a live Linear connection.
- ``dashboard_get()`` — denormalised "what's on my plate?" payload
  composed from priorities + inbox + PRs + recent runs. Tested
  end-to-end with a seeded workspace + fixtures.
- ``audit_search`` — straight DB query against ``audit_log``
  with optional ``action`` / ``target_kind`` / ``target_id`` / ``since``
  filters. Tested with seeded rows.

The shape assertions are deliberately broad — these are denormalised
payloads whose copy may evolve. We pin only the load-bearing keys
that the agent will read.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def toolbox(db_session, seed_workspace):
    from backend.app.core.config import get_settings
    from backend.app.services.agent.tools import ToolBox

    user, _, workspace = seed_workspace
    return ToolBox(
        db_session,
        settings=get_settings(),
        workspace_id=workspace.id,
        user_id=user.id,
    )


# ---------------------------------------------------------------------------
# ticket_get
# ---------------------------------------------------------------------------


class _StubTrackerWithSnapshot:
    """Minimal stub for the tracker resolution path in ``_tool_get_ticket``.

    The handler calls ``self._resolve_tracker(None, None)`` to get a
    tracker object, then ``tracker.get_ticket_snapshot(ref)``. Since
    ``_resolve_tracker`` queries DB integrations, we monkey-patch
    ``_resolve_tracker`` directly and skip the DB dance.
    """

    kind = "linear"

    def __init__(self, snapshot=None) -> None:
        self.snapshot = snapshot
        self.calls = []

    async def get_ticket_snapshot(self, ref):
        self.calls.append(ref)
        return self.snapshot


@pytest.mark.asyncio
async def test_get_ticket_returns_snapshot(toolbox, monkeypatch) -> None:
    stub = _StubTrackerWithSnapshot(
        snapshot={
            "ticket_ref": "ELS-99",
            "title": "Test ticket",
            "description": "body",
            "url": "https://linear.app/elship/issue/ELS-99",
            "state": "In Progress",
            "labels": ["bug"],
            "project_id": "proj-uuid",
        }
    )

    async def _stub_resolve(_self, _kind, _hint):
        return stub

    monkeypatch.setattr(
        type(toolbox), "_resolve_tracker", _stub_resolve, raising=True
    )

    raw = await toolbox._tool_get_ticket({"ticket_ref": "ELS-99"})
    payload = json.loads(raw)
    assert payload["ticket_ref"] == "ELS-99"
    assert payload["title"] == "Test ticket"
    assert payload["state"] == "In Progress"
    assert payload["project_id"] == "proj-uuid"
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_get_ticket_returns_not_found_when_missing(
    toolbox, monkeypatch
) -> None:
    stub = _StubTrackerWithSnapshot(snapshot=None)

    async def _stub_resolve(_self, _kind, _hint):
        return stub

    monkeypatch.setattr(
        type(toolbox), "_resolve_tracker", _stub_resolve, raising=True
    )

    raw = await toolbox._tool_get_ticket({"ticket_ref": "ELS-9999"})
    payload = json.loads(raw)
    assert payload["error"] == "ticket_not_found"
    assert payload["ticket_ref"] == "ELS-9999"


@pytest.mark.asyncio
async def test_get_ticket_requires_ticket_ref(toolbox) -> None:
    from backend.app.services.agent.tools import ToolInvocationError

    with pytest.raises(ToolInvocationError):
        await toolbox._tool_get_ticket({})


# ---------------------------------------------------------------------------
# dashboard_get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dashboard_returns_load_bearing_keys(
    toolbox, db_session, seed_workspace
) -> None:
    """Empty workspace returns the canonical shape with zero counts —
    the agent should be able to phrase a useful answer even without
    seeded data."""
    raw = await toolbox._tool_get_dashboard({})
    payload = json.loads(raw)

    # Top-level keys that the agent will read
    for key in ("now", "priorities", "inbox", "pull_requests", "recent_activity"):
        assert key in payload, f"missing top-level key {key!r}"

    # Priorities buckets (always present, even if empty)
    for bucket in ("active", "planning", "parked"):
        assert bucket in payload["priorities"]
        assert isinstance(payload["priorities"][bucket], list)

    # Inbox shape
    assert "open_total" in payload["inbox"]
    assert "by_type" in payload["inbox"]
    assert "arrived_24h" in payload["inbox"]

    # PRs shape
    assert "open_total" in payload["pull_requests"]
    assert "shipped_24h" in payload["pull_requests"]


@pytest.mark.asyncio
async def test_get_dashboard_groups_priorities_by_state(
    toolbox, db_session, seed_workspace
) -> None:
    from backend.app.db.models.dashboard_priorities import (
        WorkspaceProjectPriority,
    )

    user, _, workspace = seed_workspace
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-active-1",
            ordinal=0,
            state="active",
        )
    )
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-active-2",
            ordinal=1,
            state="active",
        )
    )
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-draft",
            ordinal=2,
            state="planning",
        )
    )
    db_session.add(
        WorkspaceProjectPriority(
            workspace_id=workspace.id,
            project_native_id="proj-parked",
            ordinal=3,
            state="parked",
        )
    )
    await db_session.flush()

    raw = await toolbox._tool_get_dashboard({})
    payload = json.loads(raw)
    active_ids = [
        p["project_native_id"] for p in payload["priorities"]["active"]
    ]
    assert active_ids == ["proj-active-1", "proj-active-2"]
    assert [p["project_native_id"] for p in payload["priorities"]["planning"]] == [
        "proj-draft"
    ]
    assert [p["project_native_id"] for p in payload["priorities"]["parked"]] == [
        "proj-parked"
    ]


@pytest.mark.asyncio
async def test_get_dashboard_inbox_counts(
    toolbox, db_session, seed_workspace
) -> None:
    from backend.app.db.models.inbox import InboxItem

    _, _, workspace = seed_workspace
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=None,
            type="clarification",
            title="x1",
            status="new",
            intake_handle=None,
        )
    )
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=None,
            type="clarification",
            title="x2",
            status="snoozed",
            intake_handle=None,
        )
    )
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=None,
            type="approval",
            title="x3",
            status="new",
            intake_handle=None,
        )
    )
    db_session.add(
        InboxItem(
            workspace_id=workspace.id,
            repo_id=None,
            type="failure",
            title="x4",
            status="dismissed",  # NOT counted (closed)
            intake_handle=None,
        )
    )
    await db_session.flush()

    raw = await toolbox._tool_get_dashboard({})
    payload = json.loads(raw)
    assert payload["inbox"]["open_total"] == 3
    assert payload["inbox"]["by_type"]["clarification"] == 2
    assert payload["inbox"]["by_type"]["approval"] == 1
    assert "failure" not in payload["inbox"]["by_type"]


# ---------------------------------------------------------------------------
# audit_search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_search_returns_recent_rows(
    toolbox, db_session, seed_workspace
) -> None:
    from backend.app.db.models.tenancy import AuditLog

    user, _, workspace = seed_workspace
    now = datetime.now(timezone.utc)

    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            actor_token_id=None,
            action="dashboard.priorities.reorder",
            target_kind="workspace",
            target_id=str(workspace.id),
            payload={"order_count": 3},
        )
    )
    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            actor_token_id=None,
            action="navigator.specialist_consult",
            target_kind="agent_role",
            target_id="designer",
            payload={"task_preview": "review the dashboard"},
        )
    )
    await db_session.flush()

    raw = await toolbox._tool_workspace_audit_search({})
    payload = json.loads(raw)
    actions = [r["action"] for r in payload["audit_log"]]
    assert "dashboard.priorities.reorder" in actions
    assert "navigator.specialist_consult" in actions
    assert payload["count"] == len(payload["audit_log"])


@pytest.mark.asyncio
async def test_audit_search_filters_by_action(
    toolbox, db_session, seed_workspace
) -> None:
    from backend.app.db.models.tenancy import AuditLog

    user, _, workspace = seed_workspace
    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            action="dashboard.priorities.reorder",
            target_kind="workspace",
            target_id=str(workspace.id),
            payload={},
        )
    )
    db_session.add(
        AuditLog(
            workspace_id=workspace.id,
            actor_user_id=user.id,
            action="navigator.specialist_consult",
            target_kind="agent_role",
            target_id="ba",
            payload={},
        )
    )
    await db_session.flush()

    raw = await toolbox._tool_workspace_audit_search(
        {"action": "navigator.specialist_consult"}
    )
    payload = json.loads(raw)
    assert all(
        r["action"] == "navigator.specialist_consult"
        for r in payload["audit_log"]
    )
    assert len(payload["audit_log"]) == 1


@pytest.mark.asyncio
async def test_audit_search_respects_since(
    toolbox, db_session, seed_workspace
) -> None:
    """Rows older than ``since`` must NOT come back. The default
    30-day window is enforced when ``since`` isn't provided."""
    from backend.app.db.models.tenancy import AuditLog

    user, _, workspace = seed_workspace
    old_row = AuditLog(
        workspace_id=workspace.id,
        actor_user_id=user.id,
        action="ancient.event",
        target_kind="workspace",
        target_id=str(workspace.id),
        payload={},
    )
    db_session.add(old_row)
    await db_session.flush()
    # Force the row's timestamp to 60 days ago — past the default
    # 30-day window.
    old_row.created_at = datetime.now(timezone.utc) - timedelta(days=60)
    await db_session.flush()

    raw = await toolbox._tool_workspace_audit_search({})
    payload = json.loads(raw)
    actions = [r["action"] for r in payload["audit_log"]]
    assert "ancient.event" not in actions

    # Explicit since=90 days back — old row comes back.
    raw = await toolbox._tool_workspace_audit_search(
        {"since": (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()}
    )
    payload = json.loads(raw)
    actions = [r["action"] for r in payload["audit_log"]]
    assert "ancient.event" in actions


@pytest.mark.asyncio
async def test_audit_search_validates_filter_types(toolbox) -> None:
    from backend.app.services.agent.tools import ToolInvocationError

    with pytest.raises(ToolInvocationError, match="action must be a string"):
        await toolbox._tool_workspace_audit_search({"action": 42})
    with pytest.raises(
        ToolInvocationError, match="target_kind must be a string"
    ):
        await toolbox._tool_workspace_audit_search({"target_kind": 42})


# ---------------------------------------------------------------------------
# repo_symbols (ELS-72)
#
# Integration tests against the tool dispatcher. The parser itself is
# covered by ``test_symbol_parser.py``; here we pin the handler shape:
# mode selection (paths vs. query), bounds + truncation, ``files_failed``
# / ``skipped_unsupported`` mapping, and the closed set of error
# surfaces. GitHub calls are stubbed at the ``GitHubCodeHost`` boundary
# (``get_blob`` / ``search_code``) so tests stay hermetic.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def toolbox_with_repo(db_session, seed_workspace):
    """Toolbox + an activated repo backed by a stubbed GitHub install.

    Returns ``(toolbox, repo)``. The repo's ``installation_id`` points
    at a real ``GitHubInstallation`` row so ``_resolve_repo_with_install``
    walks the same code path production hits (the seam we monkey-patch
    sits one layer below, at ``GitHubCodeHost``).
    """
    from datetime import datetime, timezone

    from backend.app.core.config import get_settings
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )
    from backend.app.services.agent.tools import ToolBox

    user, _, workspace = seed_workspace
    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=820_072,
        account_login="acme",
        account_type="Organization",
        repository_selection="selected",
        installed_at=datetime.now(timezone.utc),
    )
    db_session.add(install)
    await db_session.flush()
    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=720_072_072,
        full_name="acme/widgets",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/widgets",
        description=None,
        activated_at=datetime.now(timezone.utc),
    )
    db_session.add(repo)
    await db_session.flush()

    toolbox = ToolBox(
        db_session,
        settings=get_settings(),
        workspace_id=workspace.id,
        user_id=user.id,
    )
    return toolbox, repo


def _stub_get_blob(monkeypatch, files: dict[str, str]):
    """Patch ``GitHubCodeHost.get_blob`` to serve ``files`` dict.

    Unknown paths raise ``FileNotFoundError`` — same surface the real
    adapter raises on a GitHub 404. ``calls`` records each fetched
    path so tests can assert call counts (TC-N.8 / TC-N.9).
    """
    from backend.app.integrations.gateway.code_host import BlobContent

    calls: list[str] = []

    async def _get_blob(self, ref, *, path, ref_sha=None):  # noqa: ARG001
        calls.append(path)
        if path not in files:
            raise FileNotFoundError(f"{ref.owner}/{ref.repo}:{path}")
        content = files[path]
        return BlobContent(
            path=path,
            ref=ref_sha or "main",
            sha="cafef00d",
            size=len(content.encode("utf-8")),
            encoding="utf-8",
            content=content,
        )

    monkeypatch.setattr(
        "backend.app.integrations.github.code_host_adapter."
        "GitHubCodeHost.get_blob",
        _get_blob,
    )
    return calls


_PY_SAMPLE = (
    "VERSION = \"1.0\"\n"
    "\n"
    "def helper(x: int) -> int:\n"
    "    return x + 1\n"
    "\n"
    "class Foo:\n"
    "    def bar(self) -> None:\n"
    "        return None\n"
)


def test_dispatcher_maps_repo_symbols_to_handler(toolbox) -> None:
    """TC-1.2: tool name → handler binding (rename safety)."""
    from backend.app.services.agent.tools import ToolBox

    table = toolbox._handlers()
    assert "repo_symbols" in table
    # The bound method's ``__func__`` is the unbound class method —
    # renaming either side breaks this assertion.
    assert table["repo_symbols"].__func__ is ToolBox._tool_repo_symbols


@pytest.mark.asyncio
async def test_repo_symbols_paths_mode_fetches_then_parses(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-2.1 + TC-3.2: paths drives a single ``get_blob`` per file
    and the response keys match the documented contract."""
    toolbox, repo = toolbox_with_repo
    calls = _stub_get_blob(monkeypatch, {"x.py": _PY_SAMPLE})

    raw = await toolbox._tool_repo_symbols(
        {"repo_id": str(repo.id), "paths": ["x.py"]}
    )
    payload = json.loads(raw)

    assert calls == ["x.py"]
    # Top-level contract — every documented key present.
    for key in (
        "repo_id",
        "full_name",
        "query",
        "kinds",
        "supported_extensions",
        "files_requested",
        "files_parsed",
        "matched",
        "truncated",
        "skipped_unsupported",
        "files_failed",
        "symbols",
    ):
        assert key in payload, f"missing top-level key {key!r}"
    assert payload["full_name"] == "acme/widgets"
    assert payload["query"] is None
    assert payload["files_requested"] == 1
    assert payload["files_parsed"] == 1
    assert payload["files_failed"] is None
    assert payload["skipped_unsupported"] is None
    assert sorted(payload["supported_extensions"]) == [
        ".go",
        ".py",
        ".ts",
        ".tsx",
    ]
    names = {row["symbol"] for row in payload["symbols"]}
    assert {"helper", "Foo", "bar", "VERSION"} <= names
    # TC-3.1: every row has the documented keys with correct types.
    for row in payload["symbols"]:
        assert set(row.keys()) == {"file", "symbol", "kind", "line", "signature"}
        assert isinstance(row["line"], int) and row["line"] >= 1
        assert isinstance(row["signature"], str)
        assert "\n" not in row["signature"]
        assert len(row["signature"]) <= 240
        assert row["kind"] in {
            "function",
            "class",
            "method",
            "interface",
            "type",
            "struct",
            "enum",
            "var",
            "const",
        }


@pytest.mark.asyncio
async def test_repo_symbols_unactivated_repo_raises(
    toolbox_with_repo,
) -> None:
    """TC-2.2: a UUID not bound to this workspace surfaces the same
    error ``_tool_repo_file_get`` raises (no partial JSON leak)."""
    from backend.app.services.agent.tools import ToolInvocationError

    toolbox, _ = toolbox_with_repo
    bogus = uuid.uuid4()
    with pytest.raises(ToolInvocationError, match="not activated"):
        await toolbox._tool_repo_symbols(
            {"repo_id": str(bogus), "paths": ["x.py"]}
        )


@pytest.mark.asyncio
async def test_repo_symbols_requires_paths_or_query(
    toolbox_with_repo,
) -> None:
    """TC-N.1: ``{repo_id}`` only → ``ToolInvocationError``."""
    from backend.app.services.agent.tools import ToolInvocationError

    toolbox, repo = toolbox_with_repo
    with pytest.raises(ToolInvocationError, match="paths.*query"):
        await toolbox._tool_repo_symbols({"repo_id": str(repo.id)})


@pytest.mark.asyncio
async def test_repo_symbols_whitespace_query_treated_as_empty(
    toolbox_with_repo,
) -> None:
    """TC-N.15: whitespace-only ``query`` without ``paths`` is rejected
    by the same branch that handles missing ``query``."""
    from backend.app.services.agent.tools import ToolInvocationError

    toolbox, repo = toolbox_with_repo
    with pytest.raises(ToolInvocationError, match="paths.*query"):
        await toolbox._tool_repo_symbols(
            {"repo_id": str(repo.id), "query": "   "}
        )


@pytest.mark.asyncio
async def test_repo_symbols_invalid_repo_id_raises(
    toolbox_with_repo,
) -> None:
    """TC-N.14: bad UUID → ``ToolInvocationError`` (from ``_parse_uuid``)."""
    from backend.app.services.agent.tools import ToolInvocationError

    toolbox, _ = toolbox_with_repo
    with pytest.raises(ToolInvocationError, match="invalid UUID"):
        await toolbox._tool_repo_symbols(
            {"repo_id": "not-a-uuid", "paths": ["x.py"]}
        )


@pytest.mark.asyncio
async def test_repo_symbols_unsupported_paths_listed_not_parsed(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-N.2: ``README.md`` lands in ``skipped_unsupported``; the
    supported peer in the same list is parsed (no fetch for the .md)."""
    toolbox, repo = toolbox_with_repo
    calls = _stub_get_blob(monkeypatch, {"x.py": _PY_SAMPLE})

    raw = await toolbox._tool_repo_symbols(
        {"repo_id": str(repo.id), "paths": ["README.md", "x.py"]}
    )
    payload = json.loads(raw)
    assert payload["skipped_unsupported"] == ["README.md"]
    # README.md must NOT have been fetched — the matcher bails on
    # extension before the gateway call.
    assert calls == ["x.py"]
    assert payload["files_parsed"] == 1
    assert payload["files_failed"] is None


@pytest.mark.asyncio
async def test_repo_symbols_get_blob_not_found_reason(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-N.3: ``FileNotFoundError`` → ``reason="not_found"``; batch continues."""
    toolbox, repo = toolbox_with_repo
    _stub_get_blob(monkeypatch, {"x.py": _PY_SAMPLE})  # missing 'gone.py'

    raw = await toolbox._tool_repo_symbols(
        {"repo_id": str(repo.id), "paths": ["gone.py", "x.py"]}
    )
    payload = json.loads(raw)
    assert payload["files_failed"] == [
        {"path": "gone.py", "reason": "not_found"}
    ]
    assert payload["files_parsed"] == 1


@pytest.mark.asyncio
async def test_repo_symbols_get_blob_directory_reason(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-N.4: ``IsADirectoryError`` → ``reason="directory"``."""
    toolbox, repo = toolbox_with_repo

    async def _get_blob(self, ref, *, path, ref_sha=None):  # noqa: ARG001
        raise IsADirectoryError(f"{ref.owner}/{ref.repo}:{path}")

    monkeypatch.setattr(
        "backend.app.integrations.github.code_host_adapter."
        "GitHubCodeHost.get_blob",
        _get_blob,
    )

    raw = await toolbox._tool_repo_symbols(
        {"repo_id": str(repo.id), "paths": ["src/"]}
    )
    payload = json.loads(raw)
    # ``src/`` has no recognised extension → it ends up in
    # ``skipped_unsupported`` and ``get_blob`` is never even called.
    # The handler relies on the extension check first so the directory
    # branch is exercised by a path with a known extension that
    # resolves to a directory server-side.
    assert payload["skipped_unsupported"] == ["src/"]


@pytest.mark.asyncio
async def test_repo_symbols_get_blob_directory_with_known_extension(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-N.4 (real path): a ``.py`` path that resolves to a directory
    on GitHub maps to ``reason="directory"``."""
    toolbox, repo = toolbox_with_repo

    async def _get_blob(self, ref, *, path, ref_sha=None):  # noqa: ARG001
        raise IsADirectoryError(f"{ref.owner}/{ref.repo}:{path}")

    monkeypatch.setattr(
        "backend.app.integrations.github.code_host_adapter."
        "GitHubCodeHost.get_blob",
        _get_blob,
    )

    raw = await toolbox._tool_repo_symbols(
        {"repo_id": str(repo.id), "paths": ["pkg.py"]}
    )
    payload = json.loads(raw)
    assert payload["files_failed"] == [
        {"path": "pkg.py", "reason": "directory"}
    ]


@pytest.mark.asyncio
async def test_repo_symbols_binary_blob_reason(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-N.5: non-utf-8 encoding → ``reason="binary"``, no parse."""
    from backend.app.integrations.gateway.code_host import BlobContent

    toolbox, repo = toolbox_with_repo

    async def _get_blob(self, ref, *, path, ref_sha=None):  # noqa: ARG001
        return BlobContent(
            path=path,
            ref=ref_sha or "main",
            sha="abc",
            size=4,
            encoding="base64",
            content="AAAA",
        )

    monkeypatch.setattr(
        "backend.app.integrations.github.code_host_adapter."
        "GitHubCodeHost.get_blob",
        _get_blob,
    )

    raw = await toolbox._tool_repo_symbols(
        {"repo_id": str(repo.id), "paths": ["bin.py"]}
    )
    payload = json.loads(raw)
    assert payload["files_failed"] == [{"path": "bin.py", "reason": "binary"}]
    assert payload["symbols"] == []


@pytest.mark.asyncio
async def test_repo_symbols_fetch_error_reason(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-N.6: arbitrary exception → ``reason="fetch_error"``."""
    toolbox, repo = toolbox_with_repo

    async def _get_blob(self, ref, *, path, ref_sha=None):  # noqa: ARG001
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "backend.app.integrations.github.code_host_adapter."
        "GitHubCodeHost.get_blob",
        _get_blob,
    )

    raw = await toolbox._tool_repo_symbols(
        {"repo_id": str(repo.id), "paths": ["x.py"]}
    )
    payload = json.loads(raw)
    assert payload["files_failed"] == [{"path": "x.py", "reason": "fetch_error"}]


@pytest.mark.asyncio
async def test_repo_symbols_search_code_failure_raises(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-N.7: query mode + code-search blow-up → ``ToolInvocationError``."""
    from backend.app.services.agent.tools import ToolInvocationError

    toolbox, repo = toolbox_with_repo

    async def _search_code(self, ref, *, query, limit, **_):  # noqa: ARG001
        raise RuntimeError("rate limited")

    monkeypatch.setattr(
        "backend.app.integrations.github.code_host_adapter."
        "GitHubCodeHost.search_code",
        _search_code,
    )

    with pytest.raises(ToolInvocationError, match="code search failed"):
        await toolbox._tool_repo_symbols(
            {"repo_id": str(repo.id), "query": "Foo"}
        )


@pytest.mark.asyncio
async def test_repo_symbols_query_substring_case_insensitive(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-N.10: when ``paths`` + ``query`` are both set, per-row filter
    applies (case-insensitive substring)."""
    src = (
        "def FooBar(): pass\n"
        "def fooBaz(): pass\n"
        "def Unrelated(): pass\n"
    )
    toolbox, repo = toolbox_with_repo
    _stub_get_blob(monkeypatch, {"x.py": src})

    raw = await toolbox._tool_repo_symbols(
        {"repo_id": str(repo.id), "paths": ["x.py"], "query": "foo"}
    )
    payload = json.loads(raw)
    names = sorted(row["symbol"] for row in payload["symbols"])
    assert names == ["FooBar", "fooBaz"]
    assert payload["query"] == "foo"


@pytest.mark.asyncio
async def test_repo_symbols_kinds_filter(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-N.11: ``kinds=[function]`` drops ``class`` / ``method`` rows."""
    toolbox, repo = toolbox_with_repo
    _stub_get_blob(monkeypatch, {"x.py": _PY_SAMPLE})

    raw = await toolbox._tool_repo_symbols(
        {
            "repo_id": str(repo.id),
            "paths": ["x.py"],
            "kinds": ["function"],
        }
    )
    payload = json.loads(raw)
    assert {r["kind"] for r in payload["symbols"]} == {"function"}
    assert payload["kinds"] == ["function"]


@pytest.mark.asyncio
async def test_repo_symbols_limit_truncates_and_early_exits(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-N.8: hitting ``limit`` flips ``truncated=true`` and stops the
    outer file loop so we don't fetch files we'd discard."""
    many = "\n".join(f"def f{i}(): pass" for i in range(200)) + "\n"
    toolbox, repo = toolbox_with_repo
    calls = _stub_get_blob(monkeypatch, {"a.py": many, "b.py": many})

    raw = await toolbox._tool_repo_symbols(
        {
            "repo_id": str(repo.id),
            "paths": ["a.py", "b.py"],
            "limit": 5,
        }
    )
    payload = json.loads(raw)
    assert len(payload["symbols"]) == 5
    assert payload["truncated"] is True
    # ``b.py`` must not be fetched — the outer loop short-circuits as
    # soon as ``a.py`` saturates the budget.
    assert calls == ["a.py"]


@pytest.mark.asyncio
async def test_repo_symbols_max_files_caps_fetches(
    toolbox_with_repo, monkeypatch
) -> None:
    """TC-N.9: query mode never fetches more files than ``max_files``,
    even when code-search hands back a longer list."""
    toolbox, repo = toolbox_with_repo
    calls = _stub_get_blob(
        monkeypatch,
        {
            "a.py": "def Foo(): pass\n",
            "b.py": "def Foo(): pass\n",
            "c.py": "def Foo(): pass\n",
        },
    )

    async def _search_code(self, ref, *, query, limit, **_):  # noqa: ARG001
        return [{"path": p} for p in ("a.py", "b.py", "c.py", "d.py")]

    monkeypatch.setattr(
        "backend.app.integrations.github.code_host_adapter."
        "GitHubCodeHost.search_code",
        _search_code,
    )

    raw = await toolbox._tool_repo_symbols(
        {"repo_id": str(repo.id), "query": "Foo", "max_files": 2}
    )
    payload = json.loads(raw)
    assert len(calls) <= 2
    assert payload["files_requested"] == 2
