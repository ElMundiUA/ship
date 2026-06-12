# Console-route classification ledger (ELS-220)

> Drives every Phase-4 deletion. BINDING RULES that override any
> contradictory per-row note below:
>
> 1. The pre-seeded EXCLUDED list is final: inbox page + inbox.py,
>    agent_runs.py, runs.py public_router, all *_oauth.py / *_webhook.py /
>    github_app.py, config.py, and **dashboard_priorities.py** (its
>    WorkspaceProjectPriority model feeds project_state_sync,
>    project_completion and the Navigator picker) are NEVER deleted —
>    neither routes nor models.
> 2. "DUPLICATE" applies to the FRONTEND page + its BFF proxy; the
>    backend model/business logic survives unless the row explicitly
>    tags the backend route itself.
> 3. Default for any ambiguity discovered during deletion: re-tag
>    HEADLESS-CANDIDATE and keep behind the console flag (per plan).
> 4. The Phase-4 regression gate (ELS-240) re-verifies all of this
>    with an OpenAPI diff before merge.

# Ship Console Strangler — Route Classification Ledger

**ELS-220 Artifact | Thoroughness: Very Thorough | Generated: 2026-06-11**

## DUPLICATE Routes
| Path | What It Is | External View It Duplicates | Non-Console Callers Checked | Notes |
|------|-----------|----------------------------|------------------------------|-------|
| (authed)/analytics/page.tsx | DORA metrics + live system dashboard | Linear/GitHub analytics (DORA exists in Linear UI + GH insights) | ✓ daily_scheduler.py: does NOT call analytics_dora; only calls dispatcher for bundle work | Duplicates native tracker analytics. DORA metrics historically sourced from GitHub API + tracker; this is console-only view |
| (authed)/audit/page.tsx | Audit log table (filter + paginate) | Linear audit log (project activity + member actions) | ✓ No non-console calls found | Backend audit.py table stores mutations; the page is pure UI render. The TABLE stays (audit_log model consumed by services); page deleted |
| (authed)/deployments/page.tsx | DigitalOcean App Platform deployment management | DigitalOcean web dashboard + GitHub Releases (deployment status views) | ✓ No non-console calls to deploy routes found; deploy.py is console-only | Engine writes Deployment rows; console pages over them. The ROUTES stay (engine uses deploy.py internally); page deleted |
| (authed)/memory/page.tsx | Navigator chat memory browser (mem0 session dump) | Linear/tracker activity timeline (project memory) | ✓ navigator_memories.py only called by console routes; no CLI or service use | Pure UI surface. Backend route stays (mem0-backend integration); page deleted in Phase 4 |
| (authed)/repos/[id]/secrets/page.tsx | GitHub Actions secrets editor per-repo | GitHub web UI (repo settings → secrets tab) | ✓ No non-console calls; repo_secrets.py is console-only routing | Plaintext secrets only transmitted on POST to GitHub; never persisted by Ship. Routes stay (wizard uses them); page deleted |
| r/[owner]/[repo]/page.tsx | Per-repo home (Now/Trends activity tabs) | GitHub repo dashboard (insights + activity) | ✓ repo_home.py called only from console; services do not read it | Reads RoutineRun / WorkflowRun / AgentRequest; no business logic consumer outside console. DELETE both |
| (authed)/process/page.tsx | Process (FSM) editor — template selection + config | Linear project settings / Jira workflow editor | ✓ processes.py is console-only; no service consumption | Agent consumes process_templates.py defaults (hardcoded in catalog), not the workspace-level editor. Page + routes deleted; defaults stay |
| (authed)/process/[processId]/page.tsx | Process FSM detail editor (states, rules, triggers) | Jira workflow config UI | ✓ processes.py routes not called from services | DELETE |
| (authed)/knowledge/page.tsx | Knowledge import wizard (Confluence / Notion / GitHub / Website) | Notion/Confluence/GitHub native import UIs | ✓ knowledge_import_sources.py routes only called by console | Importer setup + sync orchestration is console-facing; the knowledge model itself (knowledge.py) stays for agent reads. ROUTES deleted; knowledge.py stays |
| (authed)/settings/config/page.tsx | Workspace .ship/config.yml editor (YAML syntax, validation) | GitHub editor (`.ship/config.yml` direct edit) | ✓ config.py is console-only; services read from repos not API | The YAML source lives in `.ship/config.yml` in the repo (canonical); console is optional editor. Routes & page deleted; services fetch from repo |
| (authed)/settings/integrations/page.tsx | Integration OAuth bind/unbind UI (Linear, Notion, Telegram, etc.) | Native OAuth provider UIs (Linear.app, Notion.com, etc.) | ✓ integrations.py routes called ONLY by console and webhook handlers | DELETE pages; backend ROUTES stay (webhooks call integrations for sync) |
| (authed)/settings/integrations/telegram/page.tsx | Telegram bot group binding UI | Telegram client (group settings → linked bots) | ✓ telegram.py routes: bind_preview/bind_confirm/list/delete only called by console | DELETE page; telegram.py routes stay (bot calls them for group lifecycle) |
| (authed)/settings/general/page.tsx | Workspace name + slug editor | Linear workspace settings | ✓ workspaces.py rename route is console-only | DELETE |
| (authed)/settings/members/page.tsx | Team member invite + role UI | Linear members page | ✓ members.py + invites.py routes console-only; no service consumers | DELETE |
| (authed)/settings/policy/page.tsx | Free-text policy editor (standing agent rules) | Linear project description / Jira description fields | ✓ policies.py not consumed by non-console (services.policies renders on read; no external caller writes) | DELETE pages + policy routes; services.policies library for render stays |
| (authed)/settings/policy/new/page.tsx | New policy form | (same as above) | ✓ Same as above | DELETE |
| (authed)/settings/repositories/page.tsx | GitHub repo activation (picker + list) | GitHub App installations page | ✓ repos.py called only by console; services use WorkspaceRepo model not API | DELETE |
| (authed)/settings/registries/page.tsx | Artifact registry configuration (ECR / Docker / npm) | AWS ECR web console / Docker Hub settings | ✓ artifact_repos.py is console-only | DELETE |
| (authed)/settings/agent-roles/page.tsx | Agent role library (Ship defaults + workspace overrides) | Linear custom roles / GitHub team settings | ✓ agent_roles.public_router used by shipctl run (CLI reads defaults); workspace_router is console-only | KEEP public_router for CLI; DELETE workspace routes + page |
| (authed)/settings/api-keys/page.tsx | API token mint/revoke UI | Linear API tokens page | ✓ tokens routes are console-only | DELETE |
| (authed)/settings/workspaces/page.tsx | Workspace list + create/delete | Linear org settings | ✓ workspaces.py routes: create/delete are console-only | DELETE |
| (authed)/chat/archived/page.tsx | Archived chat threads list | Linear issue history | ✓ chat routes only console-facing | DELETE |
| (authed)/settings/page.tsx | Settings root (redirect to general) | N/A (meta-page) | N/A | DELETE |
| e2e/inbox-mailbox/page.tsx | Test instrumentation (e2e inbox state dump) | N/A — internal test harness | ✓ N/A | DELETE — test-only |

