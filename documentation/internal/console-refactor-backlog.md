# Console refactor — shipped & deferred (RFC-0008 §D–§G)

Living scratchpad for the "two-mode UI" refactor (`/` workspace
mode, `/r/<slug>/…` repo mode). Each PR here ships something the
user can touch today; the **Deferred** sub-sections below each PR
capture the stuff we deliberately skipped so it doesn't leak out of
the loop. Move items into a new PR (or a follow-up bullet) when
work actually starts — don't silently delete them.

---

## PR-0 — Navigator in header · shipped

Global `⌘K` launcher opens Navigator as a fullscreen `/chat` page
instead of burying it in Inbox.

Deferred: none (this one was purely cosmetic).

---

## PR-1 — Two-mode shell · shipped

`AppShell` now renders workspace-vs-repo nav from `usePathname()`.
Workspace mode lives at `/`, repo mode at `/r/<owner>/<repo>/…`.
Fleet stubs (`/fleet/{requests,policy,adoption,knowledge}`) landed
as empty placeholders in this PR.

Deferred:

- **Workspace picker for multi-workspace users.** Today we
  implicitly grab `workspaces[0]`. Fine for pilot, needs a switcher
  in the header when real users have >1 workspace.
- **Repo switcher inside repo mode.** `AppShell` shows the repo
  chip but there's no quick-jump dropdown to another activated
  repo — every jump goes through Workspace Home.
- **Deep-link preservation across mode switches.** Clicking the
  Navigator launcher from `/r/<slug>/lanes` should eventually
  scope the Navigator session to that repo; right now it's
  workspace-only.

---

## PR-2 — Fleet Requests MVP · shipped

`POST /v1/workspaces/{ws}/fleet/requests` with best-effort fan-out
(pre-flight validation rejections persisted on the parent as JSONB).
Console has list / new / detail pages with repo multi-select and
per-child GitHub Actions deep-links.

Deferred:

- **Retry of rejected children.** A "retry failed repos" action on
  the detail page that replays just the `rejected` / `failed`
  children without re-validating the parent pattern.
- **Bulk cancel from the list view.** Cancel currently requires
  opening each fleet request.
- **Fleet request templates.** Save "pattern X + these inputs +
  these repos" as a preset so operators don't rebuild the form
  every Tuesday.
- **Pagination on `GET /fleet/requests`.** Ships unpaginated today;
  fine until the history gets big.
- **Webhook / Slack notifications** when a parent flips to
  `partial` or `failed`. Current signal is "open the Console".

---

## PR-3 — Adoption funnel · shipped

`GET /v1/workspaces/{ws}/adoption?window_days=14` returns the
five-stage funnel (installed → activated → seeded → first_run →
steady) plus orthogonal flags (`install_missing`,
`bundle_out_of_date`, `stuck`, `cold`). Console renders
StatTiles + sorted repo table.

Deferred:

- **Funnel trend over time.** Today it's a snapshot. A weekly
  history table ("Steady grew from 3 → 7 this week") would make
  the funnel a motivational tool instead of a status board.
- **Configurable window picker in UI.** API accepts `window_days`
  but the page hard-codes the default.
- **One-click "nudge" action** per stuck/cold repo — e.g. open a
  Navigator thread pre-seeded with "why didn't this repo run
  anything in 14d?".
- **Export / CSV of the repos table** for platform teams that
  want to spreadsheet the rollup.

---

## PR-4 — Repo-home Now/Trends · shipped

`GET /v1/workspaces/{ws}/repos/{id}/home?window_days=30` single
endpoint for both tabs. Console `/r/<slug>` renders pill tabs,
`NowTab` (in-flight, 24h splits, attention flags, recent activity),
`TrendsTab` (stacked-bar histogram, per-lane breakdown).

Deferred:

- **Per-lane drill-down from Trends.** Clicking a lane row should
  open a filtered Trends view (last N runs of *that* lane only).
- **Live updates on the Now tab.** Currently a page load. A
  lightweight SSE or revalidate-every-30s would close the gap
  between "I clicked run" and "I see it".
