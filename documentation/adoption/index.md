# Adopt Ship to your project

Ship is delivered as **versioned artifacts** (RFC-0001) that agents resolve
through a single CLI: **`shipctl`**. Every adoption starts with one of three
commands; the rest is the methodology.

## Pick one adoption path

<div class="ship-card-grid" markdown="1">

<div class="ship-card" markdown="1">

### Existing repo

Use this when the product code already lives in git. `shipctl init`
detects on-disk markers, writes `.ship/config.yml`, and installs only
the agent rule files you ask for. Add `--bootstrap` to render CI +
tracker scaffolding for the supported preset triples.

```bash
npx @elmundi/ship-cli init --yes \
  --agents cursor,codex,claude-md \
  --tracker linear --ci gh-actions --preset web-app \
  --copy-rules
```

</div>

<div class="ship-card" markdown="1">

### Greenfield

Brand-new product, empty directory. `shipctl new` runs `git init`,
writes a minimal `README.md`, seeds `.ship/config.yml` from the
provided stack flags, and runs `init --copy-rules` for the listed
agents in one shot.

```bash
npx @elmundi/ship-cli new my-product \
  --preset web-app --tracker linear --ci gh-actions \
  --agents cursor,codex --yes
cd my-product
shipctl verify --no-network
```

</div>

<div class="ship-card" markdown="1">

### Pilot / verify-only

When the team wants to validate a Ship-enabled repo without touching
anything. Runs every check under `cli/lib/verify/checks/` (config,
gitignore, rules markers, cache integrity, bootstrap markers,
declared-agent disk signals). `--no-network` skips manifest /
Linear / secret reachability probes.

```bash
npx @elmundi/ship-cli verify --no-network
```

</div>

</div>

## Quick links

| Resource | Purpose |
|----------|---------|
| [Getting started](../getting-started/index.md) | Form-driven entry point that builds your `shipctl init` command + agent prompt. |
| [shipctl CLI reference](../tools/shipctl-cli.md) | Authoritative quick reference for every `shipctl` command. |
| [Agent setup contract](agent-setup-contract.md) | Mandatory interactive discovery behavior for agents (now with the machine-readable preamble). |
| [Agent playbook](agent-playbook.md) | Canonical generic onboarding playbook. |
| [Delivery, quality & release](delivery-quality-and-release-process.md) | End-to-end operating model, QA split, release gates, daily digest/retro. |
| [Agent launch matrix](agent-launch-matrix.md) | One protocol, 13 agent surfaces — id ↔ install target ↔ adapter artifact. |
| [Tracker adapters](../tools/ship-agent-trackers.md) | Per-tracker contracts (`linear`, `jira`, `github-issues`, `azure-boards`, `clickup`, `spreadsheet`, `none`). |
| [CI adapters](../tools/ship-agent-ci.md) | Per-CI contracts (`gh-actions`, `gitlab-ci`, `buildkite`, `circleci`, `azure-pipelines`, `jenkins`, `manual`). |
| [ElMundi rollout](elmundi.md) | Reference-org specific delta. |
| [The book](../framework/index.md) | Long-form rationale and trade-offs. |

## Authoritative protocol

The behavior of `shipctl` and the on-the-wire contract are normative in the
RFCs. If a doc and an RFC disagree, the RFC wins.

| RFC | Subject |
|-----|---------|
| [RFC-0001](../rfc/rfc-0001-artifacts-protocol.md) | Artifacts protocol — kinds, manifest, fetch policy, cache, pinning, channels, deprecation, yank. |
| [RFC-0002](../rfc/rfc-0002-shipctl-config.md) | `.ship/config.yml` schema, precedence, validation, gitignore defaults, `state.json` sibling. |
| [RFC-0003](../rfc/rfc-0003-telemetry-and-feedback.md) | Telemetry events (`type`-tagged), batching, denylist, feedback dedup. |
| [RFC-0004](../rfc/rfc-0004-adapters.md) | Adapter shape, hooks, templating, addendums, cross-adapter `requires`, `## Patch` merges. |

Browse the full [RFC index](../rfc/index.md) for status and changelogs.

## Source of truth for content

The actual rule bodies, presets, and addendums live as artifacts under
[`documentation/collections/`](../collections/). They are fetched by
`shipctl` — never copied into client repos:

- `collection/agent-rules-<agent>` — one per supported agent (13 today).
- `collection/preset-<preset>` — `web-app`, `api-backend`, `mobile-app`, `cli`,
  `monorepo`, `adoption-minimum`.
- `collection/addendum-<vertical>` — e.g. `addendum-pharma` for stricter
  evidence + retention.

`shipctl init` resolves these by looking at `stack.preset` and `stack.agents`
in `.ship/config.yml`. Changing the stack changes which artifacts are pulled;
versions are pinned via `artifacts.pins` (RFC-0001 § Pinning).

## Philosophy

- **Agent first.** Onboarding is interactive. The agent asks; the human confirms.
- **Interface first.** Methodology over vendor lock-in; adapters bridge to your tracker / CI.
- **Evidence first.** Every automation step leaves an auditable record (`<kind>:<id>@<version>` in the PR; tracker comments; CI run URLs).
- **No vendoring.** Methodology bodies live on the Ship site. Clients cache, never fork.
