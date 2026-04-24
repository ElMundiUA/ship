# Operating

Day-2 recipes for an operator running Ship in a real repository. The order on this page mirrors the order an operator actually meets the work: open the **Inbox** to see what needs you today, use **Coverage** to find Plays you should be running but aren't, then drop into the `shipctl` recipes for sync / verify / doctor / telemetry / lanes / bootstrap / secrets when you need them. For single-command syntax see [`/cli`](/cli); for the meaning of every `.ship/config.yml` field see [Configuration](/docs/configuration); when a noun is unfamiliar (Play, Automation, Run, Inbox, artifact, channel, pin, marker), look it up once in [Concepts](/docs/concepts). Failures are not on this page — when something is broken, jump to [Troubleshooting](/docs/troubleshooting).

> **Vocabulary box.** When this page says **Play** it means a row in the catalog at `/plays` (a `pattern:` ref under the hood). **Automation** is a Play assigned to a scope with a cadence — one row in `lanes:` in the affected repo's `.ship/config.yml`. **Run** is a single execution of a Play. **Inbox** is the single attention surface at `/inbox`. **Coverage** is the *"how many of my activated repos have this Play assigned"* view at `/automations?tab=coverage`. The console renames `lane` → `Automation` and `pipeline_run` → `Run` for the operator surface; the YAML and the CLI keep the protocol terms unchanged. See [Concepts](/docs/concepts) for the full vocabulary.

## Inbox first

The default Day-2 mental model is **Inbox-first**. When you sit down to work, open `/inbox` — it tells you what needs your attention today. Everything else (Plays / Automations / Runs / Coverage) is *understanding* surfaces; Inbox is the only *action* surface. If you're trying to decide what to do next and no Inbox row is screaming, the answer is usually "review Coverage and close a gap" — not "find something else to schedule".

### Drain the Inbox in the right order

- **Goal:** turn a fresh-coffee Inbox view into zero-by-noon without missing a high-severity item.
- **Steps:**
  1. Open `/inbox?owner=me&status=new` — items routed to you, oldest first. Each row's chip shows the type (`clarification` · `improvement` · `failure` · `approval` · `exception`) and the originating Play.
  2. Sort *failures* and *approvals* to the top — they're the ones that block someone else's Run or PR. Apply the typed disposition (Approve / Reject / Request changes for `approval`; Retry / Disable automation / Acknowledge for `failure`); reassign anything that isn't yours despite routing.
  3. Take *clarifications* next — answering one usually unblocks a Run that's already been spent on prep.
  4. Triage *improvements* in batch at the end. *Accept* enqueues an Automation; *Defer* parks the suggestion for the next planning cycle; *Decline* is a final disposition with a one-line reason.
- **What to check:** `/inbox?owner=me&status=new` empties out; the *Resolved today* counter on `/inbox` matches the number of dispositions you applied.
- **Common pitfall:** treating *snooze* as triage. Snooze is a hold; the item auto-returns. If you're using snooze to re-route, use *Reassign* instead so ownership is in the audit trail.

### Reassign instead of dropping

