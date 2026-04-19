# Troubleshooting

Failure-first lookup. Find the symptom with `Cmd-F` (operator language, not internals), then read the four-part entry: **Symptom · Likely cause · Fix · Where to verify**. For non-failure recipes go to [Operating](/docs/operating); for field definitions to [Configuration](/docs/configuration); for command surface to [/cli](/cli).

## `shipctl init`

#### Init refuses my `--preset` value

- **Symptom:** init exits 1 with `init: unknown --preset "<x>". Allowed: web-app, api-backend, mobile-app, cli, monorepo, adoption-minimum`.
- **Likely cause:** typo or a preset id that hasn't shipped yet — `PRESETS` in `cli/lib/config/schema.mjs` is the closed enum.
- **Fix:** pick a value from the allowed list (same enum applies to `--tracker`, `--ci`, `--language`, `--channel`, `--agents`). For unsupported combos, scaffold by hand from `SHIP_BOOTSTRAP_PLAN.md` (see [Operating → Bootstrap](/docs/operating)).
- **Verify:** `shipctl init --dry-run --preset <x>` exits 0 and prints the plan header.

#### Init says my agent id is unknown

- **Symptom:** `init: unknown agent "<x>". Allowed: aider, claude, claude-md, cline, codex, ...`.
- **Likely cause:** the id you passed is not a key of `KNOWN_AGENTS` (`cli/lib/detect.mjs`).
- **Fix:** use one of the listed ids; agent rule artifacts on the manifest are named `agent-rules-<agent>` for each.
- **Verify:** `shipctl doctor` lists the same id under "Agents:" with a confidence score, and `shipctl collection list` shows `agent-rules-<agent>`.

#### `--copy-rules` did nothing — no rules were installed

- **Symptom:** init prints `warn: --copy-rules: no cached artifact for collection/agent-rules-<agent> (was the fetch successful?)` and the rules file is missing on disk.
- **Likely cause:** the embedded sync step failed for that artifact (offline, 4xx, sha mismatch). Init logs `warn: artifact fetch partially failed (...)` earlier in the same run.
- **Fix:** re-run after fixing the network/auth issue.
  ```bash
  shipctl sync --only collection:agent-rules-<agent>
  shipctl init --copy-rules --agents <agent>
  ```
- **Verify:** `ls .ship/cache/collection/agent-rules-<agent>@*` and the install target file (e.g. `.cursor/rules/ship-artifacts-protocol.mdc`) both exist.

#### Init refuses to overwrite my existing rules file

- **Symptom:** `warn: <path> has ship-cli installed-from @X.Y.Z; pass --force to replace with @A.B.C` and the install record reports `action: skipped`.
- **Likely cause:** the previously installed `installed-from` footer pins a different version; init is being conservative on purpose (`installAgentRule` in `cli/lib/commands/init.mjs`).
- **Fix:** verify your custom edits sit *outside* the marker block (`<!-- ship-cli: artifacts-protocol v1 -->` … `<!-- ship-cli:end artifacts-protocol -->`), then rerun with `shipctl init --copy-rules --force`. Hand edits inside the marker block will be replaced.
- **Verify:** the footer line at the bottom of the file reads `<!-- ship-cli: installed-from collection/agent-rules-<agent>@<new-version> -->`.

#### Telemetry flag rejected

- **Symptom:** `init: --telemetry must be on|off|ask (got "<x>")` and exit 1.
- **Likely cause:** `--telemetry` only accepts those three literals.
- **Fix:** `--telemetry off` for non-interactive runs (the same default `--yes` picks); `--telemetry on` to opt in immediately.
- **Verify:** the printed summary shows `Telemetry: on|off`.

#### Init exits with `--copy-rules: no install_target for <agent>`

- **Symptom:** warn line `--copy-rules: no install_target for <agent>; skipping` and the target file is not written.
- **Likely cause:** the cached `agent-rules-<agent>` artifact has no `install_target` in its front-matter and the agent id has no `KNOWN_AGENTS` fallback (`fallbackInstallTarget` in `cli/lib/commands/init.mjs`).
- **Fix:** sync to refresh the artifact (front-matter may have been added since); if the artifact genuinely lacks `install_target`, raise it via `shipctl feedback draft --kind collection --id agent-rules-<agent>`.
- **Verify:** `shipctl collection show agent-rules-<agent>` displays `spec.install_target` in the front-matter.

