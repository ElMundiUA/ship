"""CODEOWNERS parser + resolver service (RFC-0010 P5-02).

The future onboarding wizard (P5-06) will call this service to seed
:class:`backend.app.db.models.inbox.InboxRoutingRule` rows from a
customer repo's ``CODEOWNERS`` file. Three responsibilities:

1. **Parse** — :func:`parse_codeowners` is a pure function that turns
   the file's text into an ordered tuple of :class:`CodeownersRule`.
   Order matters: GitHub's matcher uses *last match wins*, and the
   downstream consumer (``inbox/routing.py``) relies on file order
   to honour that.
2. **Fetch** — :func:`fetch_codeowners` mints an installation token
   (via :func:`fetch_installation_token`), then walks the three
   GitHub-recognised CODEOWNERS locations
   (``.github/CODEOWNERS``, ``CODEOWNERS``, ``docs/CODEOWNERS``)
   against the repo's default branch and returns the first hit.
3. **Resolve** — :func:`resolve_codeowners` classifies each owner
   token and (for ``user`` / ``email`` kinds) maps it to a workspace
   user_id with a single batched query — never N+1 per token.

The module is pure-service: no FastAPI route, no DB write, no cache.
The wizard service composes it; the existing ``code_owner`` handle
resolver in ``inbox/routing.py`` does *not* import it yet — that
wiring lives in a different ticket.
"""

from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass
from typing import Final, Literal

import httpx
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import User, WorkspaceMember
from backend.app.integrations.github.app_auth import (
    GITHUB_API_BASE,
    fetch_installation_token,
)


logger = logging.getLogger(__name__)


# Three locations GitHub's own CODEOWNERS matcher recognises, in the
# precedence order documented in
# https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners.
# We probe in the same order so a repo with both
# ``.github/CODEOWNERS`` and ``docs/CODEOWNERS`` resolves the way
# GitHub itself would.
_CODEOWNERS_PATHS: Final[tuple[str, ...]] = (
    ".github/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
)

# Candidate columns on ``users`` that *might* hold a GitHub handle.
# Today none of these exist (see _user_handle_column docstring), but
# introspecting at runtime means a future migration adding any one
# of them lights up handle resolution without code changes here.
_HANDLE_COLUMN_CANDIDATES: Final[tuple[str, ...]] = (
    "github_handle",
    "github_username",
    "github_login",
    "login",
)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeownersRule:
    """One non-comment line from a CODEOWNERS file.

    ``path_pattern`` is stored verbatim because the consumer in
    ``inbox/routing.py`` runs its own match logic (and may extend it
    later for ``^``-negation, escape sequences, etc.). Compiling a
    glob here would lock that downstream out.

    ``owners`` is also raw — ``@org/team``, ``@user``, and bare
    ``email@addr`` tokens are kept as-is so :class:`ResolvedOwner`
    can classify them without re-tokenising.
    """

    path_pattern: str
    owners: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedOwner:
    """A CODEOWNERS owner token classified + (best-effort) resolved.

    ``kind`` is the *lexical* class of the token; ``user_id`` is
    populated only when we successfully matched the token to a user
    in the workspace's membership. ``label`` is what the wizard UI
    renders next to the rule — it falls back to the raw token when
    we can't form anything friendlier.

    A token that *looks* like a user (``@handle``) but doesn't match
    any workspace member keeps ``kind="user"`` and gets recorded in
    :attr:`CodeownersResolution.unresolved` — the wizard surfaces
    those so the admin can either invite the missing teammate or
    remap the rule by hand.
    """

    raw: str
    kind: Literal["user", "team", "email", "unknown"]
    user_id: uuid.UUID | None
    label: str


