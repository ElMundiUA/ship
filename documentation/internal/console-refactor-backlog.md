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

## PR-6 — AI-create pattern in Lanes/Requests · pending

User-described scope: "on the Lanes/Requests tabs there should be
a way to quickly create additional patterns with AI when
something is missing". This is the RFC-0008 follow-on that kills
the separate Catalog tab.

Open questions before we start:

- Where does the "generate new pattern" button live — Lanes tab,
  Requests tab, or both?
- Does it produce a pattern in the shared catalog (persisted) or
  a one-off scratch pattern for this repo only?
- What's the guardrail — LLM proposes YAML, we render a diff, user
  merges? Or do we auto-apply and show a rollback affordance?

---

## PR-7 — Knowledge graph · pending

Workspace-level knowledge primitive. No design yet. Likely reuses
the existing `KnowledgeBucket` / `KbChunk` schema but exposes a
graph view (repo ↔ pattern ↔ lane ↔ tag) the Navigator can walk.

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