#### My tracker/CI/preset triple has no bootstrap template

- **Symptom:** with `--bootstrap`, you only get `SHIP_BOOTSTRAP_PLAN.md` and no real workflow files; the bootstrap-files verify check skips with `combo <x>+<y>+<z> has no bootstrap template`.
- **Likely cause:** v1 only special-cases `mobile-app + gh-actions + linear` (`cli/lib/verify/checks/bootstrap-files.mjs`). Every other combination falls through to plan-only.
- **Fix:** follow the TODO checklist in `SHIP_BOOTSTRAP_PLAN.md` to hand-bootstrap, or pick the supported triple while you pilot.
- **Verify:** `shipctl verify --check bootstrap-files` reports `skip` (expected) for unsupported combos and `pass` once the supported triple is rendered with `ship-managed` markers.

## `shipctl sync`

#### Sync fails before contacting the API: ".ship/ not found"

- **Symptom:** `.ship/ not found. Run 'shipctl config init' first.` and exit 10.
- **Likely cause:** `findShipRoot` walked up from cwd and found no `.ship/config.yml` (`cli/lib/config/io.mjs`).
- **Fix:** `cd` to the repo root, or pass `--cwd <repo>`, or run `shipctl config init` first.
- **Verify:** `shipctl config path` prints the absolute config path.

#### Sync reports `content_sha256 mismatch`

- **Symptom:** `failed: <kind>/<id>@<version> content_sha256 mismatch (manifest=<x> got=<y>)` and exit 20.
- **Likely cause:** the body returned by `POST /fetch` does not match the hash in the manifest — usually a stale CDN edge or a partial transfer (`syncArtifacts` in `cli/lib/commands/sync.mjs`).
- **Fix:** re-run `shipctl sync` (transient); if it persists for the same version, file feedback so the artifact is re-stamped (the server hashes per RFC-0005, see `normalizeForArtifactSha` in `cli/lib/cache/store.mjs`).
- **Verify:** the next sync prints `updated: 1` (or `up_to_date`) for the same key with `failed: 0`.

#### Sync says my pin was skipped

- **Symptom:** `skipped_pin: <kind>/<id> pinned=<pin> upstream=<version>` in the notes; the entry is not refreshed.
- **Likely cause:** the manifest version no longer satisfies the value at `artifacts.pins.<kind>/<id>` in `.ship/config.yml` (`pinSatisfies` in `cli/lib/commands/sync.mjs`).
- **Fix:** intentional — bump or remove the pin (`shipctl config set artifacts.pins.<kind>/<id> <new>`), or test with `shipctl sync --force-unpin`.
- **Verify:** `shipctl <kind> show <id>` prints the version you expect and the next sync moves the entry to `updated`.

#### Sync says an artifact is `yanked` or `deprecated`

- **Symptom:** notes line `yanked: <key>@<v>` or `deprecated: <key>@<v> → <replaced_by>`.
- **Likely cause:** the publisher marked the version `yanked: true` (hard refusal) or `deprecated: true` (soft warning) in the manifest.
- **Fix:** for `deprecated`, follow `replaced_by` and update your pin/agents list. For `yanked`, you must move off the version — pinning it will be ignored on next sync.
- **Verify:** `shipctl sync` shows `yanked: 0` and `deprecated: 0` after you migrate.

#### Sync re-fetches even though nothing changed upstream

- **Symptom:** notes line `refetch: <key>@<v> (missing|drifted|...)` on every run.
- **Likely cause:** the on-disk body was deleted or hand-edited after caching; `verifyCachedOnDisk` (`cli/lib/cache/store.mjs`) detects drift and forces a re-fetch (this is the integrity guard).
- **Fix:** stop editing files under `.ship/cache/` directly. If you need a local override, check it into your repo as a *fork* of the artifact, not in the cache.
- **Verify:** the next sync moves the entry to `up_to_date` and stays there.