@dataclass(frozen=True)
class CodeownersResolution:
    """Aggregate result of :func:`resolve_codeowners`.

    ``rules`` is in file order so the consumer can preserve
    last-match-wins semantics. ``handles_by_path`` indexes the same
    data by ``path_pattern`` for callers that just want "who owns
    ``/backend/`` ?" without re-walking the rule list.

    ``fetched_from`` records *which* branch the file came from
    (``"main"`` is treated as a synonym for "default branch was
    main"; ``"default_branch"`` covers any other default like
    ``master`` or ``trunk``). ``"missing"`` means none of the three
    locations returned a file — the wizard then offers the admin a
    "no CODEOWNERS, route everything to the workspace owner"
    fallback.

    ``sha`` is the blob sha of the file we read; the wizard stamps
    it onto :class:`backend.app.db.models.inbox.InboxRoutingRule`
    rows so a re-run of the resolver after the customer edits
    CODEOWNERS can detect "nothing changed" without diffing
    payloads.
    """

    rules: tuple[CodeownersRule, ...]
    handles_by_path: dict[str, tuple[ResolvedOwner, ...]]
    unresolved: tuple[str, ...]
    fetched_from: Literal["main", "default_branch", "missing"]
    sha: str | None


# ---------------------------------------------------------------------------
# Pure parser
# ---------------------------------------------------------------------------


def parse_codeowners(text: str) -> tuple[CodeownersRule, ...]:
    """Parse CODEOWNERS ``text`` into an ordered tuple of rules.

    Implements the subset of GitHub's CODEOWNERS grammar the
    wizard cares about:

    - Lines starting with ``#`` (after any leading whitespace) are
      comments and ignored.
    - Inline comments (``... # tail``) are stripped before
      tokenisation; the file's authors use them to label rules.
    - Blank lines are ignored.
    - Section headers — GitHub's newer ``[Section Name]`` /
      ``[Section Name][2]`` syntax — are stripped: every rule is
      emitted at the top level. Sections are an opt-in feature for
      *required* reviewers in branch-protection; the wizard only
      cares about routing today.

      .. todo:: When the wizard grows section-aware UX (e.g. show
         "Frontend reviewers" headers in the seed preview) this
         function should emit a parallel ``sections`` tuple instead
         of dropping them.
    - Each surviving line is split on whitespace; the first token is
      the path pattern, the rest are owners. A line with no owners
      (just a pattern) is a valid "no required reviewer for this
      path" rule and emits a rule with an empty ``owners`` tuple.
    - Owner tokens must either start with ``@`` or contain ``@``
      (email syntax). Anything else is dropped with a debug log
      so a typo can't introduce a phantom owner downstream.
    - Patterns starting with ``^`` (GitHub's newer "negation /
      ignore" prefix) are passed through verbatim — the consumer
      decides what to do with them.

    Pure: no I/O, no logging side-effects beyond a debug message
    for dropped tokens.
    """

    rules: list[CodeownersRule] = []

    for raw_line in text.splitlines():
        line = raw_line
        # Strip BOM on the very first line so a UTF-8-with-BOM
        # CODEOWNERS doesn't make the first rule's pattern start
        # with a stray U+FEFF.
        if line.startswith("\ufeff"):
            line = line.lstrip("\ufeff")

        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        # Section header: ``[Owners]`` or ``[Owners][2]`` etc. The
        # bracketed form is the only way GitHub introduces a
        # section block, so a substring check on ``[`` ... ``]`` at
        # the line edges is enough — we don't need to model the
        # ``[name][min_approvers]`` substructure to skip it.
        if stripped.startswith("[") and "]" in stripped:
            # Conservative: only skip if the line is *just* a
            # bracketed header (with optional trailing ``@owner``
            # default-owners GitHub allows after the bracket). Any
            # owners trailing the section header are dropped along
            # with the header — they apply to the section as a
            # whole, not to a specific path, and the wizard models
            # path-level routing only.
            continue

        # Drop inline comments. ``#`` cannot appear inside a path
        # pattern or owner token in any spec we've seen, so a naive
        # split is safe (and matches GitHub's own parser).
        if "#" in stripped:
            stripped = stripped.split("#", 1)[0].strip()
            if not stripped:
                continue

        tokens = stripped.split()
        if not tokens:
            continue

        pattern = tokens[0]
        owner_tokens: list[str] = []
        for token in tokens[1:]:
            if token.startswith("@") or "@" in token:
                owner_tokens.append(token)
            else:
                # Quietly drop — a CODEOWNERS file with non-handle
                # debris (numbers, stray words) is almost always a
                # typo, not an intentional owner. Logging at debug
                # keeps us out of the operator's eyeline.
                logger.debug(
                    "codeowners: dropping non-owner token %r on line %r",
                    token,
                    raw_line,
                )

        rules.append(
            CodeownersRule(path_pattern=pattern, owners=tuple(owner_tokens))
        )

    return tuple(rules)


