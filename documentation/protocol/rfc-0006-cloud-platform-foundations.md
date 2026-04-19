---
rfc: 0006
title: "Cloud platform foundations — multi-tenant, git-as-truth, Postgres everywhere"
status: Proposed
created: 2026-04-19
---

# RFC-0006 — Cloud platform foundations

## Summary

Ship grows from a stateless read-only methodology API into a **multi-tenant cloud platform** that:

- runs identically on a developer laptop (`docker compose up`), inside an organisation (Helm chart), and as our SaaS (`ship.elmundi.com.ua`),
- stores **artifacts, documents, and configuration in git** (one or more repositories per workspace), and uses Postgres only as a **derived index, runtime state, and event store**,
- supports **personal / team / enterprise** tenancy through an `Org → Workspace → Project` hierarchy,
- exposes a versioned `/v1` HTTP API alongside the existing unversioned methodology endpoints (kept as backwards-compatible aliases for the already-released CLI).

This RFC fixes the fundamentals — tenancy model, source-of-truth split, deployment topology, and database choice. Follow-up RFCs cover specific surfaces (documents, inbox/merge UI, telemetry sinks, daily/retro workflows).

## Goals

1. **One runtime, three deployments.** Same image, same schema, same migrations for laptop / on-prem / SaaS.
2. **Git as source of truth** for everything a human authors (artifacts, documents, retro reports). Postgres is rebuildable from git + object storage.
3. **Tenancy from day one.** No bolted-on multi-tenant retrofit later.
4. **Backwards compatible.** Already-shipped `@elmundi/ship-cli` keeps working against `/patterns`, `/tools`, `/workflows`, `/collections`, `/search`, `/fetch`, `/feedback`, `/telemetry` without changes.
5. **Self-serve friendly.** Booting locally is `cp .env.example .env && docker compose up`. No SQLite / no fallbacks / no “works on my machine” surprises.

## Non-goals

- Inventing a new artifact format. RFC-0005 stands; this RFC consumes it.
- Replacing CI/agent runners. Ship orchestrates and observes; the user’s scheduler still runs the work.
- Building a billing system in v1. Quota counters land in the data model, charging integration is out of scope.

## Tenancy model

```
Org                              (the billing & SSO boundary)
 ├── Workspace 1..N              (catalog scope; the unit users see)
 │    ├── Member 1..N            (RBAC: owner | admin | maintainer | member | viewer)
 │    ├── Project 0..N           (a (repo, tracker, default workflow) triple)
 │    ├── ArtifactRepo 1..N      (git remotes the workspace pulls/pushes from)
 │    └── Integration 0..N       (Linear, GitHub App, Slack, OTLP, …)
 └── Membership (Org-level)      (org admins, billing contacts)
```

- **Individual** → 1 Org = 1 Workspace = 1 User. The Org layer is hidden in the UI until they upgrade to Team.
- **SMB / startup** → 1 Workspace, N Projects, ~5–50 members.
- **Enterprise** → 1 Org, N Workspaces (per department / business unit), shared SSO and billing, **isolated** catalogs and documents per workspace. Cross-workspace artifact promotion is an explicit PR flow, never an implicit read-through.

### Visibility & catalog resolution

Artifact and document lookups always carry a `workspace_id`. The resolver merges three layers in priority order:

1. **project** overrides (artifact pinned at `projects/<proj>/.ship/`)
2. **workspace** entries (in `workspaces/<ws>/artifacts/...` git repo)
3. **global** entries (this monorepo, mirrored read-only)

Each layer can be toggled per workspace (`catalog_sources: { global: true, workspace: true, project: true }`). Air-gapped enterprises can disable `global` entirely.

### RBAC roles (workspace-scoped)