#### Sync gets HTTP 401/403/5xx from the methodology API

- **Symptom:** sync exits 20 with `HTTP 401 Unauthorized for <baseUrl>/...` (or 403 / 5xx) and stops before any cache writes.
- **Likely cause:** `SHIP_API_TOKEN` missing/invalid for a private mirror, or the configured `api.base_url` is unreachable.
- **Fix:** export a valid `SHIP_API_TOKEN` (or unset it for the public host); confirm `api.base_url` in `.ship/config.yml` and `SHIP_API_BASE` agree.
- **Verify:** `shipctl verify --check api-reachable` returns `pass`.

#### Sync hangs — no progress, no exit

- **Symptom:** sync produces no output and never returns.
- **Likely cause:** a Node `fetch` to `api.base_url` is blocked by an outbound proxy/firewall; the CLI uses no timeouts (`cli/lib/http.mjs`).
- **Fix:** Ctrl-C, then re-run with the right proxy env (`HTTPS_PROXY=...`) or point `SHIP_API_BASE` at a reachable mirror. `api.offline_ok: true` lets you operate from cache.
- **Verify:** `curl -sS $SHIP_API_BASE/health` returns a body, and `shipctl verify --check api-reachable` passes.

## `shipctl verify`

#### `config-present` fails

- **Symptom:** `[fail] config-present  missing .ship/config.yml — run 'shipctl config init'` (or `... invalid: <error>`).
- **Likely cause:** the file isn't there, or `validateConfig` rejected it (`cli/lib/verify/checks/config-present.mjs`).
- **Fix:** `shipctl config init`, or apply `shipctl config validate` and fix the first reported error.
- **Verify:** `shipctl verify --check config-present` returns `pass`.

#### `stack-enums` fails

- **Symptom:** `[fail] stack-enums  stack.tracker: "<x>" is not valid. Expected one of: linear, jira, ...`.
- **Likely cause:** a `stack.*` field in `.ship/config.yml` is not in the enum (`cli/lib/config/schema.mjs`).
- **Fix:** edit the field with `shipctl config set stack.tracker <valid>` (see [Configuration](/docs/configuration) for enum lists).
- **Verify:** rerun `shipctl verify --check stack-enums`.

#### `agents-on-disk` warns "no on-disk signal for declared agents"

- **Symptom:** `[warn] agents-on-disk  no on-disk signal for declared agents: <agent>`.
- **Likely cause:** `stack.agents` lists an agent the detector can't find (no marker file/dir, no install_target on disk; `cli/lib/verify/checks/agents-on-disk.mjs`).
- **Fix:** install the rules with `shipctl init --copy-rules --agents <agent>`, or remove the agent from `stack.agents` if you don't actually use it.
- **Verify:** `shipctl doctor` shows the agent under "disk:" with non-zero confidence.

#### `rules-markers` fails on a rule file

- **Symptom:** `[fail] rules-markers  <agent>: <path> has no '<!-- ship-cli: artifacts-protocol v1 -->' marker` (or `... has no 'installed-from' footer`, or `footer @X, cache has @Y`).
- **Likely cause:** the rule file was hand-rewritten without the marker / footer; or `shipctl sync` fetched a newer agent-rules artifact and `--copy-rules` hasn't been run yet (`cli/lib/verify/checks/rules-markers.mjs`).
- **Fix:** `shipctl init --copy-rules --force` to reinstall the marker block, then keep custom content outside the markers.
- **Verify:** rerun `shipctl verify --check rules-markers` — should `pass` for every declared agent.

#### `cache-integrity` fails with "tampered"

- **Symptom:** `[fail] cache-integrity  N/M cached entries tampered: <kind>/<id>@<v>...`.
- **Likely cause:** the body in `.ship/cache/<kind>/<id>@<version>/ARTIFACT.md` was edited by hand and no longer matches `.meta.json:content_sha256` (`verifyCached` in `cli/lib/cache/store.mjs`).
- **Fix:** never edit cache bodies. Restore by re-syncing:
  ```bash
  rm -rf .ship/cache/<kind>/<id>@<v>
  shipctl sync --only <kind>:<id>
  ```
