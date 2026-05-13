"""Tests for ``backend.app.services.codeowners`` (RFC-0010 P5-02).

Three suites:

1. **Pure parser** — :func:`parse_codeowners` exercised against
   hand-crafted snippets and the real ``.github/CODEOWNERS`` checked
   into this repo.
2. **Resolution** — :func:`resolve_codeowners` against a seeded
   workspace + a stubbed GitHub fetcher; covers handle/email/team
   classification, case-insensitivity, the unresolved bucket, and
   the "one query, no N+1" guarantee.
3. **Fetch** — :func:`fetch_codeowners` against an
   :class:`httpx.MockTransport` that simulates the three GitHub
   CODEOWNERS locations + 401/403/404 paths.
"""

from __future__ import annotations

import base64
import uuid
from pathlib import Path

import httpx
import pytest

from backend.app.services import codeowners as codeowners_module
from backend.app.services.codeowners import (
    CodeownersResolution,
    CodeownersRule,
    fetch_codeowners,
    parse_codeowners,
    resolve_codeowners,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SHIP_CODEOWNERS = REPO_ROOT / ".github" / "CODEOWNERS"


# ---------------------------------------------------------------------------
# Pure parser tests
# ---------------------------------------------------------------------------


def test_parse_empty_returns_no_rules() -> None:
    assert parse_codeowners("") == ()


def test_parse_global_default_rule() -> None:
    rules = parse_codeowners("*  @owner\n")
    assert rules == (CodeownersRule(path_pattern="*", owners=("@owner",)),)


def test_parse_strips_comments() -> None:
    text = """\
# leading comment
*  @everyone   # tail comment
# trailing comment
"""
    assert parse_codeowners(text) == (
        CodeownersRule(path_pattern="*", owners=("@everyone",)),
    )


def test_parse_preserves_order() -> None:
    text = """\
*           @default
/backend/   @be
/frontend/  @fe
*.md        @docs
"""
    rules = parse_codeowners(text)
    patterns = [r.path_pattern for r in rules]
    assert patterns == ["*", "/backend/", "/frontend/", "*.md"]


def test_parse_handles_team_token() -> None:
    rules = parse_codeowners("/secops/  @org/secops @org/team-lead\n")
    assert rules[0].owners == ("@org/secops", "@org/team-lead")


def test_parse_handles_email_token() -> None:
    rules = parse_codeowners("/billing/  finance@example.com\n")
    assert rules[0].owners == ("finance@example.com",)


def test_parse_skips_section_headers() -> None:
    text = """\
[Section One]
*  @a

[Section Two][2]
/x/  @b
"""
    rules = parse_codeowners(text)
    assert tuple(r.path_pattern for r in rules) == ("*", "/x/")
    assert rules[0].owners == ("@a",)
    assert rules[1].owners == ("@b",)


def test_parse_ignores_blank_lines() -> None:
    text = "\n\n*  @a\n\n\n/x/  @b\n\n"
    assert len(parse_codeowners(text)) == 2


def test_parse_pattern_with_no_owners() -> None:
    rules = parse_codeowners("/legacy/\n")
    assert rules == (CodeownersRule(path_pattern="/legacy/", owners=()),)


def test_parse_passes_caret_negation_through() -> None:
    rules = parse_codeowners("^/vendor/  @nobody\n")
    assert rules[0].path_pattern == "^/vendor/"
    assert rules[0].owners == ("@nobody",)


def test_parse_drops_non_owner_debris() -> None:
    rules = parse_codeowners("/x/  @user 12345 garbage\n")
    assert rules[0].owners == ("@user",)


def test_parse_real_ship_file() -> None:
    """The repo's own CODEOWNERS must parse and contain @denyskuzin."""
    assert SHIP_CODEOWNERS.exists(), "fixture missing — committed in P5-02"
    rules = parse_codeowners(SHIP_CODEOWNERS.read_text())
    assert rules, "expected at least one rule"
    flattened_owners = {owner for rule in rules for owner in rule.owners}
    assert "@denyskuzin" in flattened_owners


# ---------------------------------------------------------------------------
# Resolution test fixtures
# ---------------------------------------------------------------------------


def _stub_fetch(monkeypatch, *, text: str | None, sha: str | None, branch: str):
    """Replace :func:`fetch_codeowners` so resolve tests skip the HTTP path."""

    async def _fake_fetch(*, session, workspace_id, repo_id, client=None, **_kwargs):
        return text, sha, branch

    monkeypatch.setattr(codeowners_module, "fetch_codeowners", _fake_fetch)


async def _seed_member(
    db_session, workspace, *, email: str, display_name: str | None = None
):
    """Insert a fresh user + workspace membership and return the User."""
    from backend.app.db.models.tenancy import User, WorkspaceMember

    user = User(email=email, display_name=display_name)
    db_session.add(user)
    await db_session.flush()
    db_session.add(
        WorkspaceMember(
            workspace_id=workspace.id, user_id=user.id, role="member"
        )
    )
    await db_session.flush()
    return user


# ---------------------------------------------------------------------------
# Resolution tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_known_user_handle(
    db_session, seed_workspace, monkeypatch
) -> None:
    """A handle that matches an email-local-part resolves to the user."""
    _, _, workspace = seed_workspace
    user = await _seed_member(
        db_session,
        workspace,
        email="denyskuzin@example.com",
        display_name="Denys Kuzin",
    )
    _stub_fetch(monkeypatch, text="*  @denyskuzin\n", sha="abc", branch="main")

    result = await resolve_codeowners(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=uuid.uuid4(),
    )

    assert isinstance(result, CodeownersResolution)
    assert result.fetched_from == "main"
    assert result.sha == "abc"
    owners = result.handles_by_path["*"]
    assert len(owners) == 1
    assert owners[0].kind == "user"
    assert owners[0].user_id == user.id
    assert owners[0].label == "Denys Kuzin (@denyskuzin)"
    assert result.unresolved == ()


