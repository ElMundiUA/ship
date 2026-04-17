# Pharma Mobile Pilot — End-to-End Smoke Test Log

> **Update (2026-04-18, post-fix rerun):** A single `shipctl new` now materializes
> the entire project AND `shipctl verify --no-network` exits **0 (7 pass, 4 skip)**
> on the freshly produced repo. See the "Post-fix rerun" section at the bottom of
> this log. All bugs A / B / C / D / E / F are fixed; G (monorepo manifest drift)
> is resolved via `tools/tracker-contract 1.0.0 → 1.1.0`.

- **Date:** 2026-04-18 (Sat)
- **Operator:** Cursor agent (smoke run)
- **Stack:** preset=`mobile-app`, tracker=`linear`, ci=`gh-actions`,
  agents=`cursor,claude-md,codex`, language=`ts`, channel=`stable`
- **Backend:** local FastAPI on `127.0.0.1:8100`
  (`. .venv/bin/activate && uvicorn backend.app.main:app --host 127.0.0.1 --port 8100`)
- **Demo repo:** `/tmp/pharma-pilot-demo`
- **CLI:** `node cli/bin/shipctl.mjs` from `/Users/denyskuzin/Projects/ship`

> Goal: prove the Ship stack works end-to-end. Findings/bugs are recorded; no
> code in `cli/` / `backend/` / etc. was modified during this run.

---

## Task 1 — start backend on :8100

```text
INFO:     Uvicorn running on http://127.0.0.1:8100 (Press CTRL+C to quit)
$ curl -sS http://127.0.0.1:8100/manifest | head -c 120
{"version":1,"generated_at":"2026-04-17T21:53:34.552294+00:00","entries":[{"kind":"pattern","id":"adopt-ship-generic", ...
```

Backend was already running cleanly on first probe (no port conflict).

## Task 2 — `shipctl new`

### First attempt (clean run, no env)

```text
$ rm -rf /tmp/pharma-pilot-demo
$ node cli/bin/shipctl.mjs new /tmp/pharma-pilot-demo \
    --preset mobile-app --tracker linear --ci gh-actions \
    --agents cursor,claude-md,codex --language ts --channel stable \
    --yes --base-url http://127.0.0.1:8100
warn: artifact fetch partially failed (HTTP 503 Service Unavailable for
 https://ship.elmundi.com/api/methodology/manifest?channel=stable
{"error":"Methodology proxy is not configured. ..."})
Ship init complete
...
```

**Bug A — `shipctl new` does not propagate `--base-url` to the spawned `init`
subprocess.** After the run, only `.gitignore`, `README.md`, `.ship/config.yml`,
`.ship/state.json`, `.ship/cache/.gitkeep` existed. The agent rules and preset
artifacts never landed because `init` defaulted to the production URL
(`ship.elmundi.com`), which is not configured locally → 503. See
`cli/lib/commands/new.mjs:388-397` (`buildInitArgv`) — it forwards
`--tracker/--ci/--preset/--copy-rules/--force` but **omits `--base-url`,
`--language`, `--channel`, `--agents-... env`**, etc.

### Second attempt (workaround: `SHIP_API_BASE` env)

```text
$ SHIP_API_BASE=http://127.0.0.1:8100 node cli/bin/shipctl.mjs new /tmp/pharma-pilot-demo \
    --preset mobile-app --tracker linear --ci gh-actions \
    --agents cursor,claude-md,codex --language ts --channel stable \
    --yes --base-url http://127.0.0.1:8100
created: /tmp/pharma-pilot-demo/.ship/config.yml
created: /tmp/pharma-pilot-demo/.ship/state.json
created: /tmp/pharma-pilot-demo/.ship/cache/
updated: /tmp/pharma-pilot-demo/.gitignore
config set stack.tracker=linear
config set stack.ci=gh-actions
config set stack.preset=mobile-app
config set stack.language=ts
config set api.channel=stable
config set stack.agents=[cursor,claude-md,codex]
config set telemetry.share=false
Ship init complete
Sync: up_to_date=0 updated=4 failed=0
Done. Ship scaffolding in /tmp/pharma-pilot-demo
```

Cache after this run:

```text
.ship/cache/collection/agent-rules-claude-md@1.0.0.md
.ship/cache/collection/agent-rules-codex@1.0.0.md
.ship/cache/collection/agent-rules-cursor@1.0.0.md
.ship/cache/collection/preset-mobile-app@1.0.0.md
(+ matching .meta.json sidecars)
```