- **Verify:** `shipctl verify --check cache-integrity` returns `pass`.

#### `gitignore-cache` warns ".ship/cache/ not listed"

- **Symptom:** `[warn] gitignore-cache  .ship/cache/ not listed in .gitignore — add it to avoid committing cached bodies`.
- **Likely cause:** init normally appends `.ship/cache/`, but the file may have been pruned or this repo was set up before init had the helper (`cli/lib/verify/checks/gitignore-cache.mjs`).
- **Fix:** append `.ship/cache/` to `.gitignore`, or set `cache.vcs_tracked: true` in `.ship/config.yml` if you intentionally want the cache committed (the inverse warning fires if both are set).
- **Verify:** rerun the check; `pass` once the line is present (or `vcs_tracked=true` is acknowledged).

#### `bootstrap-files` fails with "missing"

- **Symptom:** `[fail] bootstrap-files  .github/workflows/ship-pilot.yml: missing` (and similar for `.ship/labels.yml`, `.env.example`).
- **Likely cause:** the `mobile-app + gh-actions + linear` triple is declared in `.ship/config.yml` but the bootstrap scaffolding hasn't been rendered (`cli/lib/verify/checks/bootstrap-files.mjs`).
- **Fix:** `shipctl init --bootstrap`. For other combos, the check `skip`s and you scaffold by hand (see [Operating → Bootstrap](/docs/operating)).
- **Verify:** all three target files contain the `ship-managed` marker the check looks for.

#### `ci-secrets` warns about a missing secret

- **Symptom:** `[warn] ci-secrets  secrets referenced in workflows but missing from .env.example: <NAME>`.
- **Likely cause:** a `${{ secrets.NAME }}` reference in `.github/workflows/*.yml` is not declared in `.env.example` (`cli/lib/verify/checks/ci-secrets.mjs`; `GITHUB_TOKEN` is excluded).
- **Fix:** add `NAME=` (no value) to `.env.example` so reviewers know the workflow needs it; configure the actual value in your CI provider's secret store, never in `.ship/config.yml`.
- **Verify:** rerun the check; `pass` reports `all N referenced secret(s) declared`.

#### `api-reachable` fails

- **Symptom:** `[fail] api-reachable  <baseUrl> unreachable: <status / network error>`.
- **Likely cause:** `api.base_url` (or `SHIP_API_BASE`) is wrong, the host is down, or you're behind a proxy. The check tries `/health` and then `/patterns` (`cli/lib/verify/checks/api-reachable.mjs`).
- **Fix:** confirm `shipctl config get api.base_url`, set `HTTPS_PROXY` if needed, or run `shipctl verify --no-network` while offline.
- **Verify:** the check returns `pass: <baseUrl>/health → 200`.

#### `tracker-labels` warns about missing Linear labels

- **Symptom:** `[warn] tracker-labels  missing labels on Linear: <label1>, <label2>` (when `LINEAR_API_KEY` is set; otherwise the check `skip`s).
- **Likely cause:** `.ship/labels.yml` declares labels the workspace doesn't have yet (`cli/lib/verify/checks/tracker-labels.mjs`).
- **Fix:** create the labels in Linear, or remove them from `.ship/labels.yml` if you stopped using them.
- **Verify:** rerun with `LINEAR_API_KEY` exported; `pass` once the sets agree.

## `shipctl doctor`

#### Doctor doesn't list an agent I do use

- **Symptom:** "Agents:" line is empty or missing your agent.
- **Likely cause:** the agent's marker file/dir isn't where the adapter looks (e.g. `.cursor/`, `AGENTS.md`, `.claude/`); the detector requires confidence ≥ 0.5 (`inferStack` in `cli/lib/commands/doctor.mjs`).
- **Fix:** create the expected file (the table in [/cli](/cli) lists the marker per agent), or declare the agent explicitly in `stack.agents` so verify still recognises it.
- **Verify:** `shipctl doctor` lists the agent with confidence ≥ 0.5; `shipctl verify --check agents-on-disk` returns `pass`.

#### Doctor's preset guess is wrong