- **Custom time windows.** UI locked to 30d window the backend
  supports 1–180d; expose a picker.
- **Compare repos in Trends.** "Show API vs Web success rate side
  by side" — needs a second repo selector on the page.

---

## PR-5 — Policy primitive · shipped (mirror-lane MVP)

`workspace_policies` + `workspace_policy_exceptions` tables,
compliance rollup keyed on `Pipeline.lane_id`. Console
`/fleet/policy` lists policies with `StatTile` funnel and
expandable per-repo opt-out toggles; `/fleet/policy/new` has a
pattern picker + cadence field.

Deferred:

- **Drift detection (`.ship/config.yml` vs policy).** Today
  compliance is "does the DB have a matching `Pipeline` row?".
  Real drift needs parsing the live config SHA and diffing lanes +
  inputs. Big enough to be its own PR.
- **Navigator autofix.** "One PR per repo to bring config back in
  line with the policy" — depends on drift detection being real.
- **Other policy kinds.** Schema has a `kind` discriminator but
  only `mirror_lane` is wired:
  - `required_request` — "every request of type qa must pass
    before merge" (pull-request check).
  - `required_check` — minimum success rate or freshness on a
    given lane before a repo counts as healthy.
- **Policy enable/disable toggle in UI.** DB column exists
  (`enabled`), but form doesn't edit it and list view just shows
  a badge when disabled.
- **Edit existing policies.** Today you can create + delete; to
  change cadence you delete and recreate.
- **Per-policy input defaults editor.** `inputs` column accepts
  JSONB but the form doesn't expose it. Works as default-only
  until we add the UI.
- **Cadence validation at create time.** We let any string through;
  the workflow materialiser is the one that eventually rejects
  bad cron. Surface that earlier.
- **"Apply now" action.** Creating a policy doesn't materialise
  the lane on missing repos automatically — that's still the
  wizard's job. Could change this later with a bulk-open-PR flow
  (depends on Navigator autofix).

---

## PR-6 — AI-create pattern in Lanes/Requests · shipped

Shipped in this PR:

- **Workspace-private catalog layer.** New `custom_patterns` table
  (migration `0026`), merged on top of the baked-in catalog via
  `catalog_service.list_patterns_for_workspace()` so collisions
  resolve in favour of the workspace row. Every `CatalogEntryOut`
  now carries a `source: "builtin" | "workspace"` tag.
- **Backend CRUD + LLM draft.** New router at
  `/v1/workspaces/{ws}/patterns` covers `GET` / `POST` / `DELETE`
  plus a non-persistent `POST .../draft` that calls
  `AgentClient.acomplete` in JSON mode to produce a reviewable
  `PatternDraft` from a free-form brief. Guards reject built-in
  id collisions, enforce per-workspace uniqueness, and block
  deletion if a `WorkspacePolicy` references the pattern.
- **`GET /v1/catalog/patterns?workspace_id=…`.** Same endpoint as
  before, but returns a merged view when `workspace_id` is set.
  Membership checked via `ROLES_READ`.
- **Console AI author modal** (`pattern-ai-author.tsx`) — two-stage
  flow (brief → review). Wired into the `New fleet request` and
  `New policy` forms. Newly-saved workspace patterns are spliced
  into the local picker state and badged `custom`.
- **Schema tolerance.** `CatalogEntryOut.content_sha256` now
  coerces non-string placeholders (e.g. `0`) to `str` so newly-
  added Wave-2 patterns that haven't had their digest backfilled
  yet don't 500 the picker endpoints. Temporary shim; drop once
  the catalog backfill lands.

Deferred:

- **Repo-mode integration (Lanes/Requests in `/r/<slug>/…`).** The
  fleet forms use the unified `ApiCatalogPattern`, but the
  repo-level Lanes/Requests screens still consume the older
  `ApiLaneCatalogEntry` / request-form shape. Wiring the same
  modal there needs a thin adapter — pushed to a follow-up so this
  PR stays scoped to Fleet forms.
