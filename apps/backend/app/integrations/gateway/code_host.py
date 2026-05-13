"""Code-host gateway interface — repos, pull requests, contents.

Concrete implementations live under sibling vendor packages
(``backend.app.integrations.github.code_host_adapter`` is the pilot
implementation). Pipelines and resolvers depend on this protocol, not on
the GitHub SDK directly, so adding GitLab or Azure DevOps later is a pure
adapter add.

The reference dataclasses use *discriminated kinds* so a future
``RepoRef(kind="ado", org=..., project=..., repo=...)`` slots in without
breaking ``RepoRef(kind="github", owner=..., repo=...)`` consumers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RepoRef:
    """Vendor-discriminated identifier for a single repository."""

    kind: Literal["github", "gitlab", "azure_devops"]
    owner: str
    repo: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True, slots=True)
class PullRequestRef:
    """Identifier for a single pull/merge request on a code host."""

    repo: RepoRef
    number: int


@dataclass(frozen=True, slots=True)
class BlobContent:
    """One file's contents at a given ref.

    ``encoding`` is ``"utf-8"`` for text blobs and ``"base64"`` for
    binary; the caller decides how to decode. ``sha`` is the vendor's
    blob SHA so indexing pipelines can diff against the last known
    version without re-downloading.
    """

    path: str
    ref: str
    sha: str
    size: int
    encoding: str
    content: str


@dataclass(frozen=True, slots=True)
class RepoSummary:
    """Rich snapshot of a repository for UI listings (picker, dashboard).

    ``RepoRef`` stays minimal because it's a cross-host *identifier*; this
    is the *resource* shape we return when the user actually needs to
    eyeball the list. ``external_id`` is the vendor's numeric id (opaque
    to us), persisted alongside the install so we can de-dupe selections
    even if the user renames the repo on the vendor side.
    """

    ref: RepoRef
    external_id: int | str
    full_name: str
    default_branch: str
    private: bool
    html_url: str
    description: str | None


@runtime_checkable
class CodeHostGateway(Protocol):
    """Operations a code host must support to back our default pipelines.

    Methods are intentionally minimal: each one is a verb the pipelines
    actually use today (`list_repos`, `get_pull_request`, `list_files`).
    Resist the urge to mirror the full vendor SDK here — every method we
    add is a method every future adapter must implement.
    """

    async def list_repos(self) -> list[RepoRef]:
        """Repos visible to the calling identity (App installation, PAT…)."""
        ...

    async def list_repo_summaries(self) -> list[RepoSummary]:
        """Same set as ``list_repos`` but with the metadata the picker UI
        needs (default branch, visibility, html_url, vendor id)."""
        ...

    async def get_pull_request(self, ref: PullRequestRef) -> dict[str, Any]:
        """Return a normalised PR snapshot for the review pipeline.

        We deliberately keep the shape as ``dict[str, Any]`` for the pilot
        — committing to a strict schema before we have two adapters would
        be premature lock-in. Day-3 typing pass tightens this up.
        """
        ...

    async def list_files(self, ref: RepoRef, *, ref_sha: str | None = None) -> list[str]:
        """Flat list of repo paths at ``ref_sha`` (HEAD if omitted).

        Used by the code-map pipeline; truncated upstream to 5k entries on
        very large repos.
        """
        ...

    async def get_blob(
        self,
        ref: RepoRef,
        *,
        path: str,
        ref_sha: str | None = None,
    ) -> BlobContent:
        """Fetch the contents of ``path`` at ``ref_sha`` (HEAD if omitted).

        Used by the agent KB indexer (``.ship/knowledge/**/*.md``) and the
        agent's ``get_repo_file`` tool. Binary files come back with
        ``encoding="base64"`` so callers can refuse to feed binaries to
        the model; text files are already decoded into ``content``.

        Raises :class:`FileNotFoundError` when the path does not exist at
        the requested ref so callers can distinguish "GitHub said no"
        from "GitHub is down".
        """
        ...
