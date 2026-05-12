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
    admin_invites,
    agent_roles,
    agent_runs,
    agent_secrets,
    artifact_repos,
    audit,
    auth,
    analytics_dora,
    catalog,
    chat,
    clarifications,
    config,
    dashboard,
    dashboard_live_system,
    dashboard_priorities,
    distiller,
    github_app,
    health,
    improvements,
    inbox,
    integrations,
    invites,
    knowledge,
    knowledge_canon,
    knowledge_import_sources,
    linear_oauth,
    members,
    native_integrations,
    notifications,
    notion_oauth,
    pipelines,
    processes,
    policies,
    repo_home,
    repo_secrets,
    repos,
    telegram,
    tracker_binding,
    waitlist,
    workspaces,
)


api_router = APIRouter(prefix="/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin_invites.router)
# Public waitlist submission from landing site (E08 T05). No auth required.
api_router.include_router(waitlist.router)
api_router.include_router(workspaces.router)
api_router.include_router(config.router)
api_router.include_router(artifact_repos.router)
api_router.include_router(audit.router)
api_router.include_router(integrations.public_router)
api_router.include_router(integrations.router)
api_router.include_router(native_integrations.router)
api_router.include_router(knowledge_import_sources.router)
# Canon endpoints (topic views + claims) ride before legacy knowledge
# router so the more-specific ``/topic-views`` / ``/claims`` paths
# don't get swallowed by ``/{slug}`` in ``knowledge.router``.
api_router.include_router(knowledge_canon.router)
api_router.include_router(knowledge.router)
api_router.include_router(members.router)
# Unified inbox surface (RFC-0010 §5). List/detail/disposition over
# inbox_items + inbox_item_events. Reassignment delegates to the
# routing service (services.inbox.routing). Owner-or-admin RBAC for
# mutations; member RBAC for reads.
api_router.include_router(inbox.router)
# Workspace repo activations (pilot Day 2 — picker UI + Code Map MVP).
# Lives next to artifact_repos but is keyed off GitHub App installations
# instead of paste-URL clones.
api_router.include_router(repos.router)
# E14 — write-side surface that ``shipctl run`` calls during a routine
# run. Workspace-scoped (admin auth), vendor-agnostic ``ticket_ref``.
# Server resolves the right TrackerGateway adapter so the CLI doesn't
# need per-vendor logic.
api_router.include_router(agent_runs.router)
# GitHub App OAuth + webhooks (pilot WOW-onboarding flow). Webhook route
# is public; install start/callback do their own auth.
api_router.include_router(github_app.router)
# Linear OAuth (pilot Day 2 — tracker WOW flow). Callback is public so
# the browser redirect from Linear can hit it without a session.
api_router.include_router(linear_oauth.router)
# Notion OAuth (pilot Day 2 — tracker WOW flow). Same shape as Linear.
api_router.include_router(notion_oauth.router)
# Telegram bot adapter (group ↔ workspace bridge). Console-facing
# endpoints only — bind preview/confirm + linked-groups list/delete.
# The bot worker process talks to ``/v1/workspaces/{ws}/chat/stream``
# directly with the service PAT minted at bind time.
api_router.include_router(telegram.router)
# Pipelines API + dashboard summary (pilot Day 3 — main app surface).
# ``pipelines.router`` is workspace-scoped (RBAC); ``pipelines.public_router``
# hosts the ``/pipelines/runs/{id}/result`` callback that dispatched
# GitHub Actions workflows hit with a bearer ``run_token`` (no session,
# no workspace prefix).
api_router.include_router(pipelines.router)
api_router.include_router(pipelines.public_router)
api_router.include_router(processes.router)
api_router.include_router(dashboard.router)
# Dashboard v2 prioritizer surface (PR-1). GET / POST under
# ``/v1/workspaces/{ws}/priorities`` — list+reorder of tracker
# projects, plus the workspace-level autonomy pause toggle.
api_router.include_router(dashboard_priorities.router)
# Dashboard v2 Live System aggregator (PR-2). One denormalised
# endpoint feeds the middle column of the home dashboard
# (masthead glyphs, knowledge, routines, daily-digest, specialist
# health) so the Console doesn't fan out to half a dozen routes
# on every render.
api_router.include_router(dashboard_live_system.router)
api_router.include_router(analytics_dora.router)
# Per-repo Home rollup (RFC-0008 §F — PR-4) — a single snapshot the
# /r/<slug> page renders as Now + Trends tabs without fanning out to
# the four source endpoints client-side.
api_router.include_router(repo_home.router)
# Catalog read-only surface (presets / collections) backed by
# ``artifacts/**/ARTIFACT.md``. Powers the wizard preset picker and
# pattern/tool pickers.
api_router.include_router(catalog.router)
# Team invites (B7 — WOW onboarding team install). Admin-scoped list/
# create/revoke under ``/workspaces/{ws}/invites``; public peek +
# authenticated accept at ``/invites/{token}``.
api_router.include_router(invites.workspace_router)
api_router.include_router(invites.invite_router)
# Platform invites (E08 — closed-beta gating). Admin-only create/list/revoke
# under ``/admin/invites``; public status check at ``/public/invites/{token}``
# and capacity check at ``/public/beta-capacity``.
api_router.include_router(admin_invites.public_router)
api_router.include_router(admin_invites.beta_capacity_router)
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
# Dashboard notifications / dismissible banners (A4 "PR-merged" +
# A5 "self-heal auto-triggered"). Reader + dismiss surface; writes
# happen from the webhook handlers via `services.notifications`.
api_router.include_router(notifications.router)
# Per-repo Ship-managed Actions secrets (B10). Admin-only CRUD at
# /workspaces/{ws}/repos/{repo}/secrets. Plaintext on POST only; the
# service layer syncs to GitHub so cron/push/dispatch workflows all
# see the values as ``${{ secrets.X }}``.
api_router.include_router(repo_secrets.router)
# (Lanes HTTP route retired — endpoints had zero console / CLI
# consumers. The ``.ship/config.yml`` sync still runs through
# ``services.lanes_sync`` from the GitHub webhook in
# ``routes.github_app``; just the human-facing list / detail /
# admin-sync endpoints went away.)
# Workspace prose-rule policies (Workspace policy injection) —
# free-text standing rules ("Always work via PR", "Never commit
# secrets") that get rendered into the agent system prompt at
# runtime, both in Navigator chat and in `shipctl run` stdout.
# See ``services.policies`` for the shared rendering helper.
api_router.include_router(policies.router)
# Agent role registry (Phase 2.4). Two surfaces:
# * ``/v1/agent-roles*`` — read-only Ship defaults (file-backed).
# * ``/v1/workspaces/{ws}/agent-roles*`` — workspace overrides + clones,
#   plus ``/{slug}/resolve`` used by ``shipctl run`` to fetch the
#   right body (workspace row when present, else Ship default).
api_router.include_router(agent_roles.public_router)
api_router.include_router(agent_roles.workspace_router)
# Per-repo agent API-key wiring (Wizard v2 iter 3). Admin-only check
# + push; plaintext lives only in the HTTP hop to GitHub's secrets
# API and is never persisted by Ship.
api_router.include_router(agent_secrets.router)
# Per-repo tracker binding (Wizard v2 iter 4). GET/PUT/DELETE under
# /workspaces/{ws}/repos/{repo}/tracker. Rides on workspace-level
# OAuth connections (no per-repo tokens); stores the team/project
# selection and falls back to the workspace default on read.
api_router.include_router(tracker_binding.router)