But on-disk render targets were **still missing**: `.cursor/rules/...`,
`CLAUDE.md`, `AGENTS.md`, `.github/workflows/ship-pilot.yml`, `.ship/labels.yml`,
`.env.example`, `SHIP_BOOTSTRAP_PLAN.md`.

**Bug B — `shipctl new` never enables `--copy-rules` or `--bootstrap`** when it
spawns `init`. `cli/lib/commands/new.mjs:388-397` only adds `--copy-rules`
when the user explicitly passes `--copy-rules` to `new`, and there is no way to
ask `new` for `--bootstrap`. Without those, `new` produces an unusable repo
(no rule files, no CI workflow, no labels file, no `.env.example` block).

### Workaround — re-run `init` with the missing flags

```text
$ SHIP_API_BASE=http://127.0.0.1:8100 node cli/bin/shipctl.mjs init \
    --cwd /tmp/pharma-pilot-demo \
    --agents cursor,claude-md,codex \
    --tracker linear --ci gh-actions --preset mobile-app \
    --copy-rules --bootstrap --yes \
    --base-url http://127.0.0.1:8100
Installed rules:
  - wrote .cursor/rules/ship-artifacts-protocol.mdc (from collection/agent-rules-cursor@1.0.0)
  - wrote CLAUDE.md (from collection/agent-rules-claude-md@1.0.0)
  - wrote AGENTS.md (from collection/agent-rules-codex@1.0.0)
Bootstrap (preset=mobile-app):
  - wrote: SHIP_BOOTSTRAP_PLAN.md
  - wrote: .github/workflows/ship-pilot.yml
  - wrote: .ship/labels.yml
  - appended: .env.example
Sync: up_to_date=4 updated=0 failed=0
```

### Acceptance checks (after the workaround)

| Expected file | Present? | Notes |
|---|---|---|
| `.git/` | yes | created by `git init -q` |
| `.ship/config.yml` | yes | `shipctl config validate` → `ok` |
| `.ship/cache/collection/agent-rules-cursor@1.0.0.md` | yes | sha verified |
| `.ship/cache/collection/agent-rules-claude-md@1.0.0.md` | yes | sha verified |
| `.ship/cache/collection/agent-rules-codex@1.0.0.md` | yes | sha verified |
| `.ship/cache/collection/preset-mobile-app@1.0.0.md` | yes | sha verified |
| `.cursor/rules/ship-artifacts-protocol.mdc` | yes | contains `<!-- ship-cli: artifacts-protocol v1 -->` and footer `<!-- ship-cli: installed-from collection/agent-rules-cursor@1.0.0 -->` |
| `CLAUDE.md` | yes | marker + footer `…/agent-rules-claude-md@1.0.0` |
| `AGENTS.md` | yes | marker + footer `…/agent-rules-codex@1.0.0` |
| `.github/workflows/ship-pilot.yml` | yes | rendered from preset |
| `.ship/labels.yml` | yes | mobile-app preset labels |
| `.env.example` | yes | contains `# --- ship-managed ---` block with `LINEAR_API_KEY`, `EXPO_TOKEN`, etc. |
| `SHIP_BOOTSTRAP_PLAN.md` | yes | rendered |
| `.gitignore` includes `.ship/cache/` | yes | also `.ship/state.json`, `feedback-drafts/`, `telemetry-outbox.jsonl` |

## Task 3 — pharma addendum

`shipctl collection fetch addendum-pharma --base-url http://127.0.0.1:8100`
**printed** the addendum but did **not** persist it to `.ship/cache/collection/`.

**Bug C — `shipctl collection fetch <id>` is read-only.** It calls
`POST /fetch` and dumps the body to stdout; nothing is written to the on-disk
cache (`cli/lib/commands/manifest-catalog.mjs:108-122`). Workaround:

```text
$ node cli/bin/shipctl.mjs sync --cwd /tmp/pharma-pilot-demo \
    --base-url http://127.0.0.1:8100 --only collection:addendum-pharma
up_to_date: 0  updated: 1  failed: 0
$ ls .ship/cache/collection
addendum-pharma@1.0.0.md   addendum-pharma@1.0.0.meta.json
agent-rules-claude-md@1.0.0.md  ...
preset-mobile-app@1.0.0.md ...
```

