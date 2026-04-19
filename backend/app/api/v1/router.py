"""Top-level router for the v1 API.

Mounted by ``backend.app.main`` under ``/v1``. Existing unversioned routes
(``/patterns``, ``/tools``, ``/workflows``, ``/collections``, ``/search``,
``/fetch``, ``/feedback``, ``/telemetry``) stay where they are for the
already-released ``@elmundi/ship-cli``.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.v1.routes import (
    artifact_repos,
    auth,
    health,
    integrations,
    knowledge,
    members,
    onboarding,
    workspace_artifacts,
    workspaces,
)


api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(workspaces.router)
api_router.include_router(artifact_repos.router)
api_router.include_router(integrations.router)
api_router.include_router(knowledge.router)
api_router.include_router(members.router)
api_router.include_router(onboarding.router)
api_router.include_router(workspace_artifacts.router)