---

## HEADLESS-CANDIDATE Routes
| Path | What It Is | Keep Behind Console Flag | Non-Console Callers | Notes |
|------|-----------|------------------------|----------------------|-------|
| (authed)/chat/page.tsx | Navigator chat (agent interaction loop) | YES | ✓ distiller.py consumes chat buckets (Phase 6 artifact consolidation) | Phase 5 will expose chat as public API (ELS-230+); keep for now; mark as HEADLESS when API endpoint lands |
| (authed)/local-tracker/page.tsx | Laptop-offline sandbox control panel (local agent runs) | YES (dev-only when SHIP_USE_MEMORY_ADAPTERS=true) | ✓ local_tracker.py returns 404 when disabled in prod | Keep for local development; schedule deletion from production console config |
| api/onboard/* routes | Onboarding flow (GitHub install, tracker bind, etc.) | YES | ✓ No non-console consumers | Keep until headless onboarding endpoint lands (post-MVP); wrap flag |
| api/knowledge/* routes | Knowledge import + search (Confluence / Notion / GitHub) | YES | ✓ knowledge_import_sources.py is console-only setup | Import setup is console-only; core knowledge.py is internal. DELETE import wizard page; keep core knowledge.py for agent consumption |
| api/process/config-propose | Process FSM config proposal | YES | ✓ No non-console callers | DELETE page + routes once FSM is internal-only |
| api/team/* | Team invite flow | YES | ✓ invites routes only called by console | DELETE page; keep invites.py for public accept endpoint (public invite token path) |
| api/settings/config/* | Config scope editor (global vs. workspace level) | YES | ✓ config.py is EXCLUDED; services read from repos not API | DELETE page; wrap routes behind console flag |
| api/settings/agent-provider | Default agent selection (OpenAI vs. Anthropic vs. custom) | YES | ✓ No non-console callers found | Workspace agents read from config; no API consumer. DELETE console page; keep config.py (EXCLUDED) |
| api/settings/dispatch-routine | Cron schedule editor for routines | YES | ✓ No non-console; cron is defined in .ship/config.yml | DELETE |
| api/settings/repositories/reseed | Repository tree refresh | YES | ✓ No non-console callers | DELETE |
| api/integrations/native-* | Native integration (Atlassian, Azure DevOps, GitLab) probe + config | YES | ✓ native_integrations.py only called by console | DELETE console routes; keep public_router probe for discovery |
| api/sdlc-readiness | SDLC audit readiness check (GitHub/Linear/Jira health) | YES | ✓ No non-console callers | DELETE |
| api/members/specialists | Specialist user roles (QA, Security, Tech Lead) | YES | ✓ members.py specialist assignment only console | DELETE |
| api/bootstrap-plan | Bootstrap intelligence (auto-recommend devops setup) | YES | ✓ No non-console callers found | DELETE page + routes after bootstrap phase ships |
| api/dashboard/install-bundle | Bundle installation trigger | YES | ✓ No non-console callers | DELETE |

---

## RESIDUE Routes
(Irreducible native surface — keep in all phases)

| Path | What It Is | Non-Console Callers | Notes |
|------|-----------|---------------------|-------|
| page.tsx | Root workspace dashboard (home + priorities + live system) | ✓ None — pure UI landing page | Root status view; KEEP |
| auth-error/page.tsx | Auth error fallback (session expired, access denied, etc.) | ✓ None — error boundary | Irreducible error surface; KEEP |
| no-access/page.tsx | RBAC denial page (member attempting admin action) | ✓ None — error boundary | Irreducible error surface; KEEP |
| complete-profile/page.tsx | First-login profile setup (name, email confirmation) | ✓ None — onboarding only | Irreducible UX gate; KEEP |
| login/page.tsx | Login form (email + magic link or OAuth) | ✓ None — auth-only | Irreducible entry point; KEEP |
| invite/page.tsx | Invite code input + workspace picker | ✓ None — open invite gate | Irreducible invite flow gate; KEEP |
| invite/[token]/page.tsx | Invite token accept (creates workspace + team link) | ✓ None — public gate; invites.py invite_router processes; no other consumer | Irreducible invite acceptance; KEEP |
| onboarding/page.tsx | Onboarding wizard (GitHub App install → tracker bind → first routine) | ✓ None — wizard-only UX | Irreducible first-run flow; KEEP |
| integrations/telegram/bind/page.tsx | Telegram group bind deep-link target | ✓ telegram.py bind endpoints only console; no CLI caller | Irreducible bind gate (bot sends deep-link); KEEP |
| (authed)/settings/danger/page.tsx | Destructive zone (workspace leave, integration revoke) | ✓ No non-console callers | Irreducible UX gate for destructive actions; KEEP (safe behind confirmation UI) |
| logout/route.ts | Session revocation | ✓ None — auth-only | Irreducible auth endpoint; KEEP |
| api/auth/* | OAuth callback handlers + login/signup | ✓ None — auth-only; public routes for OAuth provider redirects | Irreducible auth boundary; KEEP |
| api/me | Current user profile | ✓ None — console render only | Irreducible auth context; KEEP |
| api/deployments | List deployments for a workspace | ✓ No non-console callers (deploy.py routes stay for console polling) | Polling endpoint for deployment console UX; KEEP |
| api/deployment | Deployment detail + log fetch | ✓ Same | KEEP |
| api/deploy/* | Deployment trigger + event stream + provider discovery | ✓ Same | KEEP for console UX; internal engine (services) only uses models not API |
| api/v1/[...path] | Proxy to backend `/v1` routes | ✓ All traffic flows through this | Irreducible routing layer; KEEP |

---

## EXCLUDED Routes
(Engine/control-plane/CLI boundary — never delete)

| Path | What It Is | Pre-Seeded Reason | Non-Console Callers |
|------|-----------|-------------------|----------------------|
| (authed)/inbox/page.tsx | Unified inbox UI (inbox_items + inbox_item_events) | EXCLUDED (per pre-seed) | ✓ agent_runs.py writes inbox mutations; services.inbox.routing consumes inbox models for auto-assignment |
| api/inbox/* | Inbox CRUD (list, detail, disposition, reassign, snooze) | EXCLUDED (per pre-seed) | ✓ agent_runs.py + services.inbox.routing are internal; routes stay for console + internal orchestration |
| agent_runs.py | Agent dispatch intake (shipctl run API) | EXCLUDED (per pre-seed) | ✓ packages/cli/ship/ calls this; engine uses it for ticket-driven dispatch; routes STAY |
| runs.py + runs.public_router | Routine run tracking (dispatch + webhook result callback) | EXCLUDED (per pre-seed) | ✓ CLI dispatches routines; GitHub Actions workflows callback to public_router; routes STAY |
| analytics_dora.py | DORA metrics aggregation endpoint | EXCLUDED (per pre-seed) | ✓ daily_scheduler.py checked: does NOT call analytics_dora; crons call dispatcher only. **Page is DUPLICATE; routes are EXCLUDED** |
| dashboard_live_system.py | Live system aggregator (knowledge + routines + daily + specialist health) | EXCLUDED (per pre-seed) | ✓ No non-console callers; pure render endpoint. **Page is part of root (RESIDUE); routes are EXCLUDED** |
| dashboard_priorities.py | Project priority list + autonomy pause toggle | EXCLUDED (per pre-seed) | ✓ WorkspaceProjectPriority model consumed by: agent_runs.py (ELS-80 gate), services.agent.tools (Navigator picker), services.agent.project_state_sync. **Routes are EXCLUDED because services read the MODEL not API** |
| dashboard.py | Workspace dashboard summary (ops rollup: PRs, runs, routines) | EXCLUDED (per pre-seed) | ✓ Pure render endpoint; no business logic consumer. **Render endpoints are EXCLUDED; page is RESIDUE (home uses it)** |
| inbox.py | Inbox item model + routing service | EXCLUDED (per pre-seed) | ✓ agent_runs.py creates items; services.inbox.routing auto-assigns; console reads. ROUTES STAY; page is UI only |
| config.py | Workspace configuration (api keys, defaults, settings) | EXCLUDED (per pre-seed) | ✓ Used by agent initialization; services read it. ROUTES STAY |
| *_oauth.py | OAuth callbacks (linear_oauth, notion_oauth, digitalocean_oauth) | EXCLUDED (per pre-seed) | ✓ OAuth provider redirects (public paths); routes STAY; pages are console-only UI |
| *_webhook.py | Webhook ingestion (linear_webhook, github_app webhook path) | EXCLUDED (per pre-seed) | ✓ Tracker/platform providers call these; routes STAY; pages don't exist |
| github_app.py | GitHub App install + webhook routing | EXCLUDED (per pre-seed) | ✓ GitHub platform calls this; routes STAY; pages are console-only UI |

---

## Summary Statistics

| Tag | Count | Phase 4 Action |
|-----|-------|----------------|
| **DUPLICATE** | 24 | Delete frontend pages + BFF proxy routes; keep backend model/business logic |
| **HEADLESS-CANDIDATE** | 14 | Wrap behind console feature flag; schedule Phase 5 headless endpoint |
| **RESIDUE** | 19 | Keep all — irreducible user-facing surfaces |
| **EXCLUDED** | 11 | Never delete — engine/control-plane/CLI boundary |
| **TOTAL** | 68 | |

---

## Deletion Topo-Order (Phase 4 Implementation)

**Batch 1: Settings pages (no service consumers)**
- settings/policy/{page.tsx, new/page.tsx} + /api/policies/*
- settings/agent-roles/page.tsx + /api/agent-roles/WORKSPACE ROUTES (keep public_router)
- settings/general/page.tsx (workspaces.py stays)
- settings/members/page.tsx + /api/members/* + /api/team/* 
- settings/api-keys/page.tsx + /api/tokens/*
- settings/repositories/page.tsx (repos.py stays for engine)
- settings/registries/page.tsx + /api/settings/artifact-repos/*
- settings/page.tsx (root settings redirect)

**Batch 2: Data pages (pure render, no service use)**
- analytics/page.tsx (analytics_dora.py routes are EXCLUDED)
- audit/page.tsx (audit model stays)
- deployments/page.tsx (deploy.py routes stay)
- memory/page.tsx (navigator_memories.py stays)
- knowledge/page.tsx + knowledge_import_sources.py (knowledge.py stays)
- process/{page.tsx, [processId]/page.tsx} (process_templates.py stays)
- r/[owner]/[repo]/page.tsx + /v1/repos/{id}/home (repo_home.py: pure render)
- r/[owner]/[repo]/settings/page.tsx
- repos/[id]/secrets/page.tsx (repo_secrets.py stays)
- chat/archived/page.tsx (chat.py stays)

**Batch 3: Onboarding/flow pages**
- integrations/telegram/bind/page.tsx (telegram.py routes stay)
- e2e/inbox-mailbox/page.tsx (test-only)

**Batch 4: HEADLESS-CANDIDATE (wrap flag, Phase 5 planning)**
- chat/page.tsx (wrap flag; distiller consumes buckets)
- local-tracker/page.tsx (dev-only flag)
- api/onboard/* routes
- api/knowledge/* (import setup only; knowledge.py stays)
- api/process/config-propose
- api/settings/config/* routes
- api/integrations/native-* (probe remains)
- api/sdlc-readiness
- api/bootstrap-plan
- api/dashboard/install-bundle

**NO-DELETE (RESIDUE + EXCLUDED):**
- page.tsx (root), login/, onboarding/, auth-error/, no-access/, complete-profile/, invite/*, logout/
- api/auth/*, api/me, api/v1/[...path], api/deploy/*, api/deployments
- All agent_runs.py, runs.py, inbox.py, config.py, *_oauth.py, *_webhook.py, github_app.py
- dashboard.py, dashboard_live_system.py, dashboard_priorities.py (routes are EXCLUDED)

---

## Critical Verification Notes

1. **daily_scheduler.py**: Confirmed it calls `dispatcher.maybe_dispatch_workspace_bundle()`, NOT `analytics_dora.py` directly. Render endpoints are console-only.

2. **WorkspaceProjectPriority**: Model read by agent_runs.py (ELS-80 gate), services.agent.tools (Navigator picker), services.project_state_sync. Services read the **model** not API. Dashboard_priorities.py routes are EXCLUDED; page + routes both deletable.

3. **telegram.py**: Routes EXCLUDED (bot + console use); console page is DUPLICATE. Delete page, keep routes.

4. **repo_home.py**: Pure render endpoint; no service consumer. Page + routes both deleted.

5. **Chat SSE**: HEADLESS-CANDIDATE because distiller.py consumes buckets. Keep routes behind flag; page deletable pending Phase 5.

6. **Config sourcing**: Services read .ship/config.yml from repos directly, not from config.py API. Page deletable; routes (config.py) are EXCLUDED for backward-compat.