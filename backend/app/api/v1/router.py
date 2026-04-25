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
    adoption,
    agent_secrets,
    artifact_repos,
    audit,
    auth,
    buckets_resolver,
    catalog,
    chat,
    clarifications,
    custom_patterns,
    dashboard,
    distiller,
    feature_flags,
    fleet_lanes,
    fleet_requests,
    github_app,
    health,
    improvements,
    inbox,
    inbox_groups,
    inbox_routing,
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
    processes,
    plays,
    policies,
    repo_home,
    repo_secrets,
    repos,
    requests_api,
    tracker_binding,
    tracker_fsm,
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
# Operational groups for inbox routing (RFC-0010 §5). Distinct from
# WorkspaceMember roles — these are "who handles X" buckets (secops,
# eng_managers, on_call_eng) used by inbox_routing_rules to resolve
# symbolic handles into concrete owners.
api_router.include_router(inbox_groups.router)
# Inbox routing rules (RFC-0010 §6). Maps symbolic handles
# (`secops`, `repo_maintainer`, …) to concrete users/groups/
# strategies; consulted by services.inbox.routing.resolve_handle
# at intake time. Admin-only mutations; preview endpoint is
# explicitly side-effect free for "what would this do?" UX.
# Mounted BEFORE `inbox.router` so the literal `/inbox/routing*`
# paths win over `inbox`'s `/inbox/{item_id}` parameter capture.
api_router.include_router(inbox_routing.router)
# Unified inbox surface (RFC-0010 §5). List/detail/disposition over
# inbox_items + inbox_item_events. Reassignment delegates to the
# routing service (services.inbox.routing). Owner-or-admin RBAC for
# mutations; member RBAC for reads.
api_router.include_router(inbox.router)
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
api_router.include_router(processes.router)
api_router.include_router(dashboard.router)
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
# Plays coverage (RFC-0010 P4-00) — workspace aggregation surface
# powering the Console's Coverage tab. One row per Play (= pattern
# id) with activated_repos_total / assignments_count / coverage_pct
# and a covered/uncovered repo split. Read-only, member RBAC.
# Mounted next to lanes because both surfaces project Lane rows; this
# one collapses by pattern, lanes.router lists them verbatim.
api_router.include_router(plays.router)
# Ad-hoc agent runs ("Requests", RFC-0007 Phase 3). Workspace-scoped
# list + per-repo dispatch endpoint. Dispatches ``adhoc-agent-run.yml``
# that's seeded into every activated repo by the wizard bundle.
api_router.include_router(requests_api.router)
# Fleet Requests (RFC-0008 §D) — workspace-level fan-out of a single
# catalog pattern across many repos. Best-effort: pre-flight
# rejections (repo not found, GitHub App missing) land on the
# parent's ``rejections`` JSONB without blocking the rest; dispatch-
# time failures persist on the child :class:`AgentRequest` row with
# ``status=dispatch_failed``.
api_router.include_router(fleet_requests.router)
# Adoption funnel (RFC-0008 §E) — workspace-level rollup of "how far
# has Ship landed across these repos" (installed → activated →
# seeded → first_run → steady). Read-only; the rollup is computed
# live from WorkspaceRepo + PipelineRun + WorkflowRun + AgentRequest
# and returned in one shot.
api_router.include_router(adoption.router)
# Workspace-level Fleet lanes primitive (RFC-0008 §G — PR-5,
# previously called "policies") — mirror-lane rules + per-repo
# opt-outs with live compliance rollup. Read is member;
# create/delete/exception are admin. Renamed from "policies" to
# free up that name for free-text standing rules injected into
# agent instructions; see ``routes.fleet_lanes`` docstring.
api_router.include_router(fleet_lanes.router)
# Workspace prose-rule policies (Workspace policy injection) —
# free-text standing rules ("Always work via PR", "Never commit
# secrets") that get rendered into the agent system prompt at
# runtime, both in Navigator chat and in `shipctl run` stdout.
# See ``services.policies`` for the shared rendering helper.
api_router.include_router(policies.router)
# Workspace-private catalog layer (RFC-0008 §H — PR-6). Authoring
# (``POST /patterns/draft`` via LLM + ``POST /patterns`` to persist)
# lives here; reads are served by ``/v1/catalog/patterns`` with the
# ``workspace_id`` query parameter so every baked-in caller gets
# merged results "for free".
api_router.include_router(custom_patterns.router)
# Per-repo agent API-key wiring (Wizard v2 iter 3). Admin-only check
# + push; plaintext lives only in the HTTP hop to GitHub's secrets
# API and is never persisted by Ship.
api_router.include_router(agent_secrets.router)
# Per-repo tracker binding (Wizard v2 iter 4). GET/PUT/DELETE under
# /workspaces/{ws}/repos/{repo}/tracker. Rides on workspace-level
# OAuth connections (no per-repo tokens); stores the team/project
# selection and falls back to the workspace default on read.
api_router.include_router(tracker_binding.router)
# Tracker FSM catalog (Wizard v2 iter 7). Read-only surface: the
# canonical Ship states, per-tracker mapping hints, and (optional)
# rendered markdown previews per activated repo — exactly what the
# seed PR writes into ``.ship/tracker-fsm.md``. Source of truth is
# the committed file; this endpoint just mirrors it for the console.
api_router.include_router(tracker_fsm.router)
# Workspace feature flags (P2-19) — single JSONB-backed dict on the
# workspace row, gated read=member / write=owner. Adds the lever
# the inbox redesign rollout needs without coupling the inbox
# routes to the helper yet (that's a follow-up ticket).
api_router.include_router(feature_flags.router)
