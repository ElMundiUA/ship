# Manual

The Manual is the operator's reference for Ship: how the framework works on disk, what the moving parts are called, and how to keep a working installation working. It is **not** a sales tour, a catalog browser, a CLI command-list, or a book — those live elsewhere on this site.

> **Vocabulary at a glance.** Ship is organised around four operator nouns — **[Inbox](./concepts.md#inbox)** (work that needs you), **[Plays](./concepts.md#plays)** (the catalog of procedures), **[Automations](./automations.md)** (Plays running on a cadence), and **[Runs](./concepts.md#runs)** (the outcome-first execution history). The in-product chat agent at `/chat` is the **[Navigator](./concepts.md#navigator)**. Internally these map onto the `lanes:` config schema and the `pipeline_runs` table — see [Configuration](./configuration.md). The canonical model lives in [RFC-0010](./protocol/rfc-0010-plays-and-inbox.md).

## What you will find here

- **[Concepts](./concepts.md)** — the vocabulary. Plays / Automations / Runs / Inbox first; the artifact / kind / channel / pin / install_target / preset terms underneath. Read this first if any noun on the site looks unfamiliar.
- **[Configuration](./configuration.md)** — every field of `.ship/config.yml`, the `.ship/` on-disk layout, defaults, examples. The `lanes:` schema is the protocol layer behind the **Automations** console page.
- **[Automations](./automations.md)** — the operator mirror of the `/automations` console page: what an Automation is, how to assign one, the Coverage tab, and how it compiles down to `lanes:` entries in `.ship/config.yml`.
- **[Knowledge buckets](./knowledge-buckets.md)** — the scoped knowledge surface (`workspace / project / repo / user`), the Distiller, and how patterns reach for buckets via `spec.knowledge_topics`.
- **[Operating](./operating.md)** — day-2 work: pinning versions, switching channels, reading `verify` output, debugging `sync`, telemetry on/off, drafting feedback.
- **[Authoring artifacts](./authoring.md)** — how to write your own pattern, tool, collection, preset, or adapter; front-matter contract; how to test locally.
- **[Discovery contract](./discovery.md)** — the Phase 0–4 interview an agent runs before its first PR. Normative for agent integrators.
- **[Agent matrix](./agent-matrix.md)** — supported agent ids, their on-disk markers, install targets, and the adapter artifact for each.
- **[Protocol](./protocol/index.md)** — the RFCs. Normative spec for the artifacts protocol, config schema, telemetry, adapters, folder layout, lanes, the catalog reform, and the operator IA.
- **[Troubleshooting](./troubleshooting.md)** — common errors and what to do about them.
- **[Legal](./legal.md)** — license and versioning policy.

## What you will *not* find here

| If you want… | Go to |
|--------------|-------|
| The catalog of patterns / tools / collections | [/patterns](/patterns), [/tools](/tools), [/collections](/collections) |
| `shipctl` command and flag reference | [/cli](/cli) |
| The long-form rationale (why the loop looks the way it does) | [/book](/book) |
| Customer stories and reference deployments | [/use-cases](/use-cases) |
| The interactive setup wizard | [/docs/getting-started](/docs/getting-started) |

If a page in the Manual repeats one of those surfaces, it is a bug — open an issue.

## Where to next

Operator surface, in the order most people meet it:

- **[Concepts](/docs/concepts)** if any Ship noun looks unfamiliar — start with Plays, Automations, Runs, Inbox.
- **[Inbox](/docs/concepts#inbox)** — what the unified attention surface holds (clarifications, improvements, failures, approvals, exceptions) and how items get routed to one owner.
- **[Plays](/docs/concepts#plays)** — the catalog of operational procedures and how they map to the underlying patterns.
- **[Automations](/docs/automations)** — assigning a Play to a scope (this repo / selected / fleet) on a cadence, plus the Coverage tab.
- **[Runs](/docs/concepts#runs)** — the outcome-first execution history and how it links back to Inbox items.
- **[Knowledge buckets](/docs/knowledge-buckets)** — what the Navigator and Plays consult for repo-specific facts.

Reference layer (still authoritative under the hood):

- **[Configuration](/docs/configuration)** — the full `.ship/config.yml` field reference.
- **[RFC-0010](/docs/protocol/rfc-0010-plays-and-inbox)** — the canonical model for the four operator nouns.
- **[RFC-0007](/docs/protocol/rfc-0007-lanes-and-run-agent)** — `lanes:` and the `shipctl run` dispatch surface that backs Automations.
- **[RFC-0008](/docs/protocol/rfc-0008-catalog-reform)** — the `<category>-<name>` pattern catalog reform.