Then created the placeholder opt-in (TODO for shipctl: provide
`shipctl addendum add <id>`):

```text
$ printf -- "- addendum-pharma\n" > /tmp/pharma-pilot-demo/.ship/addendums.yml
```

## Task 4 — `shipctl doctor`

```text
$ node cli/bin/shipctl.mjs doctor --cwd /tmp/pharma-pilot-demo
Ship doctor — inspecting /tmp/pharma-pilot-demo

Tracker:     linear (0.70) · evidence: .env.example (LINEAR_API_KEY)
             github-issues (0.30)
             none (0.05)
CI:          gh-actions (1.00) · evidence: .github/workflows/ (1 workflow(s))
             manual (0.05)
Language:    none detected
Agents:      agents-md (1.00), claude-md (1.00), cursor (1.00)

Inferred preset:  adoption-minimum (evidence: no strong preset signals)

Recommendations:
  1. shipctl init --bootstrap --tracker linear --ci gh-actions --agents agents-md,claude-md,cursor --preset adoption-minimum
```

**Bug D — doctor ignores `.ship/config.yml`.** It re-detects from filesystem
signals only and:

- mis-identifies the codex agent: `AGENTS.md` is reported as `agents-md`
  (config has `codex`).
- reports `Language: none detected` even though `stack.language: ts` is in the
  config (a TS-only repo with no `package.json` looks empty to the detector).
- infers `adoption-minimum` for the preset, with no signal that the configured
  preset is `mobile-app`.

The recommendation block then suggests rewriting the stack with the wrong
agents/preset. Doctor needs to read `.ship/config.yml` first and present it
alongside (or instead of) the heuristics.

## Task 5 — `shipctl verify --no-network`

```text
[pass] config-present        .ship/config.yml parsed; schema v1
[pass] gitignore-cache       .ship/cache/ listed in .gitignore
[pass] stack-enums           tracker=linear, ci=gh-actions, preset=mobile-app, language=ts, agents=[cursor,claude-md,codex]
[fail] rules-markers         codex: missing rule file .codex/SHIP_API.md
[pass] cache-integrity       5 cached entries verified (sha256 ok)
[pass] bootstrap-files       3 bootstrap files carry ship-managed markers
[warn] agents-on-disk        no on-disk signal for declared agents: codex
[skip] api-reachable         skipped (--no-network)
[skip] artifacts-up-to-date  skipped (--no-network)
[skip] tracker-labels        skipped (--no-network)
[skip] ci-secrets            skipped (--no-network)

11 checks total: 5 pass, 1 warn, 1 fail, 4 skip
Exit code: 1 (any fail)
```

**Bug E — codex install target is inconsistent.** The renderer wrote
`AGENTS.md` (using `agent-rules-codex@1.0.0`); but `verify`'s
`rules-markers` check expects `.codex/SHIP_API.md`. So a fresh, "by-the-book"
install always fails this check. Either the codex agent should write to
`.codex/SHIP_API.md` (matching verify), or verify should accept `AGENTS.md` for
codex. Same root cause as bug D's "agents-md vs codex" confusion.

The `agents-on-disk` warning has the same origin (no `.codex/` dir present).

## Task 6 — sync cycle + drift test

### Up-to-date check

```text
$ node cli/bin/shipctl.mjs sync --check-only --cwd /tmp/pharma-pilot-demo \
    --base-url http://127.0.0.1:8100
up_to_date: 5  updated: 0  failed: 0
```

### Drift detection

```text
$ printf "\n" >> .ship/cache/collection/agent-rules-cursor@1.0.0.md
$ shipctl verify --no-network
[fail] cache-integrity       1/5 cached entries tampered: collection/agent-rules-cursor@1.0.0
```

**Bug F — sync won't re-fetch on drift / missing files.** Reverting via
`mv ...bak orig` left `verify` still failing because state/meta carried the
expected sha. Even after `rm` of the `.md` file, `sync ... --only ...` reported
`up_to_date: 1` and did **not** re-download. The cache was finally rebuilt only
after also deleting the `.meta.json` sidecar. Sync should treat missing `.md`
and sha mismatch as triggers, not just stale meta.

After the manual fix:

```text
[pass] cache-integrity       5 cached entries verified (sha256 ok)
```