| Role | Browse catalogs | Submit feedback | Approve PRs | Manage integrations | Manage members |
|---|---|---|---|---|---|
| viewer | yes | yes | no | no | no |
| member | yes | yes | no | no | no |
| maintainer | yes | yes | yes | no | no |
| admin | yes | yes | yes | yes | yes |
| owner | yes | yes | yes | yes | yes (+ delete workspace) |

Org-level roles (`org_owner`, `org_admin`, `billing`) cross-cut workspaces.

## Source of truth

| Thing | Source of truth | Postgres role |
|---|---|---|
| Artifact (`ARTIFACT.md`, examples, scripts) | git repo (workspace or global monorepo) | indexed copy in `artifacts` + `artifact_versions` for fast list/search |
| Document bucket source files (PDF, DOCX, …) | object storage (S3 / MinIO) | metadata + parsed MD + chunks/embeddings |
| Document parsed MD | git repo of the workspace (`documents/<bucket>/<id>/ARTIFACT.md`) | indexed copy + chunks |
| Daily / retro reports | git repo (`reports/daily/YYYY-MM-DD.md`, `reports/retro/YYYY-Wxx.md`) | indexed copy + action item state |
| Workspace settings, integrations | git (`workspaces/<ws>/config.yml`) **and** Postgres (live editable mirror; DB wins on conflict, periodic write-back to git as PR) | live |
| Users, sessions, tokens, audit log, telemetry events, quotas | **Postgres only** | source of truth |
| Secrets (integration tokens) | **Postgres only**, encrypted at rest with KMS / Fernet | source of truth |

Rule of thumb: **anything a human authors lives in git; anything a system observes lives in Postgres.** A workspace can be fully reconstructed from `(git repos + S3 bucket)`; Postgres is rebuildable.

## Deployment topology

A single repo, a single image set, three target environments:

```
ship-server   FastAPI app (HTTP API + UI server)
ship-worker   Background jobs (ingestion, indexing, cron, webhook fan-out)
postgres      Postgres 16 + pgvector
redis         Broker for the worker, pub/sub, cache
minio (S3)    Blob storage for documents
```

| Environment | Postgres | Redis | S3 | Auth | Notes |
|---|---|---|---|---|---|
| Laptop / `docker compose up` | container `postgres:16` + pgvector image | container | MinIO container | local stub (single-user, no login) | one-command boot, no external accounts |
| Self-hosted in org (Helm) | managed Postgres or in-cluster | in-cluster Redis | bucket of choice | OIDC / SAML / GitHub Enterprise | one chart, opinionated values.yaml |
| SaaS | **Neon** (control-plane DB + per-workspace branches as enterprise tier) | managed Redis | S3 | GitHub OAuth, Google, SAML for enterprise | scale-to-zero on idle workspaces |

The application code targets **vanilla Postgres + pgvector** only. Neon-specific features (branching, scale-to-zero) are used by the SaaS control plane (one CLI command per workspace), never inside hot request paths. This guarantees self-hosted parity.

### Why Neon for SaaS

- `pgvector` is a first-class extension (no ticket, no plan limit).
- **Branching** maps perfectly onto our preview-environment story for catalog PRs (`shipctl --catalog=preview/<pr>` becomes `neon branches create --parent main`).
- **Scale-to-zero** keeps free-tier workspaces nearly free.
- Pooled connection endpoint avoids the FastAPI ↔ serverless burst problem when used with the `-pooler` URL.

Trade-offs to mitigate in code:

- Cold start (~300–500 ms): warm-up ping every N minutes from `ship-worker`, optional `min_cu > 0` per paid workspace.
- Connection limits: connect through PgBouncer (Neon pooler) for app and worker; use direct endpoint only for migrations.
- HNSW index builds are memory-bound: temporarily scale up compute on first ingestion of a large bucket.

## Database