- **Symptom:** `Inferred preset: <wrong>` when the repo is something else.
- **Likely cause:** the heuristic order in `inferPreset` matches a high-priority signal first (e.g. `pubspec.yaml` → `mobile-app` even in a hybrid repo).
- **Fix:** declare the preset in `.ship/config.yml` — `shipctl config set stack.preset <correct>`. Doctor's "config wins" rule means future runs won't fight you (`reconcileStack` in `cli/lib/commands/doctor.mjs`).
- **Verify:** `shipctl doctor` shows `Preset: <correct> (config) [disk inferred: <wrong>]`.

#### Doctor crashes with "unknown argument"

- **Symptom:** `doctor: unknown argument: <flag>` and a stack trace.
- **Likely cause:** doctor's arg parser is strict; only `--cwd`, `--write-inventory`, `--json`, `--no-network`, `--help` are accepted.
- **Fix:** drop the unknown flag — doctor never makes network calls in v1, so `--no-network` is a no-op kept for forward compatibility.
- **Verify:** `shipctl doctor --help` shows the closed flag list.

#### Doctor and verify disagree about my agents

- **Symptom:** doctor lists an agent that verify reports as missing (or vice versa).
- **Likely cause:** doctor unions `config.stack.agents` with disk signals (so a config-declared agent shows up "for free"); verify only inspects on-disk install targets.
- **Fix:** install rules with `shipctl init --copy-rules --agents <agent>` so the install_target file exists. Or remove the agent from config if it isn't really there.
- **Verify:** `shipctl verify --check agents-on-disk` and `--check rules-markers` both `pass`.

## `shipctl <kind> show / list`

#### `show` exits 1 with "Unknown id"

- **Symptom:** `Unknown id: <id>` (disk mode) or `HTTP 404 Not Found for <baseUrl>/<plural>/<id>` (hosted mode).
- **Likely cause:** the id you typed isn't on the manifest for that kind. List entries are scanned from `artifacts/<plural>/` on disk or from `GET /<plural>` over HTTP (`cli/lib/commands/manifest-catalog.mjs`).
- **Fix:** `shipctl <kind> list` to discover real ids; check spelling (artifact ids are kebab-case).
- **Verify:** `shipctl <kind> show <correct-id>` prints front-matter + body.

#### `fetch` says "artifact not found" for a version that exists

- **Symptom:** `artifact not found: <kind>:<id>@<version>` from `fetchArtifact` (`cli/lib/http.mjs`).
- **Likely cause:** the `<version>` doesn't exist on the channel you're configured for; only `stable` is listed by default — `edge`-only versions need an explicit channel.
- **Fix:** drop `--version` to get latest, or `shipctl sync --channel edge` and retry.
- **Verify:** `shipctl <kind> list --json | rg <id>` shows the exact version string you expect.

#### `show` returns a body that looks unrendered (front-matter visible)

- **Symptom:** the output starts with `---\nartifact_kind: ...\n---` and the rest is raw markdown.
- **Likely cause:** this is normal — `show` prints the raw artifact body so agents can parse the front-matter (`spec.install_target`, etc.). It is not cache poisoning.
- **Fix:** none. Use a markdown viewer (`glow`, `bat -l md`) for nicer rendering, or `shipctl <kind> show <id> --json` and pipe through `jq -r .content`.
- **Verify:** `shipctl verify --check cache-integrity` returns `pass` — the cache is fine.

## Telemetry

#### Outbox grows but never drains

- **Symptom:** `shipctl telemetry status` reports `outbox_pending=N` but `last_flush_at=never`.
- **Likely cause:** `telemetry.share=false` so flush no-ops with `telemetry disabled; nothing to send` (`cli/lib/commands/telemetry.mjs`); or `flush` exits 20 silently in CI because the API is unreachable. Events are appended any time `appendEvent` is called by code that *was* enabled at write time.
- **Fix:** `shipctl telemetry on --yes` if you want to send, or `shipctl telemetry off` and delete the file (`rm .ship/telemetry-outbox.jsonl`) if you don't.
- **Verify:** after `shipctl telemetry flush`, `outbox_pending=0` and `last_flush_at` is updated.

#### Flush exits 20 with no obvious error