- **Repo-local patterns in `.ship/config.yml`.** User asked for
  "both" workspace-private + repo-local. We only ship the first
  flavour; the second (write-through into `.ship/config.yml`
  custom_lanes) needs a PR change and is its own PR.
- **Draft-pattern caching / resume.** The draft endpoint is
  stateless — regenerate throws the previous JSON away. Fine for
  MVP, but a "Keep my edits, regenerate the body only" option
  would cut token cost during iteration.
- **Fleet-request list sort flake.** Pre-existing test
  `test_fleet_list_returns_newest_first` is flaky because
  `created_at` collisions at ms resolution break the deterministic
  order. Unrelated to PR-6 scope; tracked separately.

---

## PR-7A — Workspace knowledge search + override flag · shipped

Foundation of the workspace-level knowledge primitive. Splits the
former "PR-7 — Knowledge graph" stub into three landing PRs
(7A → 7B → 7C). Shipped in this PR:

- **Migration 0027**: `bucket_articles.overrides_workspace_article_id`
  (self-FK, ondelete `SET NULL`, partial index on non-null values).
  NULL = regular article; non-null = this row intentionally overrides
  the referenced workspace-canonical article. Distinct from
  `supersedes_id`, which keeps intra-bucket versioning semantics.
- **`POST /v1/workspaces/{ws}/knowledge/search`**. Embeds the query
  through the existing agent-embedding helper, runs parallel vector
  searches over `bucket_articles` + `kb_chunks`, then re-ranks hits
  into three buckets: `repo_match` (hint repo), `workspace`
  (workspace-scope canonical), `other_repo` (everything else).
  Returns 412 if the embedding service is unconfigured, matching the
  LLM-unconfigured convention from PR-6.
- **`GET /v1/workspaces/{ws}/knowledge/canonical`**. Lists every
  workspace-scope bucket with `article_count` and `override_count`
  (how many per-repo articles currently override it). Also surfaces
  **orphan slugs**: slugs present in ≥2 repo-scope buckets with no
  workspace-scope copy — the 7B promotion pipeline consumes this.
- **Console**:
  - `searchKnowledge` + `getKnowledgeCanonical` helpers in
    `lib/api/client.ts`.
  - `/api/knowledge/{search,canonical}` Next.js proxies keep the
    session bearer server-side.
  - `/fleet/knowledge` is no longer a `FleetStub` — it's a Search +
    Canonical tabbed page driven by the two new endpoints, with a
    repo-boost dropdown from `listActivatedRepos`.
  - Per-repo bucket article rows now render a
    "overrides workspace canonical" badge + deep-link when
    `overrides_workspace_article_id` is set. `ApiBucketArticle`
    (and `BucketArticleOut` on the backend) gained the
    `overrides_workspace_article_id` +
    `overrides_workspace_bucket_slug` fields.
- **Tests**: `backend/tests/test_v1_knowledge_search.py` covers
  repo-match ranking, workspace fallback, 412-on-unconfigured-
  embeddings, canonical listing, override counting, and orphan-
  slug detection. Existing suites unchanged (same pre-existing
  `test_fleet_list_returns_newest_first` flake as before).

What's *not* in this PR:

- No dedup / canonicalisation / LLM promotion — that's 7B.
- No Navigator tool for ambient knowledge pull — that's 7C.
- No graph/edge visualisation — RFC decided workspace search
  replaces the graph idea for the foreseeable future.

---

## PR-7B — Dedup clustering + LLM promotion drafts · shipped

Turns the `orphan_slugs` discovery feed from 7A into an actionable
promotion flow. Shipped in this PR:

- **Migration 0028**: `knowledge_promotion_candidates` cache table
  (workspace_id + fingerprint unique, ttl_expires_at index). One row
  per cluster; `article_ids` is a JSONB list, `slug_hint` +
  `centroid_score` drive the Console's ranking/hint UX.
