"""CI gateway interface — workflow runs, retries, log access.

Pilot adapter: GitHub Actions (covered by the GitHub App). Future:
Buildkite, CircleCI, GitLab CI, Azure Pipelines. The interface stays small
on purpose — adding a method here means every future adapter must support
it, so we add only what the default pipelines actually call.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from backend.app.integrations.gateway.code_host import RepoRef


@runtime_checkable
class CIGateway(Protocol):
    async def list_runs(
        self, repo: RepoRef, *, limit: int = 25
    ) -> list[dict[str, Any]]:
        """Recent workflow runs for ``repo``, newest first.

        Adapters normalise to ``{"id", "name", "status", "conclusion",
        "url", "created_at"}`` at minimum.
        """
        ...

    async def rerun(self, repo: RepoRef, *, run_id: str | int) -> None:
        """Re-trigger ``run_id`` (failed-only by default in the pipeline)."""
        ...

    async def get_logs(
        self, repo: RepoRef, *, run_id: str | int
    ) -> str:
        """Plaintext logs for the run, used as input for self-heal."""
        ...
