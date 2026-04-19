# Getting started — ready-to-go

This is the operational entrypoint. Everything Ship does in your repo runs
through one CLI binary — **`shipctl`** — and one config file —
**`.ship/config.yml`**. There are three adoption paths; pick the one that
matches the repo you are sitting in, copy the command, and you are done.

> The interactive wizard that builds the exact command and a starter agent
> prompt for you lives on the **site**, not in this markdown file:
> [`/docs/getting-started`](https://ship.elmundi.com/docs/getting-started).
> It is a real React component (`landing/src/components/agent-setup-form.tsx`)
> and does not work when this file is rendered standalone (e.g. on GitHub).

## 1) Three adoption paths

### Existing repo

Most teams. Run from the root of the repo you want agents to operate on:

```bash
npx @elmundi/ship-cli init --yes \
  --agents cursor,codex,claude-md \
  --tracker linear --ci gh-actions --preset web-app \
  --copy-rules
```

`shipctl init` writes `.ship/config.yml` (RFC-0002), seeds the cache,
installs the per-agent rule files at the install targets declared in each
`collection/agent-rules-<agent>` artifact, and stops short of CI/tracker
scaffolding. Add `--bootstrap` for the supported `mobile-app + gh-actions
+ linear` skeleton (others get a `SHIP_BOOTSTRAP_PLAN.md`).

### Greenfield

Empty directory, brand-new product:

```bash
npx @elmundi/ship-cli new my-product \
  --preset web-app --tracker linear --ci gh-actions \
  --agents cursor,codex --yes
cd my-product
```

`shipctl new` runs `git init`, drops a minimal `README.md`, seeds
`.ship/config.yml`, and runs `init --copy-rules` for the listed agents.
Use `--here` to scaffold into the current directory instead of creating
`<name>/`.

### Quick verify

Anywhere with a `.ship/config.yml` already in place — useful in CI or as
a smoke test after a sync:

```bash
npx @elmundi/ship-cli verify --no-network
```

Runs every check under `cli/lib/verify/checks/`: config schema, gitignore,
rules markers, cache integrity, bootstrap markers, declared-agent disk
signals. `--no-network` skips the methodology / Linear / secret reachability
probes for offline runs.

## 2) What Ship expects from any stack

- A queue state equivalent to `Todo`.
- Execution states equivalent to `In Progress`, `In Review`, `Done`, `Blocked`.
- A way to store routing signals (`ready:*`, `stage:*`, `result:*`) or equivalent fields.
- A place to store evidence (comments, links, reports).
- A CI surface that can run lint → build → test → e2e → delivery → release.
- Secrets handled by the platform's secret store (never in `.ship/config.yml`).
- An agent — any of the 13 supported — with on-disk markers Ship can detect.
- A way to record `<kind>:<id>@<version>` per consumed artifact in the PR.
- An owner for promotion to prod (manual approver or scheduled gate).
- A digest/retro recipient (DL alias recommended, not a personal email).

## 3) After init: keep the loop tight

- Inspect: `shipctl doctor` — proposes a stack from on-disk signals; pair
  with `--write-inventory` to persist `.ship/inventory.json`.
- Stay current: `shipctl sync` — refreshes the cache; honours `artifacts.pins`.
- Verify: `shipctl verify` — full local + network checks.
- Configure: `shipctl config get|set|show` — atomic edits on `.ship/config.yml`.

## 4) Where to go next

- CLI quick reference: [shipctl CLI](../../cli/README.md)
- Adoption hub: [Pick a path](../adoption/index.md)
- Interactive contract: [Agent setup contract](../adoption/agent-setup-contract.md)
- Process policy: [Delivery, quality & release](../adoption/delivery-quality-and-release-process.md)
- Protocol RFCs: [RFC index](../rfc/index.md)
- Long rationale: [The book](../framework/index.md)
- Reference implementation: [ElMundi](../examples/elmundi/index.md)
