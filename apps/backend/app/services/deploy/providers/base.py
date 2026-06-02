"""Provider protocol + shared result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from backend.app.services.deploy.plan import DeployPlan


@dataclass(frozen=True, slots=True)
class ProviderRef:
    """Opaque handle to a deployed app on a specific provider.

    Stored in the ``Deployment`` row so the status-poller knows what to
    query. Both fields are provider-specific strings; callers should not
    parse them.
    """

    provider: str
    app_id: str
    deployment_id: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DeploymentStatus:
    """Current snapshot of a deployment's state.

    ``phase`` mirrors the provider vocabulary (e.g. ``ACTIVE``,
    ``BUILDING``, ``ERROR``). ``terminal`` is True when no further
    phase transitions are expected. ``live_url`` is set once the
    deployment is active.
    """

    phase: str
    terminal: bool
    succeeded: bool
    live_url: str | None = None
    error_message: str | None = None


class DeployProvider(Protocol):
    """Minimal interface every deploy-target adapter must implement.

    Provider-specific logic (spec serialisation, auth, pagination) lives
    in the concrete adapter. The planner and the route handler only see
    this protocol.
    """

    async def apply(
        self,
        plan: DeployPlan,
        *,
        repo_clone_url: str,
        branch: str,
        existing_app_id: str | None = None,
    ) -> ProviderRef:
        """Create (or update) the app from ``plan``.

        ``repo_clone_url`` is the git clone URL for the source repo;
        ``branch`` is the target branch. When ``existing_app_id`` is
        provided the adapter UPDATES that app in place (redeploy) instead
        of creating a new one.

        Returns a :class:`ProviderRef` that can be round-tripped through
        the DB and passed back to :meth:`status`.
        """
        ...

    async def status(self, ref: ProviderRef) -> DeploymentStatus:
        """Return the current status of the deployment identified by ``ref``."""
        ...

    async def health_check(self, url: str, path: str) -> bool:
        """Return True if ``url + path`` responds with HTTP 2xx."""
        ...


__all__ = ["DeploymentStatus", "DeployProvider", "ProviderRef"]