@pytest.mark.asyncio
async def test_resolve_unknown_handle_added_to_unresolved(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    _stub_fetch(monkeypatch, text="*  @nobody\n", sha=None, branch="main")

    result = await resolve_codeowners(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=uuid.uuid4(),
    )

    assert result.unresolved == ("@nobody",)
    owner = result.handles_by_path["*"][0]
    assert owner.kind == "user"
    assert owner.user_id is None
    assert owner.label == "@nobody"


@pytest.mark.asyncio
async def test_resolve_team_token_no_user_id(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    _stub_fetch(
        monkeypatch, text="/secops/  @org/secops\n", sha=None, branch="main"
    )

    result = await resolve_codeowners(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=uuid.uuid4(),
    )

    owner = result.handles_by_path["/secops/"][0]
    assert owner.kind == "team"
    assert owner.user_id is None
    assert owner.label == "@org/secops"
    # Teams are *not* unresolved — we know we can't resolve them
    # to a single user_id by design.
    assert result.unresolved == ()


@pytest.mark.asyncio
async def test_resolve_email_match(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    user = await _seed_member(
        db_session,
        workspace,
        email="finance@example.com",
        display_name="Finance Team",
    )
    _stub_fetch(
        monkeypatch,
        text="/billing/  finance@example.com\n",
        sha=None,
        branch="main",
    )

    result = await resolve_codeowners(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=uuid.uuid4(),
    )

    owner = result.handles_by_path["/billing/"][0]
    assert owner.kind == "email"
    assert owner.user_id == user.id
    assert "finance@example.com" in owner.label
    assert result.unresolved == ()


@pytest.mark.asyncio
async def test_resolve_handles_case_insensitive(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    user = await _seed_member(
        db_session,
        workspace,
        email="denyskuzin@example.com",
        display_name="Denys Kuzin",
    )
    _stub_fetch(monkeypatch, text="*  @DenysKuzin\n", sha=None, branch="main")

    result = await resolve_codeowners(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=uuid.uuid4(),
    )

    owner = result.handles_by_path["*"][0]
    assert owner.user_id == user.id
    assert owner.label == "Denys Kuzin (@DenysKuzin)"


@pytest.mark.asyncio
async def test_resolve_batched_membership_query(
    db_session, seed_workspace, monkeypatch
) -> None:
    """One SELECT per resolve_codeowners call regardless of token count."""
    _, _, workspace = seed_workspace
    for i in range(5):
        await _seed_member(
            db_session,
            workspace,
            email=f"user{i}@example.com",
            display_name=f"User {i}",
        )
    _stub_fetch(
        monkeypatch,
        text=(
            "*           @user0\n"
            "/backend/   @user1\n"
            "/frontend/  @user2\n"
            "/docs/      @user3\n"
            "/scripts/   @user4\n"
        ),
        sha=None,
        branch="main",
    )

    select_count = 0

    real_load_index = codeowners_module._load_workspace_index

    async def _counting_load_index(*args, **kwargs):
        nonlocal select_count
        select_count += 1
        return await real_load_index(*args, **kwargs)

    monkeypatch.setattr(
        codeowners_module, "_load_workspace_index", _counting_load_index
    )

    result = await resolve_codeowners(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=uuid.uuid4(),
    )

    assert select_count == 1
    # Sanity: every rule got a resolved owner.
    assert all(
        result.handles_by_path[rule.path_pattern][0].user_id is not None
        for rule in result.rules
    )


@pytest.mark.asyncio
async def test_resolve_missing_codeowners(
    db_session, seed_workspace, monkeypatch
) -> None:
    """A repo with no CODEOWNERS at any of the three paths."""
    _, _, workspace = seed_workspace
    _stub_fetch(monkeypatch, text=None, sha=None, branch="main")

    result = await resolve_codeowners(
        session=db_session,
        workspace_id=workspace.id,
        repo_id=uuid.uuid4(),
    )

    assert result.fetched_from == "missing"
    assert result.rules == ()
    assert result.handles_by_path == {}
    assert result.unresolved == ()
    assert result.sha is None


# ---------------------------------------------------------------------------
# Fetch tests
# ---------------------------------------------------------------------------


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


async def _seed_repo_and_install(db_session, workspace) -> uuid.UUID:
    """Insert a :class:`WorkspaceRepo` + :class:`GitHubInstallation`.

    Returns the ``WorkspaceRepo.id``. The installation_id is set
    high enough that we won't collide with another test fixture.
    """
    from backend.app.db.models.integrations import (
        GitHubInstallation,
        WorkspaceRepo,
    )

    install = GitHubInstallation(
        workspace_id=workspace.id,
        installation_id=987654321,
        account_login="acme",
        account_type="Organization",
    )
    db_session.add(install)
    await db_session.flush()

    repo = WorkspaceRepo(
        workspace_id=workspace.id,
        installation_id=install.id,
        provider="github",
        external_id=42,
        full_name="acme/widgets",
        default_branch="main",
        private=False,
        html_url="https://github.com/acme/widgets",
    )
    db_session.add(repo)
    await db_session.flush()
    return repo.id


def _patch_install_token(monkeypatch) -> None:
    async def _fake_token(installation_id, *, settings, client=None):
        return "ghs_test_installation_token"

    monkeypatch.setattr(
        codeowners_module, "fetch_installation_token", _fake_token
    )


def _client_with_handler(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_dot_github_path_first(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    repo_id = await _seed_repo_and_install(db_session, workspace)
    _patch_install_token(monkeypatch)

    seen_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_paths.append(request.url.path)
        if request.url.path.endswith("/contents/.github/CODEOWNERS"):
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": _b64("*  @denyskuzin\n"),
                    "sha": "deadbeef",
                },
            )
        return httpx.Response(404, json={"message": "not found"})

    async with _client_with_handler(handler) as client:
        text, sha, branch = await fetch_codeowners(
            session=db_session,
            workspace_id=workspace.id,
            repo_id=repo_id,
            client=client,
        )

    assert text == "*  @denyskuzin\n"
    assert sha == "deadbeef"
    assert branch == "main"
    assert seen_paths == ["/repos/acme/widgets/contents/.github/CODEOWNERS"]


@pytest.mark.asyncio
async def test_fetch_falls_back_to_root_codeowners(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    repo_id = await _seed_repo_and_install(db_session, workspace)
    _patch_install_token(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/.github/CODEOWNERS"):
            return httpx.Response(404)
        if request.url.path.endswith("/contents/CODEOWNERS"):
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": _b64("*  @root\n"),
                    "sha": "rootsha",
                },
            )
        return httpx.Response(404)

    async with _client_with_handler(handler) as client:
        text, sha, _branch = await fetch_codeowners(
            session=db_session,
            workspace_id=workspace.id,
            repo_id=repo_id,
            client=client,
        )

    assert text == "*  @root\n"
    assert sha == "rootsha"


@pytest.mark.asyncio
async def test_fetch_falls_back_to_docs_codeowners(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    repo_id = await _seed_repo_and_install(db_session, workspace)
    _patch_install_token(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/contents/docs/CODEOWNERS"):
            return httpx.Response(
                200,
                json={
                    "encoding": "base64",
                    "content": _b64("*  @docs-team\n"),
                    "sha": "docssha",
                },
            )
        return httpx.Response(404)

    async with _client_with_handler(handler) as client:
        text, sha, _branch = await fetch_codeowners(
            session=db_session,
            workspace_id=workspace.id,
            repo_id=repo_id,
            client=client,
        )

    assert text == "*  @docs-team\n"
    assert sha == "docssha"


@pytest.mark.asyncio
async def test_fetch_returns_none_when_all_404(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    repo_id = await _seed_repo_and_install(db_session, workspace)
    _patch_install_token(monkeypatch)

    call_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_paths.append(request.url.path)
        return httpx.Response(404, json={"message": "not found"})

    async with _client_with_handler(handler) as client:
        text, sha, branch = await fetch_codeowners(
            session=db_session,
            workspace_id=workspace.id,
            repo_id=repo_id,
            client=client,
        )

    assert text is None
    assert sha is None
    assert branch == "main"
    # All three CODEOWNERS locations were probed before we gave up.
    assert len(call_paths) == 3
    assert call_paths[-1].endswith("/contents/docs/CODEOWNERS")


@pytest.mark.asyncio
async def test_fetch_propagates_auth_error(
    db_session, seed_workspace, monkeypatch
) -> None:
    _, _, workspace = seed_workspace
    repo_id = await _seed_repo_and_install(db_session, workspace)
    _patch_install_token(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "bad creds"})

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        async with _client_with_handler(handler) as client:
            await fetch_codeowners(
                session=db_session,
                workspace_id=workspace.id,
                repo_id=repo_id,
                client=client,
            )

    assert excinfo.value.response.status_code == 401
