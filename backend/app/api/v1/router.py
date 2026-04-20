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
    audit,
    auth,
    catalog,
    dashboard,
    github_app,
    health,
    integrations,
    knowledge,
    linear_oauth,
    members,
    notion_oauth,
    pipelines,
    repos,
    workspace_artifacts,
    workspaces,
)


api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(workspaces.router)
api_router.include_router(artifact_repos.router)
api_router.include_router(audit.router)
api_router.include_router(integrations.router)
api_router.include_router(knowledge.router)
api_router.include_router(members.router)
api_router.include_router(workspace_artifacts.router)
# Workspace repo activations (pilot Day 2 — picker UI + Code Map MVP).
# Lives next to artifact_repos but is keyed off GitHub App installations
# instead of paste-URL clones.
api_router.include_router(repos.router)
# GitHub App OAuth + webhooks (pilot WOW-onboarding flow). Webhook route
# is public; install start/callback do their own auth.
api_router.include_router(github_app.router)
# Linear OAuth (pilot Day 2 — tracker WOW flow). Callback is public so
# the browser redirect from Linear can hit it without a session.
api_router.include_router(linear_oauth.router)
# Notion OAuth (pilot Day 2 — tracker WOW flow). Same shape as Linear.
api_router.include_router(notion_oauth.router)
# Pipelines API + dashboard summary (pilot Day 3 — main app surface).
# ``pipelines.router`` is workspace-scoped (RBAC); ``pipelines.public_router``
# hosts the ``/pipelines/runs/{id}/result`` callback that dispatched
# GitHub Actions workflows hit with a bearer ``run_token`` (no session,
# no workspace prefix).
api_router.include_router(pipelines.router)
api_router.include_router(pipelines.public_router)
api_router.include_router(dashboard.router)
# Catalog read-only surface (presets / workflows / collections) backed
# by ``artifacts/**/ARTIFACT.md``. Powers the wizard preset picker and
# the dashboard's workflow install buttons.
api_router.include_router(catalog.router)
