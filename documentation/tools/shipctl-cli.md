# `shipctl` CLI quick reference

Authoritative quick reference for the `shipctl` command surface. Detailed
behavior lives in [`cli/README.md`](https://github.com/ElMundiUA/ship/blob/main/cli/README.md);
the protocol contracts live in the RFCs (RFC-0001 artifacts, RFC-0002
config, RFC-0003 telemetry, RFC-0004 adapters).

## Install

```bash
npm install -g @elmundi/ship-cli
# or one-off:
npx @elmundi/ship-cli help
```

The published package is **`@elmundi/ship-cli`** under the
[elmundi](https://www.npmjs.com/org/elmundi) org. The binary is **`shipctl`**.
The previous binary name **`ship`** still resolves and forwards every argument
to `shipctl`, but prints a one-line stderr deprecation warning. It will be
removed in `@elmundi/ship-cli@0.5`.

Requirements: Node.js 20+.

## Commands at a glance

| Command | What it does |
|---------|--------------|
| `shipctl help` | Print the inline help (commands + flags + supported agents). |
| `shipctl search <query>` | Vector search over the methodology corpus (`POST /search`). |
| `shipctl docs fetch <path>` | Fetch a documentation body by repo-relative path. |
| `shipctl docs feedback …` | Post a retro / improvement note to `POST /feedback`. |
| `shipctl pattern\|tool\|workflow\|collection list\|show\|fetch\|search` | Catalog operations for each artifact kind (RFC-0001). Plural aliases work. |
| `shipctl init` | Primary adoption entrypoint — see RFC-0001 / RFC-0002 / RFC-0004. |
| `shipctl new <name>` | Greenfield scaffolder: `git init` + `.ship/config.yml` + `init --copy-rules`. |
| `shipctl doctor` | Inspect the repo, propose a stack via adapter `detect()` hooks (RFC-0004). |
| `shipctl verify` | Post-adoption liveness checks (local + config + network). |
| `shipctl config` | Manage `.ship/config.yml` (RFC-0002). |
| `shipctl sync` | Reconcile local cache with the manifest (RFC-0001 § Sync). |
| `shipctl telemetry` | Anonymous, opt-in telemetry control (RFC-0003). |
| `shipctl feedback` | Local markdown feedback drafts → GitHub issue via `POST /feedback`. |

Run `shipctl help` at any time for the inline summary; the
[help.mjs](https://github.com/ElMundiUA/ship/blob/main/cli/lib/commands/help.mjs)
file is the authoritative source for the command surface.

## Per-command quick reference

### `shipctl init`

Adoption entrypoint. Composes config bootstrap, telemetry prompt, doctor
detection, and artifact sync; `--copy-rules` installs cached
`collection/agent-rules-<agent>` files at their `install_target`;
`--bootstrap` renders CI/tracker scaffolding when the preset triple is
supported (today: `mobile-app + gh-actions + linear`).

Most common flags: `--yes`, `--dry-run`, `--agents <csv>`, `--tracker`,
`--ci`, `--preset`, `--copy-rules`, `--bootstrap`, `--telemetry on|off|ask`.

```bash
shipctl init --yes \
  --agents cursor,codex,claude-md \
  --tracker linear --ci gh-actions --preset web-app \
  --copy-rules
```

### `shipctl new <name>`

Greenfield scaffolder. Creates `<name>/`, runs `git init`, drops a minimal
`README.md`, seeds `.ship/config.yml`, applies stack flags via
`shipctl config set`, and runs `init --copy-rules` for the listed agents.

```bash
shipctl new pharma-pilot \
  --preset mobile-app --tracker linear --ci gh-actions \
  --agents cursor,claude,codex --yes
```

`--here` initializes in the current directory instead of `<name>/`.

### `shipctl doctor`

Walks every adapter's `detect(cwd)` hook (RFC-0004) and proposes a stack
with confidence scores plus evidence. Pair with `--write-inventory` to
persist `.ship/inventory.json` for downstream `init --bootstrap`.

```bash
shipctl doctor                              # human report
shipctl doctor --json                       # machine-readable
shipctl doctor --write-inventory            # write .ship/inventory.json
```

### `shipctl verify`

Post-adoption liveness check. Independent checks under
`cli/lib/verify/checks/` cover local files, config schema, and network
reachability.

```bash
shipctl verify                              # full run
shipctl verify --no-network                 # skip HTTP / Linear / secret probes
shipctl verify --check rules-markers,cache-integrity
shipctl verify --severity warn              # hide pass rows
shipctl verify --json
```

### `shipctl config`

Atomic edits on `.ship/config.yml`; never edit the file by hand.

```bash
shipctl config init                         # bootstrap .ship/ tree
shipctl config show                         # pretty-print effective YAML
shipctl config get api.channel
shipctl config set api.channel edge
shipctl config set artifacts.pins.pattern/cloud-developer 1.4.2
shipctl config validate
```

See [RFC-0002](../rfc/rfc-0002-shipctl-config.md) for the schema and the
`stack.*` enum allowlists.

### `shipctl sync`

Reconciles the local `.ship/cache/` with the server manifest. Honours
`artifacts.pins`; never deletes a cached entry before its replacement is
verified (RFC-0001 § Sync).

```bash
shipctl sync                                # pull latest for this stack
shipctl sync --check-only                   # report changes only
shipctl sync --only pattern:cloud-developer
shipctl sync --channel edge
shipctl sync --force-unpin                  # one-shot ignore version pins
```

### `shipctl telemetry`

Opt-in, OFF by default. Events are appended to `.ship/telemetry-outbox.jsonl`
and flushed in batches via `POST /telemetry` (RFC-0003).

```bash
shipctl telemetry status
shipctl telemetry on --scope artifact_usage,improvement_drafts --yes
shipctl telemetry buffer --limit 10
shipctl telemetry flush
shipctl telemetry export --out telemetry.json
shipctl telemetry delete-my-data
shipctl telemetry reset-id
shipctl telemetry off
```

### `shipctl feedback`

Local markdown drafts that submit to `POST /feedback`. The server creates
a GitHub issue on the Ship repo and may dedupe against existing open issues
(RFC-0003 § Feedback dedup).

```bash
shipctl feedback draft --kind pattern --id cloud-developer --version 1.4.2 \
  --title "Missing mobile preview step" \
  --summary "Evidence checklist misses mobile preview"
shipctl feedback list
shipctl feedback show <draft>
shipctl feedback edit <draft>
shipctl feedback submit <draft> --yes
```

### `shipctl search`

Vector search across the methodology corpus.

```bash
shipctl search "release gates" --top-k 5
```

### `shipctl docs fetch | feedback`

Documentation file fetch by repo-relative path; retro feedback as markdown.

```bash
shipctl docs fetch documentation/adoption/agent-setup-contract.md
shipctl docs feedback --title "..." --summary "..." --recommendation "…"
```

### `shipctl pattern|tool|workflow|collection`

Same subcommand surface (`list`, `show`, `fetch`, `search`) for each
artifact kind. Plural aliases (`patterns`, `tools`, …) also work. When `cwd`
or `SHIP_REPO` is inside the Ship monorepo, `list` / `show` / `fetch` read
manifests from disk instead of HTTP; `search` always uses HTTP.

```bash
shipctl pattern list
shipctl pattern show cloud-developer
shipctl collection fetch agent-rules-cursor --version 1.0.0
```

## Global flags

| Flag | Purpose |
|------|---------|
| `--base-url <url>` | Methodology API base; defaults to `SHIP_API_BASE` or `https://ship.elmundi.com/api/methodology`. |
| `--json` | Machine-readable JSON output (where supported). |

## Environment

| Variable | Effect |
|----------|--------|
| `SHIP_API_BASE` | Overrides `api.base_url` from `.ship/config.yml`. |
| `SHIP_CHANNEL` | Overrides `api.channel` (`stable` / `edge`). |
| `SHIP_TTL_HOURS` | Overrides `api.ttl_hours`. |
| `SHIP_OFFLINE_OK` | Overrides `api.offline_ok`. |
| `SHIP_TELEMETRY` | Overrides `telemetry.share`. |
| `SHIP_REPO` | Path to a Ship monorepo checkout for disk-mode reads. |

## Where to read more

- Full CLI walkthrough: [`cli/README.md`](https://github.com/ElMundiUA/ship/blob/main/cli/README.md)
- Artifacts protocol: [RFC-0001](../rfc/rfc-0001-artifacts-protocol.md)
- Config schema: [RFC-0002](../rfc/rfc-0002-shipctl-config.md)
- Telemetry & feedback: [RFC-0003](../rfc/rfc-0003-telemetry-and-feedback.md)
- Adapters: [RFC-0004](../rfc/rfc-0004-adapters.md)
- Tracker adapters quick-reference: [Tracker adapters](ship-agent-trackers.md)
- CI adapters quick-reference: [CI adapters](ship-agent-ci.md)
- Backend HTTP surface: [Backend API](backend-api.md)
