"""Top-level router for the v1 API.

Mounted by ``backend.app.main`` under ``/v1``. Existing unversioned routes
(``/patterns``, ``/tools``, ``/collections``, ``/search``, ``/fetch``,
``/feedback``, ``/telemetry``) stay where they are for the
already-released ``@elmundi/ship-cli``. RFC-0007 Phase 6 retired
``/workflows`` and ``artifact_kind=workflow``; starter YAMLs now live
inside ``backend.app.resources`` and are consumed by the internal
Pipeline install flow only.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.v1.routes import (
    agent_secrets,
    artifact_repos,
    audit,
    auth,
    buckets_resolver,
    catalog,
    chat,
    clarifications,
    dashboard,
    distiller,
    github_app,
    health,
    improvements,
    integrations,
    invites,
    knowledge,
    lanes,
    linear_oauth,
    members,
    metrics,
    notifications,
    notion_oauth,
    pipelines,
    repo_secrets,
    repos,
    tracker_binding,
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
# Catalog read-only surface (presets / collections) backed by
# ``artifacts/**/ARTIFACT.md``. Powers the wizard preset picker and
# pattern/tool pickers.
api_router.include_router(catalog.router)
# Team invites (B7 — WOW onboarding team install). Admin-scoped list/
# create/revoke under ``/workspaces/{ws}/invites``; public peek +
# authenticated accept at ``/invites/{token}``.
api_router.include_router(invites.workspace_router)
api_router.include_router(invites.invite_router)
# Clarifications inbox (C9 — human-in-the-loop Q&A). Session-auth
# admin routes under /workspaces/{ws}; run-token pipeline ingress
# under /clarifications/pipeline.
api_router.include_router(clarifications.router)
api_router.include_router(clarifications.pipeline_router)
# Improvements (C8 — agent proposals with yes/no/later decisions).
# Same split as clarifications: session admin on /workspaces/{ws};
# run-token ingress on /improvements/pipeline.
api_router.include_router(improvements.router)
api_router.include_router(improvements.pipeline_router)
# Buckets resolver — Phase 3 of the knowledge consolidation. Included
# BEFORE chat.router so ``/buckets/resolved`` matches the literal path
# here instead of being captured by chat's ``/buckets/{slug}`` route.
# See backend/docs/knowledge-consolidation.md.
api_router.include_router(buckets_resolver.router)
# Distiller — Phase 6 ingest surface for bucket articles. Mounted
# BEFORE chat.router so ``/buckets/{slug}/distill`` and
# ``/buckets/{slug}/distill/runs`` take precedence over chat's
# generic ``/buckets/{slug}`` PATCH/DELETE. See
# backend/docs/knowledge-consolidation.md Phase 6.
api_router.include_router(distiller.router)
# Chat (C12 — real agent, single-window SSE). The module also hosts
# the buckets CRUD and artifact-feedback routes because they all
# belong to the same "conversational surface" object and share a
# router prefix under /workspaces/{ws}.
api_router.include_router(chat.router)
# Metrics overview (D11 — SHIP-book dashboard). Single aggregator
# endpoint under /workspaces/{ws}/metrics/overview.
api_router.include_router(metrics.router)
# Dashboard notifications / dismissible banners (A4 "PR-merged" +
# A5 "self-heal auto-triggered"). Reader + dismiss surface; writes
# happen from the webhook handlers via `services.notifications`.
api_router.include_router(notifications.router)
# Per-repo Ship-managed Actions secrets (B10). Admin-only CRUD at
# /workspaces/{ws}/repos/{repo}/secrets. Plaintext on POST only; the
# service layer syncs to GitHub so cron/push/dispatch workflows all
# see the values as ``${{ secrets.X }}``.
api_router.include_router(repo_secrets.router)
# Lanes — projection of customer ``.ship/config.yml`` lanes (RFC-0007
# Phase 7). List + per-repo sync trigger. Webhook-driven re-syncs on
# pushes to ``.ship/config.yml`` live in ``routes.github_app``.
api_router.include_router(lanes.router)
# Per-repo agent API-key wiring (Wizard v2 iter 3). Admin-only check
# + push; plaintext lives only in the HTTP hop to GitHub's secrets
# API and is never persisted by Ship.
api_router.include_router(agent_secrets.router)
# Per-repo tracker binding (Wizard v2 iter 4). GET/PUT/DELETE under
# /workspaces/{ws}/repos/{repo}/tracker. Rides on workspace-level
# OAuth connections (no per-repo tokens); stores the team/project
# selection and falls back to the workspace default on read.
api_router.include_router(tracker_binding.router)