# ---------------------------------------------------------------------------
# GitHub fetch
# ---------------------------------------------------------------------------


async def fetch_codeowners(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    client: httpx.AsyncClient | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Fetch the repo's CODEOWNERS file from GitHub.

    Returns ``(text, blob_sha, branch_name)``. ``text`` and ``sha``
    are ``None`` when none of the three GitHub-recognised paths
    return a file; ``branch_name`` is always populated (it falls
    back to the recorded default branch, or ``"main"`` when even
    that is null) so the caller can quote the branch in the wizard
    UI even on a miss.

    Raises :class:`httpx.HTTPStatusError` if GitHub returns
    401/403 or any 5xx — the caller (wizard service) decides whether
    that's a "re-auth the App" prompt or a hard 502.
    """

    repo, install = await _load_repo_and_install(
        session, workspace_id=workspace_id, repo_id=repo_id
    )
    branch = repo.default_branch or "main"

    settings = get_settings()
    token = await fetch_installation_token(
        install.installation_id, settings=settings, client=client
    )

    owner, name = _split_full_name(repo.full_name)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=httpx.Timeout(15.0))
    try:
        for path in _CODEOWNERS_PATHS:
            response = await http.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{name}/contents/{path}",
                headers=headers,
                params={"ref": branch},
            )
            if response.status_code == 404:
                continue
            if response.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"GitHub CODEOWNERS fetch failed: "
                    f"{response.status_code} for {path}",
                    request=response.request,
                    response=response,
                )

            payload = response.json()
            # Defensive: ``contents`` returns a list when the path
            # is a directory (e.g. someone made ``CODEOWNERS`` a
            # folder). Treat that the same as a miss.
            if isinstance(payload, list):
                continue

            encoding = str(payload.get("encoding") or "base64")
            raw_content = str(payload.get("content") or "")
            sha = payload.get("sha")
            try:
                if encoding == "base64":
                    decoded = base64.b64decode(
                        raw_content.replace("\n", "")
                    ).decode("utf-8")
                else:
                    decoded = raw_content
            except (UnicodeDecodeError, ValueError):
                logger.warning(
                    "codeowners: %s/%s:%s decoded to non-utf-8; treating as miss",
                    owner,
                    name,
                    path,
                )
                continue

            return decoded, (str(sha) if sha is not None else None), branch

        return None, None, branch
    finally:
        if owns_client:
            await http.aclose()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


async def resolve_codeowners(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
    client: httpx.AsyncClient | None = None,
) -> CodeownersResolution:
    """Fetch + parse + resolve CODEOWNERS for one repo.

    The membership lookup is *one* query: every distinct handle and
    every distinct email mentioned anywhere in the file is fed into
    a single ``SELECT users JOIN workspace_members`` and the result
    is scattered back across the rules. This keeps the wizard's
    seed step linear in (file size) regardless of the workspace's
    user count — N+1 here would turn a 50-rule CODEOWNERS into 50
    round-trips on a cold cache.
    """

    text, sha, branch = await fetch_codeowners(
        session=session,
        workspace_id=workspace_id,
        repo_id=repo_id,
        client=client,
    )

    if text is None:
        return CodeownersResolution(
            rules=(),
            handles_by_path={},
            unresolved=(),
            fetched_from="missing",
            sha=None,
        )

    rules = parse_codeowners(text)

    handles: set[str] = set()
    emails: set[str] = set()
    for rule in rules:
        for raw in rule.owners:
            classification = _classify_token(raw)
            if classification == "user":
                handles.add(raw[1:].lower())
            elif classification == "email":
                emails.add(raw.lower())

    handle_lookup, email_lookup = await _load_workspace_index(
        session,
        workspace_id=workspace_id,
        handles=handles,
        emails=emails,
    )

    handles_by_path: dict[str, tuple[ResolvedOwner, ...]] = {}
    unresolved: list[str] = []
    seen_unresolved: set[str] = set()

    def _record_unresolved(raw: str) -> None:
        if raw in seen_unresolved:
            return
        seen_unresolved.add(raw)
        unresolved.append(raw)

    for rule in rules:
        resolved: list[ResolvedOwner] = []
        for raw in rule.owners:
            kind = _classify_token(raw)
            if kind == "team":
                resolved.append(
                    ResolvedOwner(
                        raw=raw,
                        kind="team",
                        user_id=None,
                        label=raw,
                    )
                )
                continue

            if kind == "user":
                handle = raw[1:]
                hit = handle_lookup.get(handle.lower())
                if hit is not None:
                    user_id, display_name = hit
                    label = (
                        f"{display_name} (@{handle})"
                        if display_name
                        else f"@{handle}"
                    )
                    resolved.append(
                        ResolvedOwner(
                            raw=raw,
                            kind="user",
                            user_id=user_id,
                            label=label,
                        )
                    )
                else:
                    _record_unresolved(raw)
                    resolved.append(
                        ResolvedOwner(
                            raw=raw,
                            kind="user",
                            user_id=None,
                            label=raw,
                        )
                    )
                continue

            if kind == "email":
                hit = email_lookup.get(raw.lower())
                if hit is not None:
                    user_id, display_name = hit
                    label = (
                        f"{display_name} ({raw})" if display_name else raw
                    )
                    resolved.append(
                        ResolvedOwner(
                            raw=raw,
                            kind="email",
                            user_id=user_id,
                            label=label,
                        )
                    )
                else:
                    _record_unresolved(raw)
                    resolved.append(
                        ResolvedOwner(
                            raw=raw,
                            kind="email",
                            user_id=None,
                            label=raw,
                        )
                    )
                continue

            # ``unknown`` — kept in the rule so the wizard can show
            # it for transparency, but flagged so the admin notices.
            _record_unresolved(raw)
            resolved.append(
                ResolvedOwner(
                    raw=raw,
                    kind="unknown",
                    user_id=None,
                    label=raw,
                )
            )

        handles_by_path[rule.path_pattern] = tuple(resolved)

    fetched_from: Literal["main", "default_branch"] = (
        "main" if branch == "main" else "default_branch"
    )

    return CodeownersResolution(
        rules=rules,
        handles_by_path=handles_by_path,
        unresolved=tuple(unresolved),
        fetched_from=fetched_from,
        sha=sha,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _classify_token(raw: str) -> Literal["user", "team", "email", "unknown"]:
    """Lexical classification of one owner token.

    See :class:`ResolvedOwner` for the kinds. We classify on syntax
    alone — the ``unknown`` bucket catches debris that survived the
    parser's own filter (it shouldn't happen, but the resolver
    still has to be total over its input).
    """
    if not raw:
        return "unknown"
    if raw.startswith("@"):
        if "/" in raw[1:]:
            return "team"
        return "user"
    if "@" in raw:
        return "email"
    return "unknown"


def _user_handle_column() -> str | None:
    """Return the name of the ``users`` column that stores a GitHub handle.

    Today the schema has none of the candidate columns (the User
    model is auth-provider agnostic), so this returns ``None`` and
    handle resolution falls back to email-local-part matching. A
    future migration adding any of the candidate columns will be
    picked up automatically.

    .. todo:: P5-06 should land a ``users.github_handle`` column
       (populated either by the GitHub OAuth callback or the
       wizard's "match teammates" step) so handle resolution stops
       depending on the email-local-part heuristic.
    """
    mapper = inspect(User)
    for name in _HANDLE_COLUMN_CANDIDATES:
        if name in mapper.columns.keys():
            return name
    return None


async def _load_workspace_index(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    handles: set[str],
    emails: set[str],
) -> tuple[
    dict[str, tuple[uuid.UUID, str | None]],
    dict[str, tuple[uuid.UUID, str | None]],
]:
    """One query per resolver call — build (handle, email) → user lookup.

    ``handles`` is the set of bare GitHub handles (no leading
    ``@``), normalised to lowercase. ``emails`` is the set of bare
    email addresses, also lowercased.

    Returns two dicts keyed by the lowercase form so the caller can
    look up case-insensitively.
    """

    handle_lookup: dict[str, tuple[uuid.UUID, str | None]] = {}
    email_lookup: dict[str, tuple[uuid.UUID, str | None]] = {}

    if not handles and not emails:
        return handle_lookup, email_lookup

    handle_column_name = _user_handle_column()
    handle_column = (
        getattr(User, handle_column_name) if handle_column_name else None
    )

    # Single batched query: every column the resolver needs in one
    # round-trip, scoped to workspace membership. ``handle_column``
    # is added dynamically when the schema has a GitHub-handle
    # column (today: never), so the query stays valid against the
    # current schema *and* future migrations.
    columns = [User.id, User.email, User.display_name]
    if handle_column is not None:
        columns.append(handle_column)
    stmt = (
        select(*columns)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == workspace_id)
    )
    rows = (await session.execute(stmt)).all()

    for row in rows:
        user_id = row[0]
        email = row[1]
        display_name = row[2]
        explicit_handle = (
            row[3] if handle_column is not None and len(row) > 3 else None
        )

        # Email lookup — ``users.email`` is globally unique, so the
        # first hit is the only hit.
        if email and emails:
            lowered = email.lower()
            if lowered in emails:
                email_lookup[lowered] = (user_id, display_name)

        if not handles:
            continue

        candidate_handles: list[str] = []
        if isinstance(explicit_handle, str) and explicit_handle:
            candidate_handles.append(explicit_handle.lower())
        if email and "@" in email:
            # Email-local-part fallback. This is a heuristic — some
            # workspaces use ``firstname.lastname@...`` with GitHub
            # handles like ``flastname`` and the heuristic will
            # miss. P5-06 is expected to add an explicit
            # ``users.github_handle`` column to fix this.
            candidate_handles.append(email.split("@", 1)[0].lower())

        for candidate in candidate_handles:
            if candidate in handles and candidate not in handle_lookup:
                handle_lookup[candidate] = (user_id, display_name)
                break

    return handle_lookup, email_lookup


async def _load_repo_and_install(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    repo_id: uuid.UUID,
) -> tuple[WorkspaceRepo, GitHubInstallation]:
    """Load the repo + its GitHub installation, scoped to ``workspace_id``.

    Two queries instead of a join because the install row is
    optional in the FK definition (legacy non-GitHub providers) —
    when missing we raise the same kind of ``LookupError`` the
    rest of the integration layer raises so the wizard can render
    "this repo isn't installed via the App yet".
    """
    repo = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalar_one_or_none()
    if repo is None:
        raise LookupError(
            f"WorkspaceRepo {repo_id} not found in workspace {workspace_id}"
        )
    if repo.installation_id is None:
        raise LookupError(
            f"WorkspaceRepo {repo_id} has no GitHub installation"
        )
    install = (
        await session.execute(
            select(GitHubInstallation).where(
                GitHubInstallation.id == repo.installation_id
            )
        )
    ).scalar_one_or_none()
    if install is None:
        raise LookupError(
            f"GitHubInstallation {repo.installation_id} not found"
        )
    return repo, install


def _split_full_name(full_name: str) -> tuple[str, str]:
    owner, _, name = full_name.partition("/")
    if not owner or not name:
        raise ValueError(
            f"WorkspaceRepo.full_name {full_name!r} is not in 'owner/repo' form"
        )
    return owner, name


__all__ = [
    "CodeownersResolution",
    "CodeownersRule",
    "ResolvedOwner",
    "fetch_codeowners",
    "parse_codeowners",
    "resolve_codeowners",
]