- **Symptom:** `flushed 0 events, N failed` and exit 20.
- **Likely cause:** every batch POST to `<baseUrl>/telemetry` failed (network or 4xx). The CLI rewrites the outbox with the un-sent batch so retries are safe.
- **Fix:** confirm `api-reachable` passes; check `SHIP_API_BASE`; retry. If you need to discard the queue, `rm .ship/telemetry-outbox.jsonl`.
- **Verify:** next `shipctl telemetry flush` reports `flushed N events, 0 failed`.

#### A field I want to send is being stripped

- **Symptom:** `appendEvent` silently drops payload keys; under `SHIP_DEBUG=1` you see `[ship:telemetry] stripped denylisted keys from <type>: <key>`.
- **Likely cause:** the key is in the RFC-0003 denylist (`path, code, diff, branch, remote, email`) — `cli/lib/telemetry/outbox.mjs:DENYLIST_KEYS`.
- **Fix:** rename the field to something outside the denylist, or fold it into a non-sensitive aggregate (e.g. `path → path_kind`). The denylist is intentional and not configurable.
- **Verify:** `SHIP_DEBUG=1 shipctl telemetry buffer --limit 1` shows the event without the denied key.

## Feedback

#### `feedback submit` exits 1 with "missing required fields"

- **Symptom:** `missing required fields: kind, id, title, summary` or any subset.
- **Likely cause:** the draft front-matter is missing `kind`, `id`, or `title`; or the body has no `**Summary**:` line. `cmdSubmit` validates against both (`cli/lib/commands/feedback.mjs`).
- **Fix:** `shipctl feedback edit <draft>` and add the missing field(s) — front-matter for `kind/id/title`, body for `**Summary**:` and `**Recommendation**:`.
- **Verify:** rerun `shipctl feedback submit`; the response prints an `issue_url`.

#### Submit returns 4xx from `/feedback`

- **Symptom:** `HTTP 4xx <status> for <baseUrl>/feedback` and exit 20; the draft is *not* moved to `sent/`.
- **Likely cause:** the server rejected the payload (oversize, schema mismatch, deduped). Sometimes the response body says `deduplicated: true` instead of erroring, in which case the CLI prints `(deduplicated: comment added to existing issue)` and exit 0.
- **Fix:** read the server message (printed verbatim by `HttpError`); shorten the title/summary or check that `--kind` is one of `pattern, tool, workflow, collection, doc`.
- **Verify:** rerun submit; on success the draft file moves to `.ship/feedback-drafts/sent/`.

#### Drafts pile up in `feedback-drafts/` and never get sent

- **Symptom:** `shipctl feedback list` shows old drafts without `[sent]`.
- **Likely cause:** `feedback submit` is a deliberate, per-draft action — there is no auto-flush. Drafts persist until you submit or `feedback remove` them.
- **Fix:** triage with `shipctl feedback list`, then `shipctl feedback submit <path> --yes` for keepers and `shipctl feedback remove <path>` for the rest.
- **Verify:** the directory only contains drafts you actually intend to send; sent ones live under `sent/`.

## Cloud agents

#### Cursor Cloud secrets are not picked up

- **Symptom:** the cloud agent boots but `shipctl sync` fails with 401/403, or the agent says it cannot reach private mirrors.
- **Likely cause:** Cursor Cloud reads secrets from the environment of the cloud machine, not from your local `.env`. `SHIP_API_TOKEN` (and any tracker key like `LINEAR_API_KEY`) must be set in the Cursor Cloud secrets pane.
- **Fix:** add the env vars in Cursor Cloud → Environment → Secrets; ensure `.cursor/environments.json`'s `install` list runs `shipctl sync` *after* `npm i -g @ship/shipctl` (see `artifacts/collections/agent-rules-cursor-cloud/ARTIFACT.md`).
- **Verify:** in the cloud agent log, `shipctl verify --check api-reachable` returns `pass` on the first task.

#### Cloud setup script ran "successfully" but the agent has no rules