- **Goal:** move an item to the right owner when routing got it wrong, without losing the audit trail.
- **Steps:**
  1. Open the item. Hit *Reassign* and pick a user, or pick a [Group](/docs/concepts#routing-handles-groups-and-dispositions) — group reassignment runs the assignment strategy (`round_robin` / `oncall` / `first`) at intake and pins one user.
  2. Add a one-line reason in the comment box; it lands on the `assigned` event in the timeline.
  3. If you find yourself reassigning the same `(handle, repo)` pair repeatedly, the routing rule is wrong. Open `Settings → Inbox routing` and fix the rule itself — see [Troubleshooting → Inbox & routing](/docs/troubleshooting#inbox--routing).
- **What to check:** the item's *Owner* field changes; the timeline shows your *assigned* event with the reason; the next item from the same Play and handle resolves to the new owner without bouncing.
- **Common pitfall:** reassigning silently to keep your numbers tidy. The next operator on the same item won't know why; always leave the one-line reason.

### Snooze with intent

- **Goal:** hide an item until *something specific* unblocks it, without losing it.
- **Steps:**
  1. Hit *Snooze* on a row that genuinely depends on a future condition (a release window, an external answer, a tracker update).
  2. Pick a duration that matches the unblocker — 4 hours for a same-day question, until-Monday for a release-week pause. The item returns to `new` automatically when `snoozed_until` passes.
  3. If the unblocker landed early, the item is still snoozed — open `/inbox?status=snoozed` and pull it back.
- **What to check:** `/inbox?status=snoozed&owner=me` shows the row with its return time; the row reappears at the top of `new` when the time passes.
- **Common pitfall:** snoozing for a vague *"later"*. Snooze is a deadline you owe yourself; if you can't say what's supposed to change by then, it belongs on a backlog (Decline + open a tracker issue), not in Inbox.

## Coverage-driven adoption

Once Inbox is drained, the next question is always *"what should we be running that we aren't yet?"* — that's the Coverage view's only job.

### Find critical-uncovered Plays

- **Goal:** identify gaps where a critical Play (`scan-security-deps`, `scan-license-deps`, `flow-pr-self-review`, `flow-incident-postmortem`, `flow-release-notes`, `flow-cert-compliance`, `scan-pii-leakage`) isn't assigned everywhere it should be.
- **Steps:**
  1. Open `/automations?tab=coverage`. The list is sorted by uncovered count descending; critical Plays at less than full coverage carry a red badge.
  2. Drill into a red row to see the **covered / uncovered split** — which repos have the Play assigned and which don't, with the reason ("never assigned" / "explicitly disabled" / "unsupported preset").
  3. Scan for patterns across rows: an entire preset cluster missing the same Play usually means the preset's starter `lanes:` block doesn't include it; raise feedback against the preset.
- **What to check:** every red row either drops to *0 uncovered* (after fanning out) or carries a justified exception (e.g. *unsupported preset* — see [Authoring → preset](/docs/authoring#authoring-a-preset) to extend the preset).
- **Common pitfall:** filtering Coverage to *Mine* and concluding gaps don't exist — Coverage is workspace-wide on purpose, because the gaps that matter are *the ones nobody owns yet*.

### Roll an Automation out across uncovered repos

- **Goal:** close a Coverage gap with one wizard pass instead of editing each repo's `.ship/config.yml` by hand.
- **Steps:**
  1. From a Coverage drill-down row, hit **Apply to all uncovered**. The Automate wizard opens with the Play, scope (`selected: <uncovered repos>`), and a sensible default cadence pre-filled.
  2. Review the cadence (event vs schedule) and the per-repo overrides. The wizard opens **one PR per affected repo** appending the new row to that repo's `lanes:` block.
  3. Track the PRs from the wizard's confirmation page (or `/automations?status=pending`); each Automation appears on the page once its PR merges and the webhook lands.
- **What to check:** the Coverage row's `N / M` ratio climbs as PRs merge; the [Automations](/docs/automations) Active tab shows the new rows with their next-run-at; `/automations?tab=coverage` re-sorts to surface the next gap.
- **Common pitfall:** queueing one giant fan-out across dozens of repos before the first PR merges. The PRs land asynchronously; flip the wizard's *Stagger by tier* toggle when the affected set crosses ~20 repos so review load stays manageable.

### Schedule a Coverage review

- **Goal:** keep Coverage from drifting between adoption sweeps.
- **Steps:**
  1. Add an Inbox-only "Coverage review" reminder on a cadence that matches your team's planning rhythm (weekly for product engineering, monthly for platform). Today this is a recurring calendar event pointing at `/automations?tab=coverage` — there's no native scheduled-review object yet (slated for v2).
  2. During the review, scan the red badges first, then the *uncovered count desc* list, then the per-category drill-downs. The output is *one decision per row*: assign now, defer with a tracker link, or accept the gap with a one-line justification on the row.
  3. Cross-check the result against `/runs?has_escalations=true` — gaps in Coverage often correlate with Inbox items that the missing Play would have prevented.
- **What to check:** every red row from the previous review either dropped to *0 uncovered* or carries a justification note; the *uncovered count desc* tail stays bounded run-over-run.
- **Common pitfall:** waiting for Coverage to stabilise before reviewing it. Coverage is a moving target — repos activate, presets change — so the right cadence is *every iteration*, not *when it looks ready*.

## Sync & cache

The cache (`.ship/cache/<kind>/<id>@<version>/`) is `.gitignore`d by default. `shipctl sync` is idempotent: nothing is written if the manifest, the local `.meta.json` sha256, and the body on disk all agree.

### Refresh after a methodology release

- **Goal:** pull the newest artifact versions for everything you already use.
- **Steps:**
  1. `shipctl sync --check-only` to preview which entries would update.
  2. `shipctl sync` to fetch them.
  3. `shipctl verify --check artifacts-up-to-date` to confirm the cache matches the channel manifest.
- **What to check:** the `updated` count in the sync summary; `artifacts-up-to-date` reports `pass`. `.ship/state.json` gets a fresh `last_sync_at` and `last_manifest_hash`.
- **Common pitfall:** running `sync` on a CI runner with no `.ship/cache/` warmup — the first run pulls everything and counts as `updated`, not `up_to_date`.

### Pin a specific version

- **Goal:** freeze one artifact at a known-good version while everything else tracks `latest`.
- **Steps:**
  1. `shipctl config set artifacts.pins.pattern/role-developer 1.4.2` (the dotted key keeps the `<kind>/<id>` slash).
  2. `shipctl sync` — newer manifest versions for that pin show up as `skipped_pin` instead of `updated`.
- **What to check:** `shipctl config get artifacts.pins.pattern/role-developer` echoes the pin; the next sync summary lists `skipped_pin: 1` for the pinned entry.
- **Common pitfall:** pinning an artifact that is not in the manifest — `shipctl config validate` fails with exit 10. Pin only ids you have already pulled at least once.

### Switch channel

- **Goal:** flip the whole repo between `stable` and `edge` (e.g. to dogfood an upcoming preset).
- **Steps:**
  1. `shipctl sync --channel edge --check-only` to see what would change without writing.
  2. `shipctl config set api.channel edge` to make the switch durable.
  3. `shipctl sync` and then `shipctl verify --check artifacts-up-to-date`.
- **What to check:** `shipctl config get api.channel` returns `edge`; the verify line reports `channel=edge`. To revert: `shipctl config set api.channel stable && shipctl sync`.
- **Common pitfall:** setting `SHIP_CHANNEL=edge` in your shell and forgetting it — env wins over config, so commands silently use `edge` until the variable is unset.

### Disable an artifact

- **Goal:** stop pulling and tracking one artifact without unpinning the rest.
- **Steps:**
  1. Remove the pin. Either hand-edit `.ship/config.yml` to delete the `artifacts.pins.<kind>/<id>` line, then `shipctl config validate`, OR run `shipctl config set artifacts.pins.<kind>/<id> ""` (empty string) which the writer prunes on the next `set`.
  2. `rm -rf .ship/cache/<kind>/<sanitized-id>@*` to drop the cached body.
  3. Check that no declared preset collection still references the id — e.g. `shipctl collection show preset-<preset>` — otherwise `sync` will re-materialise it as a preset dependency.
- **What to check:** `shipctl <kind> show <id>` (`pattern | tool | collection`) reports `not in cache`; the next sync does not re-create the folder.
- **Common pitfall:** expecting an `artifacts.disabled: [...]` list or a `shipctl config unset` command to exist — neither does. Ship deliberately treats pins as the opt-in allowlist; the way to "disable" an artifact is to stop pinning it and make sure no preset pulls it in.

### Recover from a corrupted cache

- **Goal:** force a clean re-fetch when an editor stomped on `ARTIFACT.md` or `.meta.json` drifted from disk.
- **Steps:**
  1. `shipctl verify --check cache-integrity` to confirm which entries fail their sha256.
  2. `rm -rf .ship/cache/` (the directory is gitignored — nothing is lost that the API cannot serve).
  3. `shipctl sync` to repopulate from the current manifest.
- **What to check:** `cache-integrity` returns `pass` (`N cached entries verified (sha256 ok)`); `shipctl verify` exit code is `0`.
- **Common pitfall:** trying to repair a single folder by hand — `sync` already has a physical-presence guard that re-fetches drifted bodies the next time you run it; deleting the folder is faster than editing in place.

### Inspect what version is in use

- **Goal:** know exactly which `<kind>:<id>@<version>` your agent is reading.
- **Steps:**
  1. `shipctl pattern show role-developer` (or `tool`, `collection`) prints the cached body and version.
  2. `shipctl pattern show role-developer --json` if you need it scriptable.
  3. `cat .ship/cache/pattern/role-developer@*/.meta.json` for the recorded `content_sha256`, `fetched_at`, and `channel`.
- **What to check:** the version on screen matches `shipctl config get artifacts.pins.pattern/role-developer` (when pinned) and the channel manifest entry.
- **Common pitfall:** using `shipctl search` instead — search hits the API and is not aware of which version you have on disk.

## Verify

`shipctl verify` is the post-adoption liveness check. Every check has a stable id; the run aggregates them and exits `0` unless one or more returned `fail` (warnings never fail).

### Read the output

- **Goal:** translate the table into "what should I do next".
- **Steps:**
  1. `shipctl verify` — top to bottom: each row is `[status] <check-id>  <detail>`.
  2. `shipctl verify --severity warn` once you only care about warnings and failures.
  3. `shipctl verify --json` when scripting; the response is `{checks:[…], summary:{…}, exit_code}`.
- **What to check:** the footer `N checks total: X pass, Y warn, Z fail, W skip` and the trailing `Exit code:`. A `skip` is information ("this combo has no template", "no agents declared"), not a problem.
- **Common pitfall:** treating warnings as failures in CI — they do not flip the exit code; gate on `summary.fail > 0` instead.

### Run in CI without network

- **Goal:** prove a runner started from a healthy on-disk state without hitting `ship.elmundi.com` or Linear.
- **Steps:**
  1. Commit `.ship/cache/` (set `cache.vcs_tracked: true` in `.ship/config.yml`) so the runner already has the bodies.
  2. `shipctl verify --no-network` in the workflow — the `network` category (`api-reachable`, `artifacts-up-to-date`, `tracker-labels`, `ci-secrets`) is skipped.
  3. Optionally narrow with `--check rules-markers,cache-integrity,bootstrap-files,config-present,gitignore-cache,stack-enums,agents-on-disk`.
- **What to check:** every `local` and `config` check is `pass`; `network` rows are `skip`; exit `0`.
- **Common pitfall:** forgetting to flip `cache.vcs_tracked` — without it, `.ship/cache/` is gitignored and `--no-network` will report `cache-integrity` as `skip` because there is nothing to verify.

### Interpret the most common pass/fail signals

- **Goal:** map each check id to the fix.
- **Steps / signals:**
  - `rules-markers fail: missing rule file <path>` → run `shipctl init --copy-rules --agents <agent>` to install or refresh the file.
  - `rules-markers warn: footer @X, cache has @Y` → the cache moved ahead of the installed rule; rerun `shipctl init --copy-rules --force`.
  - `cache-integrity fail: <n> entries tampered` → the body drifted from `.meta.json`; see [Recover from a corrupted cache](#recover-from-a-corrupted-cache).
  - `bootstrap-files fail: missing` (only fires for the `mobile-app + gh-actions + linear` triple) → re-run `shipctl init --bootstrap --force` to re-render the scaffolding.
  - `agents-on-disk warn: no on-disk signal for declared agents: <id>` → the config declares an agent that has no footprint; either install it (rerun `init --copy-rules`) or remove it from `stack.agents`.
- **What to check:** after each fix, re-run `shipctl verify --check <id>` to confirm the row turned `pass`.
- **Common pitfall:** chasing warnings on `tracker-labels` when `LINEAR_API_KEY` is unset — that check `skip`s without a key; export the key locally and re-run.

## Doctor

`shipctl doctor` is the inference layer; `shipctl verify` is the contract check. Doctor never makes network calls and never writes anything unless you ask it to.

### When to run doctor vs verify

- **Goal:** pick the right tool for the question.
- **Use doctor** when the question is "what is this repo?" — first time on an unfamiliar tree, after adopting a new agent footprint, or when an adapter detector has been updated.
- **Use verify** when the question is "is this Ship setup still healthy?" — after `sync`, after `init --force`, in CI on every PR.
- **What to check:** doctor's report ends with a numbered `Recommendations:` list; verify's footer is the `pass/warn/fail/skip` summary plus exit code.
- **Common pitfall:** running doctor in CI for gating — it never fails; verify is the gate.

### Persist the inferred stack

- **Goal:** capture doctor's findings so `shipctl init --bootstrap` and reviewers can read them in PRs.
- **Steps:**
  1. `shipctl doctor --write-inventory` — the human report still prints; `.ship/inventory.json` is rewritten atomically.
  2. `git add .ship/inventory.json` (it is committed by default; no secrets, no path leakage beyond what the config already shows).
- **What to check:** the trailing line `Wrote .ship/inventory.json.`; the JSON's `inferred` block matches what doctor printed.
- **Common pitfall:** running with `--cwd` pointed at a different repo and then committing the inventory in the current one — always run from the repo root you want to capture.

### Override doctor's guess in `.ship/config.yml`

- **Goal:** make config the source of truth when doctor's inference is wrong (e.g. doctor sees `linear` but the team uses Jira).
- **Steps:**
  1. `shipctl config set stack.tracker jira` (or any other field — see [Configuration](/docs/configuration#stack) for the enums).
  2. `shipctl doctor` — config now wins; doctor's report shows `Tracker: jira (config) · disk: linear (0.95)` and stops recommending Linear.
- **What to check:** the doctor report header reads `<value> (config)` for every overridden field; recommendations switch to `additive` ones (e.g. `shipctl init --agents …`) instead of `--bootstrap`.
- **Common pitfall:** setting `stack.agents: []` to silence detector noise — config wins over disk, so declared rules will never be installed; declare every agent you actually use.

### Re-run doctor after adding an agent

- **Goal:** pick up a new agent footprint (e.g. someone added `AGENTS.md` for Codex) without editing config by hand.
- **Steps:**
  1. `shipctl doctor` — confirms the new agent appears under `disk:` with confidence `≥ 0.5`.
  2. `shipctl init --agents <existing>,<new> --copy-rules` to install the new agent's rules.
  3. `shipctl verify --check rules-markers,agents-on-disk`.
- **What to check:** `agents-on-disk` is `pass`; `rules-markers` shows the new path with an `installed-from` footer.
- **Common pitfall:** running `init` without listing the existing agents — `--agents` is replace-not-merge, so omitting them drops their rules from `stack.agents`.

## Telemetry

Telemetry is opt-in and OFF by default. Nothing leaves the repo until you flip the switch and the outbox is flushed.

### Opt in or out

- **Goal:** decide whether anonymous artifact-usage events are shared.
- **Steps:**
  - In: `shipctl telemetry on --scope artifact_usage,improvement_drafts --yes`.
  - Out: `shipctl telemetry off` (also blanks every `scope.*` flag and stops the next flush from doing anything).
  - Status: `shipctl telemetry status` prints `share=`, `anonymous_id=`, `scope=`, `outbox_pending=`, `last_flush_at=`.
- **What to check:** `shipctl config get telemetry.share` matches the action you took; the `anonymous_id` is a UUID v4.
- **Common pitfall:** assuming `--yes` skips the scope prompt forever — `--yes` only skips confirmation; pass `--scope` explicitly when you want a non-default scope.

### Inspect the outbox

- **Goal:** see what would be sent before you flush.
- **Steps:**
  1. `shipctl telemetry buffer --limit 50` — last N lines from `.ship/telemetry-outbox.jsonl` with one-line summaries.
  2. `shipctl telemetry flush --dry-run` — prints `would flush <n> events to <baseUrl>/telemetry` and exits without sending.
  3. `shipctl telemetry flush` — sends in batches of 100; succeeded lines are removed from the file.
- **What to check:** after a successful flush, `outbox_pending=0` in `telemetry status` and `last_flush_at` updates in `.ship/state.json`.
- **Common pitfall:** editing the outbox file by hand — it is JSONL and any malformed line breaks `flush`. Use `shipctl telemetry off` to nuke it cleanly.

### Understand the denylist

- **Goal:** know which payload keys the CLI strips before anything lands in the outbox.
- **What is denied:** `path`, `code`, `diff`, `branch`, `remote`, `email` — see `DENYLIST_KEYS` in `cli/lib/telemetry/outbox.mjs`. The list is **protocol-level** (RFC-0003 §Denylist) and identical for every operator.
- **What is *not* configurable:** there is no `telemetry.denylist` key in `.ship/config.yml`. `shipctl config set telemetry.denylist …` will fail validation (`unknown key`). Local per-repo overrides are deliberately out of scope so telemetry envelopes are portable across teams.
- **What to check:** `SHIP_DEBUG=1 shipctl telemetry buffer --limit 1` prints `stripped denylisted keys from <event>: <key>` for each redaction the CLI performed.
- **If you need more redactions:** open a feedback draft against [RFC-0003](/docs/protocol#rfc-0003-telemetry-and-feedback) with the new key you want added. The denylist is changed in the spec, not in your config.

### Self-host the endpoint

- **Goal:** keep telemetry inside your perimeter.
- **Steps:**
  1. Stand up an HTTP service that accepts `POST /telemetry` per [RFC-0003](/docs/protocol#rfc-0003-telemetry-and-feedback) (`202 {accepted, rejected, reasons}`; `400` on denylisted keys; `429` on rate limit). Note: `shipctl run` / `shipctl sync` append `/api/methodology` to `api.base_url` for the artifact endpoints, but the telemetry endpoint posts directly against `<baseUrl>/telemetry`.
  2. Set `api.base_url` in `.ship/config.yml` to your endpoint root (telemetry shares the same base URL as the rest of the CLI).
  3. `shipctl telemetry status` to confirm the new base URL is in use; `shipctl telemetry flush --dry-run` previews the destination.
- **What to check:** the dry-run shows `would flush <n> events to <your-host>/telemetry`; a real flush returns success.
- **Common pitfall:** mixing self-hosted telemetry with the public artifact API — if `api.base_url` is your internal host, `shipctl sync` also goes there. Run a thin proxy that forwards `/patterns`, `/tools`, `/collections`, `/fetch` to `ship.elmundi.com` if you only want to intercept telemetry.

## Lanes (Automations on disk)

Each [Automation](/docs/automations) you see in the console is one row under `lanes:` in the affected repo's `.ship/config.yml`, plus a generated `.github/workflows/ship-<lane>.yml` wrapper per entry. The console renames `lane` → `Automation` for the operator surface; the YAML schema is unchanged from [RFC-0007](/docs/protocol/rfc-0007-lanes-and-run-agent). The field-level reference is in [Configuration → `lanes`](/docs/configuration#lanes); the operator surface is in [Automations](/docs/automations); this section is the YAML-first day-2 recipes for when you'd rather edit `.ship/config.yml` than walk the wizard.

### Install lane wrappers after editing `.ship/config.yml`

- **Goal:** regenerate the caller workflows so GitHub knows when to fire each lane.
- **Steps:**
  1. Edit `lanes:` in `.ship/config.yml` (add / remove / re-kind). Run `shipctl config validate` — an invalid lane exits `10` before you commit a broken file.
  2. `shipctl lanes install` — writes one `.github/workflows/ship-<lane>.yml` per declared lane, banner-guarded with `# ship-cli: lanes v1`. Idempotent; re-running is a no-op.
  3. Inspect each generated wrapper, then `git add .github/workflows/ship-*.yml .ship/config.yml` and commit in the same PR.
- **What to check:** `shipctl lanes list` prints the same set of lanes you see in `.ship/config.yml`; each generated file contains the reusable-workflow line `uses: ElMundiUA/ship/.github/workflows/run-agent.yml@v<shipctl_min>`.
- **Common pitfall:** a pre-existing `.github/workflows/ship-<id>.yml` without the Ship banner — `install` refuses to overwrite it. Delete the file or pass `--force` once you've confirmed it's safe.

### Lock lane patterns for reproducible CI

- **Goal:** pin the exact pattern bodies a lane runs so CI is reproducible and air-gapped runners work.
- **Steps:**
  1. `shipctl sync --lock` — walks every lane's `pattern` / `patterns`, materialises the body into `.ship/cache/pattern/<id>@<version>/`, and writes `.ship/shipctl.lock.json` with one entry per resolved pattern (`version`, `content_sha256`, `cached_path`).
  2. Commit `.ship/shipctl.lock.json` alongside `.ship/config.yml`. The lockfile has no secrets; everything in it is reproducible from the public manifest.
  3. When a live pattern version drifts from the lockfile, `shipctl run` prints a `pattern/<id> sha256 drift vs lockfile` warning on stderr and proceeds with the live body — re-run `shipctl sync --lock` to re-pin.
- **What to check:** `shipctl sync --lock` reports `wrote .ship/shipctl.lock.json (<N> entries, 0 unresolved)`. Unresolved entries exit `20`; re-run after fixing the pattern id.
- **Common pitfall:** forgetting to re-lock after bumping a `pattern_version:` — the lockfile pins an older sha and CI noisily warns on every run.

### Run a lane locally (or in CI)

- **Goal:** invoke a lane end-to-end without the GitHub Actions wrapper.
- **Steps:**
  1. `shipctl run --lane <id>` — resolves the lane, fetches the pattern, and emits the prompt on stdout (pipe into your agent). Only `kind: once` lanes execute fully today; `kind: event` and `kind: schedule` are recognised but emit `status: noop` on stdout (they rely on the GitHub Actions wrappers until Phase 3 of RFC-0007 wires the reusable workflow).
  2. In air-gapped CI, add `--offline` — the command resolves exclusively through `.ship/shipctl.lock.json` and `.ship/cache/` and never contacts the methodology API. Fails loud if the lockfile is missing a pattern.
  3. For local debugging, use `--dry-run` to print the prompt without writing the idempotency marker or firing the callback.
- **What to check:** `shipctl run --lane <id> --dry-run` prints the expected pattern body and exits `0`; on real runs `.ship/state/<idempotency.key>.json` is created for `kind: once` lanes.
- **Common pitfall:** running from outside the repo root — `shipctl run` searches upward for `.ship/config.yml` like every other command, but if you `cd` into a submodule it will find the submodule's config instead. Use `--cwd <repo-root>` to be explicit.

### Upgrade a legacy v1 config

- **Goal:** move a repo from the pre-lanes schema onto v2.
- **Steps:**
  1. `shipctl migrate --dry-run` — prints the proposed v2 config without writing; review the translation.
  2. `shipctl migrate --yes` — writes `.ship/config.yml.bak`, then rewrites `.ship/config.yml` with `version: 2`, `shipctl_min: "0.12.0"`, and a `lanes:` map seeded from preset defaults. Moves `stack.agent.provider` → `agent.default.provider`.
  3. Diff the result (`git diff .ship/config.yml`), then `shipctl config validate`.
  4. `shipctl lanes install` to render the caller workflows; `shipctl sync --lock` to pin the patterns.
  5. Commit `.ship/config.yml`, `.ship/shipctl.lock.json`, and the generated wrappers.
- **What to check:** `shipctl config show | head -5` reports `version: 2`; `shipctl run --lane <id>` no longer exits `2`.
- **Common pitfall:** running `shipctl migrate` against a v2 config and expecting a no-op to look different — it exits `0` with `already at the latest schema (no changes)`. That's success; keep going.

## Feedback

Feedback is always drafted locally before it is sent. Drafts live in `.ship/feedback-drafts/` (gitignored); after submit they move to `.ship/feedback-drafts/sent/`.

### Draft and submit

- **Goal:** open a GitHub issue on the Ship repository against a specific artifact.
- **Steps:**
  1. `shipctl feedback draft --kind pattern --id role-developer --version 1.4.2 --title "Missing mobile preview step" --summary "Evidence checklist misses mobile preview" --recommendation "Add a bullet under Evidence"`.
  2. `shipctl feedback list` to see the new draft path; `shipctl feedback edit <path>` opens it in `$EDITOR`.
  3. `shipctl feedback show <path>` for a final review.
  4. `shipctl feedback submit <path> --yes` — the CLI prints the issue URL and moves the file under `sent/`.
- **What to check:** the response includes `issue_url`; the draft is no longer in `feedback list` unsent rows.
- **Common pitfall:** missing `--summary` when running non-interactively — the CLI exits with `--title and --summary are required (interactive prompts unavailable)`.

### Where drafts live and the dedup window

- **Goal:** find drafts you started yesterday and avoid re-filing duplicates.
- **Steps:**
  1. Drafts: `.ship/feedback-drafts/<YYYY-MM-DD-HHMMSS>-<kind>-<id>.md`. Sent: same path under `sent/`. Use `shipctl feedback list` for the timestamped index.
  2. Dedup is server-side: when you submit, the API looks for an existing open issue with labels `artifact:<kind>:<id>` + `version:<version>`. If one exists, your submission is added as a comment and the response carries `deduplicated: true`.
- **What to check:** the CLI prints `(deduplicated: comment added to existing issue)` when dedup fires; the issue URL is the existing issue's URL.
- **Common pitfall:** assuming dedup means "do not submit" — the local draft still moves to `sent/` and a `feedback.submit` telemetry event is still emitted (when telemetry is on). Re-submitting against the same `<kind>:<id>:<version>` always lands as a comment on the same issue.

## Re-running init

`shipctl init` is the primary adoption entrypoint. Re-runs are safe by design: marker-guarded blocks are upserted in place, agent rule footers track which version is installed.

### When to re-run init

- **Goal:** know which change on disk justifies another `init`.
- **Re-run after:** a new agent footprint appears (`AGENTS.md`, `.cursor/`, `CLAUDE.md`); the preset changes (`shipctl config set stack.preset web-app`); a new agent-rules version was published and `verify` reports `rules-markers warn: footer @X, cache has @Y`; you want to add `--bootstrap` after a config-only adoption.
- **What to check:** `shipctl init --dry-run --copy-rules` shows the planned upserts before you commit; the actual run prints `Installed rules:` with each path marked `wrote` or `updated`.
- **Common pitfall:** treating `init` as destructive — without `--force`, every existing marker block stays put and only the unmarked sections of the file are appended.

### What `--force` does and does not touch

- **Goal:** know exactly what changes when you escalate.
- **`--force` does:** replace an agent rules block whose `installed-from` footer references a different version than the cached artifact; overwrite bootstrap scaffolding files (`.github/workflows/ship-pilot.yml`, `.ship/labels.yml`, `.env.example` ship-managed block) when `--bootstrap` is also set.
- **`--force` does not:** touch unmarked sections of an agent rules file; rotate the `anonymous_id`; modify `.ship/config.yml` beyond the `--tracker / --ci / --preset / --agents / --language / --channel` flags you passed; clear `.ship/cache/`.
- **What to check:** `shipctl init --dry-run --force` lists the same upserts as a normal run; the `Installed rules:` section reports `updated` for the rule files that got refreshed.
- **Common pitfall:** running `--force` to "fix" cache drift — `--force` does not resync. Use `shipctl sync` (or delete `.ship/cache/`) instead.

### Recover from a botched init

- **Goal:** revert when an `init --force` overwrote something you wanted to keep.
- **Steps:**
  1. `git status` — every file `init` writes is in the worktree (`.ship/config.yml`, agent rule files, `.gitignore`, bootstrap files). Nothing is written outside git.
  2. `git restore <path>` to revert specific files; `git restore .ship/ .gitignore` to revert the whole bootstrap.
  3. `shipctl init --dry-run` to preview a fresh run before you re-apply.
- **What to check:** `git diff` is empty after restore; `shipctl verify` reports the pre-init state.
- **Common pitfall:** running `init` from a repo with uncommitted changes — there is no automatic backup file. Commit (or stash) before any `--force` run so `git restore` is your undo.

## Bootstrap

`shipctl init --bootstrap` renders CI / tracker / secrets scaffolding from the cached preset artifact. Today there is one fully-supported triple; everything else gets a plan-only fallback.

### The supported preset triple

- **Goal:** get a runnable skeleton out of the box.
- **Triple:** `--preset mobile-app --ci gh-actions --tracker linear`.
- **Steps:**
  1. `shipctl init --bootstrap --yes --preset mobile-app --ci gh-actions --tracker linear --agents cursor,claude-md --copy-rules`.
  2. The renderer writes `.github/workflows/ship-pilot.yml` (`ship-managed: workflow` marker), `.ship/labels.yml` (`ship-managed: labels`, 8 mobile labels), and appends a `# --- ship-managed ---` block to `.env.example` with `LINEAR_API_KEY`, `LINEAR_TEAM_ID`, `GITHUB_TOKEN`, `EXPO_TOKEN`, `SENTRY_AUTH_TOKEN`.
  3. `shipctl verify` exercises the `bootstrap-files` check on the new files.
- **What to check:** all three files exist and carry their marker; `bootstrap-files` reports `pass`; `ci-secrets` lists every `${{ secrets.* }}` reference as declared in `.env.example`.
- **Common pitfall:** thinking the workflow is production-ready — the job bodies are placeholders (`echo "lint: placeholder"`). Open the file and wire the real commands; the markers are there so re-runs don't clobber your edits.

### Read the generated plan

- **Goal:** turn `SHIP_BOOTSTRAP_PLAN.md` into action.
- **Steps:**
  1. After every `--bootstrap` run, the renderer also writes (or refreshes) `SHIP_BOOTSTRAP_PLAN.md` at the repo root.
  2. The plan has four sections: **Chosen stack** (what `init` decided), **Recommended tools** (per-preset add-ons like Detox, Playwright, Atlas), **Recommended secrets / env** (the names that should appear in `.env.example`), and **Files to create / review** (a checklist with TODO bullets for the bits the renderer cannot fill).
  3. Run `shipctl collection show preset-<preset>` for the preset's full prose contract while you walk the checklist.
- **What to check:** every checklist item maps to a real file in your worktree or an explicit "no renderer yet" note.
- **Common pitfall:** treating the plan as one-shot — re-running `init --bootstrap` overwrites it with the current stack values. Commit the plan after you finish editing so the next run's diff is meaningful.

### Hand-bootstrap an unsupported triple

- **Goal:** stand up a preset whose `(preset, ci, tracker)` triple has no renderer yet (anything other than `mobile-app + gh-actions + linear`).
- **Steps:**
  1. `shipctl init --bootstrap --yes --preset web-app --ci gitlab-ci --tracker jira --agents cursor` — only `SHIP_BOOTSTRAP_PLAN.md` is written; the workflow / labels / env block are skipped.
  2. Open the plan and use **Recommended tools** + **Recommended secrets / env** as the source of truth.
  3. `shipctl collection show preset-web-app` for the preset's CI stages, label contract, and evidence types; mirror them in your CI config and tracker by hand.
  4. After hand-authoring, add a `# ship-managed: workflow` (and analogous markers for labels / env) header to your files so re-running `init --bootstrap` later does not duplicate them.
- **What to check:** `shipctl verify` skips `bootstrap-files` (`combo … has no bootstrap template`); `ci-secrets` still validates that every secret you reference appears in `.env.example`.
- **Common pitfall:** waiting for the renderer to catch up — you can ship without it. Add the markers manually so the day a renderer lands, your files upgrade in place.

## Secrets handling

Ship treats secrets as the operator's responsibility. The CLI never prompts for them and never writes their values to disk.

### Where Ship expects secrets

- **Goal:** know which file holds which thing.
- **`.env.example`** — the contract. Every `${{ secrets.X }}` reference in your gh-actions workflows must appear as `X=` here. The `ci-secrets` verify check enforces it.
- **`.env.local`** (or your platform's secret store) — the values. Never committed; never read by `shipctl`.
- **`.ship/config.yml`** — never. Pinning secrets here is a hard error in review; the file is committed.
- **Environment variables** — `LINEAR_API_KEY` (used by the `tracker-labels` verify check), `SHIP_API_BASE`, `SHIP_CHANNEL`, `SHIP_TELEMETRY` (operator overrides per [RFC-0002](/docs/protocol#rfc-0002-shipctl-config-schema)).
- **What to check:** `shipctl verify --check ci-secrets` is `pass`; `git grep` for token shapes in `.ship/` returns nothing.
- **Common pitfall:** dropping the actual key into `.env.example` — that file is committed; treat it as a manifest, not a vault.

### Per-adapter env vars

- **Goal:** know which secret to provision for the tools you wired.
- **`tool/linear`** → `LINEAR_API_KEY` (Linear personal API key; required by `verify --check tracker-labels` and by tracker adapters at runtime). See [Linear](/tools/linear).
- **`tool/github-actions`** → `GITHUB_TOKEN` is provided by the runner; declare any extra secrets you reference (`EXPO_TOKEN`, `SENTRY_AUTH_TOKEN`, etc.) in `.env.example`. See [GitHub Actions](/tools/github-actions).
- **`tool/playwright`** → no Ship-required secret; provision your test-account credentials per the [Playwright tool](/tools/playwright) doc.
- **`tool/snyk`** → `SNYK_TOKEN`; see [Snyk](/tools/snyk).
- **`tool/cursor-cloud-agent`** → cloud-agent token per the [Cursor Cloud](/tools/cursor-cloud-agent) doc; never paste it into prompts.
- **What to check:** every adapter's tool page lists its required env vars under "What you wire"; `shipctl init --bootstrap` for a supported triple seeds the right placeholder names.
- **Common pitfall:** rotating a key without updating both `.env.local` and the platform secret store — `verify` only sees `.env.example`, so a stale runtime secret will fail at execution, not at gate time.

## Where to next

When something on this page does not behave as described, jump to [Troubleshooting](/docs/troubleshooting) — that page is organized failure-first and maps each `shipctl` exit code to a recipe; the *Inbox & routing* section there covers fallback ownership and stuck reassignments. For the operator surfaces themselves see [Concepts](/docs/concepts) (Plays / Automations / Runs / Inbox), [Automations](/docs/automations) (the Active and Coverage tabs in detail), and [Knowledge buckets](/docs/knowledge-buckets) (what the Navigator and Plays consult). To add a new agent, tracker, or CI to the methodology rather than just consume one, read [Authoring](/docs/authoring) and the relevant RFC under [Protocol](/docs/protocol) — RFC-0010 is the normative spec for the four operator nouns.
