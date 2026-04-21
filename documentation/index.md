# Manual

The Manual is the operator's reference for Ship: how the framework works on disk, what the moving parts are called, and how to keep a working installation working. It is **not** a sales tour, a catalog browser, a CLI command-list, or a book — those live elsewhere on this site.

## What you will find here

- **Concepts** — the vocabulary. What is an artifact, a kind, a channel, a pin, an install_target, an adapter, a preset. Read this first if any noun on the site looks unfamiliar.
- **Configuration** — every field of `.ship/config.yml`, the `.ship/` on-disk layout, defaults, examples.
- **Lanes** — the `lanes:` block, how `shipctl run` + `shipctl lanes install` consume it, and what shows up on the Console `/lanes` page.
- **Operating** — day-2 work: pinning versions, switching channels, reading `verify` output, debugging `sync`, telemetry on/off, drafting feedback.
- **Authoring artifacts** — how to write your own pattern, tool, workflow, collection, preset, or adapter; front-matter contract; how to test locally.
- **Discovery contract** — the Phase 0–4 interview an agent runs before its first PR. Normative for agent integrators.
- **Agent matrix** — supported agent ids, their on-disk markers, install targets, and the adapter artifact for each.
- **Protocol** — the RFCs. Normative spec for the artifacts protocol, config schema, telemetry, adapters, and folder layout.
- **Troubleshooting** — common errors and what to do about them.
- **Legal** — license and versioning policy.

## What you will *not* find here

| If you want… | Go to |
|--------------|-------|
| The catalog of patterns / tools / collections | [/patterns](/patterns), [/tools](/tools), [/collections](/collections) |
| `shipctl` command and flag reference | [/cli](/cli) |
| The long-form rationale (why the loop looks the way it does) | [/book](/book) |
| Customer stories and reference deployments | [/use-cases](/use-cases) |
| The interactive setup wizard | [/docs/getting-started](/docs/getting-started) |

If a page in the Manual repeats one of those surfaces, it is a bug — open an issue.