- **Symptom:** the cloud agent answers without any Ship context; `.cursor/rules/ship-artifacts-protocol.mdc` is missing inside the container.
- **Likely cause:** the Dockerfile (or build step) ran `shipctl init --copy-rules` *before* `COPY artifacts/ ./artifacts` landed, so the local artifact tree wasn't readable yet. In hosted mode this also means the install step ran before `shipctl sync` could populate `.ship/cache/`.
- **Fix:** order the build steps so artifacts are present *first*. Use the order in the repo's `Dockerfile` (`COPY landing` → `COPY documentation` → `COPY artifacts` → then any `shipctl` step). For Cursor Cloud `install` arrays, put `shipctl sync` after `npm i -g @ship/shipctl` and before any agent task.
- **Verify:** in the running container, `ls .cursor/rules/ship-artifacts-protocol.mdc` and `shipctl verify --no-network` both succeed.

#### Cloud agent runs the wrong preset

- **Symptom:** the agent talks about `mobile-app` workflow gates in a `web-app` repo (or vice versa).
- **Likely cause:** the repo committed a `.ship/config.yml` whose `stack.preset` doesn't match the runtime; or no `.ship/config.yml` exists and doctor's heuristic guessed wrong.
- **Fix:** commit a correct `.ship/config.yml` (`shipctl config set stack.preset <correct>` then commit). Cloud agents must read config from the checkout, not infer per-task.
- **Verify:** `shipctl doctor` inside the cloud env reports `Preset: <correct> (config)`.

#### Cloud agent installed shipctl but commands aren't found

- **Symptom:** `command not found: shipctl` after `npm i -g @ship/shipctl` in the install step.
- **Likely cause:** the cloud sandbox's npm prefix isn't on the agent's `PATH`, or you used the legacy package name (current name is `@elmundi/ship-cli`, binary `shipctl`).
- **Fix:** install with `npm i -g @elmundi/ship-cli`; if `PATH` is the issue, invoke `npx @elmundi/ship-cli` from your install script instead of relying on global resolution.
- **Verify:** `shipctl --version` (or `npx @elmundi/ship-cli --version`) prints a version string.

## Environment

#### `command not found: shipctl`

- **Symptom:** the shell can't find `shipctl` after install.
- **Likely cause:** the global npm prefix isn't on `PATH`, or you ran `npx` once (which doesn't install globally).
- **Fix:** `npm i -g @elmundi/ship-cli` and ensure `$(npm prefix -g)/bin` is in `PATH`. For one-off use without global install, `npx @elmundi/ship-cli <cmd>`.
- **Verify:** `which shipctl` prints a path; `shipctl --version` prints a version.

#### `shipctl --version` reports an unexpected version

- **Symptom:** the version printed doesn't match the latest you just installed (or differs between projects).
- **Likely cause:** you have multiple installations — a local `node_modules/.bin/shipctl` (project install) shadows the global one; or `npx` is resolving from cache.
- **Fix:** `which -a shipctl` to see all candidates. Either pin a project-local install (`npm i -D @elmundi/ship-cli` and call via `npx shipctl`) or remove the local copy. `npx --no-install @elmundi/ship-cli --version` shows what npx would use.
- **Verify:** `shipctl --version` matches `npm view @elmundi/ship-cli version` (latest) or your pinned dependency.

#### Node version too old

- **Symptom:** `shipctl` exits with `SyntaxError: Unexpected token '?'` or an `node:fs/promises` import error on startup.
- **Likely cause:** the CLI requires Node 20+ (`cli/README.md → Requirements`). The package uses ESM and modern syntax that older Node versions reject.
- **Fix:** install Node 20 LTS or newer (e.g. `nvm install 20 && nvm use 20`).
- **Verify:** `node --version` is `v20.x` or higher; `shipctl --version` runs without parse errors.

## Where to next

If a recipe (not a failure) is what you actually need — pinning, channel switching, telemetry opt-in, bootstrap walkthroughs — read [Operating](/docs/operating). For field-level config detail go to [Configuration](/docs/configuration). The exact command/flag surface is documented in [/cli](/cli), and the normative behaviour behind every error in this page traces back to the [Protocol RFCs](/docs/protocol) — RFC-0001 (artifacts), RFC-0002 (config), RFC-0003 (telemetry & feedback), RFC-0005 (artifact folders).