## Task 7 — telemetry status

```text
share=false
anonymous_id=911a4f4e-29f4-4cc4-a2f5-f36d9b645cea
scope=artifact_usage=true,improvement_drafts=true,errors=false
outbox_pending=0
last_flush_at=never
```

`share=false` as expected — telemetry is opt-in.

## Task 8 — feedback draft

```text
$ shipctl feedback draft --kind collection --id preset-mobile-app --version 1.0.0 \
    --title "Add a sample offline-sync section" --summary ... --recommendation ...
/tmp/pharma-pilot-demo/.ship/feedback-drafts/2026-04-17-21-57-12-collection-preset-mobile-app.md

$ shipctl feedback list
2026-04-17-21-57-12 collection/preset-mobile-app@1.0.0 — Add a sample offline-sync section
```

`feedback submit` not exercised (no GitHub token).

## Task 9 — artifact check on the Ship monorepo

```text
$ python3 scripts/ship_artifact_check.py
FAIL: tool:tracker-contract content changed but manifest version still 1.0.0 (expected bump).
  stored_sha=4c2d76ea3d102d6996b249feda28d3b6e2442ca512a372ad28b0838b0ce754d4
  actual_sha=144e43747039b7cb1038d0b31649d5c9762fd1ce4e07a60feb2c98fd7650f11a
1 drift(s) detected across 61 checked entries.
```

**Bug G (pre-existing, monorepo-side).** `tools/tracker-contract` has been
edited without bumping its manifest version. Expected exit 0 with
"OK: 61 manifest entries checked", got exit 1. This is independent of the
demo repo; the manifest entry needs to be re-stamped (or the file reverted).
Filed here as a finding so it isn't lost.

## Task 10 — backend stop

```text
$ kill 55795        # parent
$ kill 55802        # child uvicorn worker
$ lsof -i :8100     # silent → port free
```

Backend cleanly stopped.

---

## Findings — what worked

- Backend served `/manifest`, `/collections/...`, `/fetch` correctly.
- `shipctl config init` + `config set` + `config validate` round-trip is solid.
- `shipctl init --copy-rules --bootstrap` (run manually) produced every
  expected file with proper markers and footers (`<!-- ship-cli:
  artifacts-protocol v1 -->`, `<!-- ship-cli: installed-from ... -->`).
- `verify --no-network` runs all 11 checks and gates correctly on
  `cache-integrity` drift.
- `sync --only collection:<id>` is a working escape hatch when bulk sync
  doesn't pick up an artifact.
- `telemetry status` and `feedback draft`/`feedback list` work end-to-end with
  no surprises.

## Findings — required workarounds

1. Set `SHIP_API_BASE=http://127.0.0.1:8100` in the shell before
   `shipctl new`, because `--base-url` isn't propagated.
2. Run `shipctl init --copy-rules --bootstrap` after `shipctl new` to actually
   render rules + CI/labels/.env files.
3. Use `shipctl sync --only collection:addendum-pharma` to cache an addendum
   (not `shipctl collection fetch`).
4. Hand-write `.ship/addendums.yml` to record the addendum opt-in.

## Findings — unresolved bugs / mismatches

| # | Severity | Location | Problem |
|---|---|---|---|
| A | high | `cli/lib/commands/new.mjs:388-397` (`buildInitArgv`) | `--base-url`, `--channel`, `--language`, `--telemetry`, `--bootstrap` not forwarded to spawned `init`. |
| B | high | `cli/lib/commands/new.mjs:388-397` | `new` never enables `--copy-rules` or `--bootstrap` by default → unusable scaffold. |
| C | medium | `cli/lib/commands/manifest-catalog.mjs:108-122` | `collection fetch <id>` only prints; doesn't cache. Same likely for `tool fetch` / `workflow fetch`. |
| D | high | `cli/lib/commands/doctor.mjs` (whole detector) | Doctor ignores `.ship/config.yml`; mis-detects codex as agents-md, can't see `language=ts`, infers wrong preset, then recommends overwriting stack. |
| E | high | `cli/lib/verify/checks/rules-markers.mjs` vs `.../agent-rules-codex` collection | `rules-markers` expects `.codex/SHIP_API.md`; renderer writes `AGENTS.md`. A green-field install always fails verify. |
| F | medium | `cli/lib/commands/sync.mjs` (decide-update logic) | `sync` decides update from `.meta.json` only. If the `.md` is missing or its sha drifts, sync reports `up_to_date` and skips re-download. |
| G | low (pre-existing) | `tools/tracker-contract` (root manifest entry) | Content changed without a version bump; `scripts/ship_artifact_check.py` exits 1 on the monorepo. |