- **`knowledge_dedup` service.** On-demand clustering: loads every
  non-archived, repo-scope `bucket_article` with an embedding, runs
  pairwise cosine similarity (pure-Python pass — documented choice
  for MVP-size workspaces), unions edges ≥ `0.85` into connected
  components, keeps components with ≥2 members drawn from ≥2 repos.
  Upserts the fresh fingerprint set, deletes stale rows, stamps a
  24h TTL.
- **`knowledge_promotion` service.** `draft_canonical(session, ws,
  article_ids, llm_client)` loads the cluster's articles + parent
  buckets, asks the model via `AgentClient.acomplete` in JSON mode
  for a `{slug, title, body, summary, notes}` payload, salvages the
  JSON via a new `services/json_utils.salvage_json` helper shared
  with the custom-pattern drafter.
- **New endpoints** (all under `/v1/workspaces/{ws}/knowledge`):
  - `GET  /candidates` — fresh cache or recompute, always hydrated
    with member + repo context; `is_fresh` tells the Console whether
    we just rebuilt.
  - `POST /candidates/refresh` — admin-gated forced recompute.
  - `POST /candidates/{id}/draft` — admin-gated LLM draft; 412 when
    the LLM is unconfigured (same banner the pattern drafter uses).
  - `POST /promote` — creates the workspace-scope bucket + article,
    optionally sets `overrides_workspace_article_id` on each source
    (skipping ones already pointing elsewhere), invalidates every
    candidate row that overlaps the promoted sources.
- **Console**:
  - `listKnowledgeCandidates` / `refreshKnowledgeCandidates` /
    `draftKnowledgePromotion` / `promoteKnowledge` in
    `lib/api/client.ts`, with Next.js proxies at
    `/api/knowledge/{candidates,candidates/[id]/draft,promote}`.
  - Third `/fleet/knowledge` tab **Promote candidates**. Each card
    shows slug hint, member / repo counts, centroid as a percentage,
    and a preview of up to four members. "Draft with AI" opens a
    modal with spinner → editable slug/title/body → Regenerate /
    Promote. Successful promotion closes the modal, shows a success
    banner, and re-fetches the list.
- **Schema tolerance.** `BucketSource` gains a `"promoted"` literal.
  `knowledge_buckets.source_kind` is an unconstrained `String` at
  the DB layer (confirmed via migration `0014`), so no schema change
  is needed beyond the new Python constant.
- **Tests**: `backend/tests/test_v1_knowledge_promotion.py` covers
  cross-repo cluster detection, same-repo skip, the similarity
  threshold, cache freshness (`is_fresh` flip), forced recompute
  after an embedding mutation, the LLM happy path with a fake
  `AgentClient`, 412 when the LLM is unconfigured, promotion
  mechanics (bucket + article creation, override linking, respecting
  pre-existing overrides), and cache invalidation after promote.
  Existing suites unchanged (same pre-existing
  `test_fleet_list_returns_newest_first` flake as before).

What's *not* in this PR:

- No Navigator tool. That's 7C.
- No background scheduler — clustering is on-demand with a 24h TTL;
  fine for MVP-size workspaces. A scheduled recomputer can come
  later without a schema change.
- No partial-cluster invalidation. On any promote overlap we wipe
  the matching rows; the next GET recomputes the full candidate
  set. Cheap at MVP scale, simpler than read-through revalidation.

---

## PR-7C — Navigator knowledge tool · pending

Adds a workspace-aware Navigator tool backed by
`POST /knowledge/search`, so agent threads can ambiently pull
workspace canonical + repo-local context in the same call. Depends
on 7A for the endpoint and on 7B for reliable canonicals.

---

## Cross-cutting deferred

- **Dashboard screenshots in docs.** Every PR above added UI; the
  internal product deck still shows PR-0 screens. Stale.
- **E2E coverage.** We only have Playwright tests from the pre-
  refactor era. A smoke suite that clicks Workspace Home → a
  repo → Lanes → back → Fleet Requests would prevent regressions.
- **Mobile / narrow viewport.** Two-mode shell assumes desktop
  widths. StatTiles and the repo table break below ~640px.
- **i18n.** Strings are inline English everywhere in the new pages.
