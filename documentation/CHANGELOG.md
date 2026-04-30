# Manual changelog

A short log of structural changes to the Manual itself — not to the
product. For product changes, see the [/blog](/blog).

## Phase 10 — Methodology index moves to pgvector (E13)

* **ChromaDB removed** — the persistent vector index at `backend/.chroma/` has been removed entirely. All vector search now runs on pgvector (Postgres), which the cloud platform already uses for buckets, articles, and agent memory.
* **`methodology_chunks` table** — replaces Chroma with a dedicated pgvector-backed table, indexing all chunks from `documentation/`, `artifacts/**/ARTIFACT.md`, and `README.md`. Same `/search` and `/fetch` JSON contract — the released CLI sees no behavioural change.
* **Migration path for self-hosted users** — no manual data migration required. The index is a *cache* of static repo files. On first start with the new image, the backend auto-rebuilds the index in pgvector during startup (one-time embedding cost; idempotent on subsequent starts via `content_sha` tracking). Required: `OPENAI_API_KEY` set (same as before).
* **Migration path for Bunny prod** — same as self-hosted. The new image auto-rebuilds the index on deploy. Note the one-time embedding cost (~tokens per chunk).
* **What to delete** — `backend/.chroma/` directory on disk if it exists (already gitignored; manual cleanup optional but recommended).
* **Verification** — `curl -X POST https://ship.elmundi.com/search -H 'Content-Type: application/json' -d '{"query": "policies"}'` should return non-empty `results`.

### Required env vars

* `OPENAI_API_KEY` — OpenAI API key (unchanged from before; required for embedding).
* `OPENAI_EMBED_MODEL` — embedding model (default: `text-embedding-3-small`).

**See:** [E13 — Rip out Chroma, unify on pgvector](./internal/closed-beta/E13-rip-chroma.md)

## Phase 9 — Landing page (April 2026)

* **Home page** rewritten around the operator loop: hero names Plays /
  Automations / Runs / Inbox; how-it-works extended from 3 (init / sync
  / verify) to 5 steps (Bootstrap → Pick Plays → Assign as Automations →
  Watch Runs → Triage Inbox); new flagship **Operator loop** section
  with a Vocabulary card mapping protocol-stable terms (`lanes:`,
  `pattern:`, `pipeline_runs`) to operator-console nouns.
* **Catalog pages** — patterns / kit / collections / use-cases prose
  aligned with the new vocabulary; "Lane prompts" tab in the patterns
  catalog renamed to "Automation patterns" (display label only — the
  underlying group key stays `lanes` for protocol compat).
* **Footer + backend strip** — removed every "tools, workflows, and
  collections" parallel listing (workflow artifact kind retired in
  Phase 6).
* **Hero version badge** — now sourced from `landing/package.json`
  instead of the literal `v0.7.0` it had been carrying.
* **SEO hygiene** — added `app/sitemap.ts` (enumerates static routes +
  blog + docs + patterns + tools + collections) and `app/robots.ts`
  (allow-all, points at the sitemap). Global `metadata.description`
  refreshed to mention the operator loop.
* **Asset cleanup** — removed orphaned `public/landing/hero-methodology-kit.png`
  (no remaining references in `landing/src/`).

## Phase 8 — CLI audit & docs (April 2026)

* **`shipctl help`** rewritten operator-first: vocabulary callout up front,
  commands grouped under Setup / Catalog / Run / Knowledge / Telemetry & feedback / Misc.
* **`cli/README.md`** rewritten to document every subcommand including the
  Phase-3 `shipctl callback` outcome flags (`--outcome-text`, `--findings-count`,
  `--severity`, `--artifact`, `--escalation`, `--requires-approval`) and the
  full Run lifecycle.
* **`shipctl sync --help`** added (was previously undocumented).
* **`shipctl run`** error message clarified: now names `run-agent.yml` and the
  lane kind instead of "(Phase 3)".
* **`shipctl lanes`** gets an operator-friendly alias: **`shipctl automations`**
  (both work indefinitely; YAML / `--lane` stay protocol-stable).
* Landing **`/cli`** page extended with `run` / `lanes` / `callback` /
  `knowledge init`. Setup wizard prose aligned with the new IA vocabulary.
* Removed all references to the obsolete `shipctl workflow` subcommand
  (kind was retired in Phase 6).
* Test surface: snapshot tests for the top 8 commands' `--help` guard
  against future regressions in help copy.

## 2026-04 — IA refresh: Plays / Automations / Runs / Inbox

The operator-facing Manual moved to the new information architecture
defined in [RFC-0010](./protocol/rfc-0010-plays-and-inbox.md). This
entry is historical: current seed bundles now emit `process.routines`
and `shipctl run --routine`; `lanes:` / `--lane` remain compatibility
aliases for already-seeded repositories.

### What renamed

| Before | Now (in the operator console + Manual) | Where it still applies as-is |
|---|---|---|
| Lanes | **Routines / Automations** | Legacy `lanes:` key and `shipctl lanes` wrapper commands |
| Patterns (in user-facing prose) | **Plays** | `pattern:` field; catalog folder layout |
| Pipelines | **Runs** | `pipeline_runs` table; backend telemetry |
| Clarifications + Improvements (separate UIs) | **Inbox** (single attention surface) | DB table names retained for compatibility |
| Fleet vs. Repo (separate views) | unified **scope** parameter | scope is still a query string param everywhere |
| Wizard presets (`web-app` / `non-web-app` / …) | one canonical bootstrap (`DEFAULT_BUNDLE`) | legacy preset names accepted for back-compat via `normalize_preset()` |

### What pages changed

- New: `automations.md` (replaces `lanes.md`), `internal/navigator-tools.md`
- Rewritten: `index.md`, `concepts.md`, `knowledge-buckets.md`, `configuration.md` (intro + Vocabulary callout), `operating.md`, `troubleshooting.md`, `discovery.md`, `agent-matrix.md`, `authoring.md` (added Author's-view-vs-Operator's-view callout), `authoring/pattern-vs-knowledge.md`
- Stub kept: `lanes.md` redirects to `automations.md`
- Promoted: RFC-0010 from Draft → Accepted (Phases 1-6 shipped)

### What did **not** change

- The `.ship/config.yml` schema. `lanes:` is still the YAML key.
- `shipctl` command surface (renamed in Phase 8 — see CLI changelog).
- The catalog folder layout (`artifacts/<kind>/<category>-<name>/ARTIFACT.md`).
- RFC-0001 through RFC-0009 normative content.

### Cross-reference for older bookmarks

Old console paths now 301 to new paths:

| Old | New |
|---|---|
| `/lanes`, `/fleet/lanes` | `/automations?scope=...` |
| `/pipelines`, `/fleet/requests` | `/runs?scope=...` |
| `/clarifications`, `/improvements` | `/inbox?type=...` |
| `/fleet/policy` | `/settings/policy` |
| `/fleet/adoption` | `/automations?tab=coverage` |

Old Manual paths:

| Old | New |
|---|---|
| `/docs/lanes` | `/docs/automations` (a thin redirect lives at `/docs/lanes` for now) |