Bug E + the codex/agents-md confusion in D point at a deeper schema gap:
nothing pins the codex agent to a single canonical install target.

---

## Next actions (suggested ticket queue)

1. **`shipctl new` → forward all relevant flags** (and default to
   `--copy-rules --bootstrap`). Add a regression test that an isolated
   `shipctl new --base-url <local> --yes ...` followed by `shipctl verify`
   gives a green run on the freshly produced repo.
2. **Doctor: read `.ship/config.yml` first.** Detection should be a
   reconciliation between config and on-disk evidence, with a clear
   "config says X / disk shows Y" diff. Don't recommend rewrites that
   contradict an existing config.
3. **Codex install path: pick one.** Either teach the codex collection to
   write `.codex/SHIP_API.md` (and stop writing `AGENTS.md`) or update
   `rules-markers` and `agents-on-disk` to accept `AGENTS.md` for codex.
   Then make `agent-rules-agents-md` a *separate* opt-in.
4. **`shipctl collection fetch <id> --cache`** (or change default to write to
   cache). Today the only way to cache an addendum is `sync --only ...`,
   which is non-obvious.
5. **`shipctl addendum add <id>` / `addendum remove <id>`** that manages
   `.ship/addendums.yml` instead of users hand-editing it.
6. **`shipctl sync` re-fetch triggers.** Treat missing `.md`, mismatched
   sha, or missing `.meta.json` as a re-download trigger when the manifest
   has the entry. Today only stale meta wins.
7. **Stamp `tools/tracker-contract` version bump** (or revert the content
   change) so `scripts/ship_artifact_check.py` returns 0 on the monorepo.
8. **Backend manifest proxy is misconfigured for `ship.elmundi.com`.**
   The 503 says `SHIP_METHODOLOGY_UPSTREAM_URL` is unset on the deployed API.
   File a deploy-config ticket so production users don't hit the same issue.

---

## End-state summary

- Demo repo: `/tmp/pharma-pilot-demo` (kept, 5 cached collections + addendum).
- `verify` exit code: **1** (cache-integrity green after the drift test, but
  `rules-markers` for codex still fails — see Bug E).
- `sync --check-only`: **`up_to_date: 5, updated: 0, failed: 0`** ✅
- `feedback draft`: created `2026-04-17-21-57-12-collection-preset-mobile-app.md`.
- `telemetry status`: `share=false`.
- All bootstrap files materialised after the manual `init --copy-rules
  --bootstrap` workaround; **none** of them landed from `shipctl new` alone.
- Backend (uvicorn on :8100) was stopped; `lsof -i :8100` returns empty.

---

## Post-fix rerun (2026-04-18, 22:14 UTC)

After landing the CLI fixes (see git log for `new.mjs` / `doctor.mjs` /
`sync.mjs` / `manifest-catalog.mjs` / `verify/*` / `cache/store.mjs`):

```text
$ . .venv/bin/activate && uvicorn backend.app.main:app --host 127.0.0.1 --port 8100 &
$ curl -sS http://127.0.0.1:8100/manifest | head -c 80
{"version":1,"generated_at":"2026-04-17T22:14:38.905375+00:00", ...

$ rm -rf /tmp/pharma-pilot-demo
$ node cli/bin/shipctl.mjs new /tmp/pharma-pilot-demo \
    --preset mobile-app --tracker linear --ci gh-actions \
    --agents cursor,claude-md,codex --language ts --channel stable \
    --yes --base-url http://127.0.0.1:8100

created: /tmp/pharma-pilot-demo/.ship/config.yml
config set stack.{tracker,ci,preset,language,agents} ...
Ship init complete
Installed rules:
  - wrote .cursor/rules/ship-artifacts-protocol.mdc (from collection/agent-rules-cursor@1.0.0)
  - wrote CLAUDE.md (from collection/agent-rules-claude-md@1.0.0)
  - wrote AGENTS.md (from collection/agent-rules-codex@1.0.0)
Bootstrap (preset=mobile-app):
  - wrote: SHIP_BOOTSTRAP_PLAN.md
  - wrote: .github/workflows/ship-pilot.yml
  - wrote: .ship/labels.yml
  - appended: .env.example
Sync: up_to_date=0 updated=4 failed=0
Done.
```