- **Engine:** Postgres 16, extensions `uuid-ossp`, `pgcrypto`, `vector`.
- **Driver:** `asyncpg` via SQLAlchemy 2.x async; Alembic for migrations.
- **Multi-tenant isolation:** every tenant-scoped table carries `workspace_id uuid not null`. Default isolation strategy: **shared schema + Postgres Row-Level Security**, enabled per session via `SET LOCAL app.workspace_id = '<uuid>'`. Enterprise tier opts into **dedicated Neon branch per workspace** for hard isolation.
- **Vector search:** `pgvector` (HNSW), one chunks table per kind family (`artifact_chunks`, `document_chunks`); replaces the existing Chroma store.
- **Telemetry:** partitioned `telemetry_events` table by month.

### v1 schema (initial migration)

```
orgs(id, name, slug, plan, created_at)
users(id, email, display_name, avatar_url, created_at)
org_members(org_id, user_id, role)
workspaces(id, org_id, name, slug, settings jsonb, catalog_sources jsonb, created_at)
workspace_members(workspace_id, user_id, role)
projects(id, workspace_id, name, slug, repo_url, tracker_kind, settings jsonb)
artifact_repos(id, workspace_id, kind, url, default_branch, last_sync_at)
api_tokens(id, user_id, workspace_id nullable, name, hashed_secret, scopes jsonb, expires_at, last_used_at)
integrations(id, workspace_id, kind, config jsonb, secret_ciphertext bytea, status, last_health_at)
audit_log(id, workspace_id nullable, actor_user_id, actor_token_id, action, target_kind, target_id, payload jsonb, created_at)
```

Subsequent RFCs add `artifacts`, `artifact_versions`, `artifact_chunks`, `documents`, `document_buckets`, `document_chunks`, `telemetry_events`, `pull_requests`, `daily_reports`, `retro_reports`, `action_items`.

## API surface

- Existing unversioned routes remain for the released CLI:
  - `GET /patterns`, `/patterns/{id}`, `/tools`, `/workflows`, `/collections`, `POST /search`, `POST /fetch`, `POST /feedback`, `POST /telemetry` (read against the global monorepo, no auth, unchanged behaviour).
- New versioned, tenant-scoped routes:
  - `POST /v1/auth/exchange` (OAuth code → session) and `POST /v1/auth/tokens` (PAT mint).
  - `GET /v1/workspaces`, `POST /v1/workspaces`, `GET /v1/workspaces/{ws}`.
  - `GET /v1/workspaces/{ws}/artifacts/{kind}` and `/v1/workspaces/{ws}/artifacts/{kind}/{id}` — resolve through the project / workspace / global stack.
  - `POST /v1/workspaces/{ws}/feedback`, `POST /v1/workspaces/{ws}/telemetry` — same payloads as unversioned, but scoped, authenticated, and stored in Postgres.
- Auth methods: short-lived **session JWT** for the web UI, long-lived **API tokens** (scoped) for the CLI / CI.

## Backwards compatibility

- Released CLI versions continue to talk to the public methodology API exactly as today. Their behaviour does not change until users adopt a workspace-aware CLI version.
- The new CLI minor version reads `workspace_id` and `api_token` from `.ship/config.yml`; if absent, falls back to the public unauthenticated path.

## Open questions

1. Workspace git layout: one repo per workspace, or one monorepo with `workspaces/<ws>/` folders? Default proposal: **one repo per workspace** (clean ACLs); enterprise can opt into a monorepo.
2. Do we mirror the global Ship monorepo into every self-hosted deploy, or fetch live from `github.com/ElMundiUA/ship` at request time? Proposal: **scheduled mirror via `ship-worker`** with overridable upstream URL.
3. Document parsing engine choice (`unstructured` vs `marker` vs `docling`) — separate RFC, the schema is engine-agnostic.

## Acceptance

This RFC is accepted once the first migration ships, `docker compose up` boots the full stack on a clean machine, the existing test suite still passes, and one new end-to-end test creates an Org → Workspace → User and lists global artifacts via `/v1/workspaces/{ws}/artifacts/patterns`.