`shipctl verify --no-network` on the freshly produced repo:

```text
[pass] config-present        .ship/config.yml parsed; schema v1
[pass] gitignore-cache       .ship/cache/ listed in .gitignore
[pass] stack-enums           tracker=linear, ci=gh-actions, preset=mobile-app, language=ts, agents=[cursor,claude-md,codex]
[pass] rules-markers         all 3 agent rule files have correct markers
[pass] cache-integrity       4 cached entries verified (sha256 ok)
[pass] bootstrap-files       3 bootstrap files carry ship-managed markers
[pass] agents-on-disk        3 declared agent(s) have on-disk signals
[skip] api-reachable | artifacts-up-to-date | tracker-labels | ci-secrets (--no-network)

11 checks total: 7 pass, 0 warn, 0 fail, 4 skip
Exit code: 0
```

`shipctl doctor` now reconciles config with disk instead of recommending
a rewrite:

```text
Tracker:     linear (config) · disk: linear (0.70)
CI:          gh-actions (config) · disk: gh-actions (1.00)
Language:    ts (config) · disk: no signal
Agents:      declared: cursor, claude-md, codex
             disk: AGENTS.md (→ codex via config), claude-md, cursor
Preset:      mobile-app (config)

Recommendations:
  1. Config and disk agree. Run `shipctl verify`.
```

`shipctl collection fetch addendum-pharma` (run from the client cwd, so the
`manifestFromDisk` shortcut does not hijack it) writes to cache:

```text
$ cd /tmp/pharma-pilot-demo
$ shipctl collection fetch addendum-pharma --base-url http://127.0.0.1:8100
cached: collection/addendum-pharma@1.0.0 → .ship/cache/collection/addendum-pharma@1.0.0.md
```

Drift test — append a byte to a cached artifact, `shipctl sync` re-fetches
automatically:

```text
$ printf "\n" >> .ship/cache/collection/agent-rules-cursor@1.0.0.md
$ shipctl sync --base-url http://127.0.0.1:8100
up_to_date: 3  updated: 1  failed: 0
  - refetch: collection/agent-rules-cursor@1.0.0 (drifted)
$ shipctl verify --no-network
... 7 pass, 0 fail, 4 skip ; exit 0
```

`python3 scripts/ship_artifact_check.py` → `OK: 61 manifest entries checked`
(drift G closed via `tools/tracker-contract 1.0.0 → 1.1.0`).

### Fix summary

| Bug | Resolution |
|-----|------------|
| A | `shipctl new` forwards `--base-url`, `--channel`, `--language`, `--telemetry`; defaults `--copy-rules` + `--bootstrap` ON. |
| B | Same fix — `new` produces a complete scaffold in one command. |
| C | `shipctl {collection,tool,workflow} fetch <id>` writes to `.ship/cache/` when invoked from a Ship workspace; `--print` opt-in echoes body. |
| D | `shipctl doctor` reads `.ship/config.yml` and reconciles with disk; `AGENTS.md` maps to `codex` when config declares codex. |
| E | `verify.rules-markers` / `verify.agents-on-disk` read `install_target` from cached artifact front-matter. |
| F | `shipctl sync` re-checks physical file sha and re-fetches on missing or drifted body. |
| G | `tools/tracker-contract` bumped to 1.1.0 + re-stamped. |

CLI test suite: **75/75 passing** (14 new regression tests across the fix wave).

### Residual polish items

- `resolveShipRepoRootForCatalog()` short-circuits when invoked from inside
  the Ship monorepo checkout, so `collection fetch` bypasses cache writes
  there. End-users running from their own project directory are unaffected.
  Future polish: always honour `findShipRoot` first.
- Production 503 on `ship.elmundi.com/api/methodology/manifest`
  (`SHIP_METHODOLOGY_UPSTREAM_URL` unset) — deploy-config ticket; does not
  block local runs or the 1-command pharma-pilot flow.
- `shipctl addendum add/remove <id>` would replace manual editing of
  `.ship/addendums.yml`; tracked as a future affordance.
