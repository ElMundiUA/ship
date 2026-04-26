# Concepts

The vocabulary. Every other Manual page assumes the words below mean the same thing they mean here. The page is structured operator-first: the four nouns you live in (Inbox, Plays, Automations, Runs) come first; the supporting nouns (Coverage, Navigator, Knowledge bucket) come next; the internal protocol terms (Pattern, Lane, Workflow) appear later in their own section so engineers can map operator surface to backend ontology. The full normative model lives in [RFC-0010](./protocol/rfc-0010-plays-and-inbox.md); when this page and the RFC disagree, the RFC wins. For commands, follow the [`/cli`](/cli) link.

## The four operator nouns

Ship's console exposes exactly four operator-facing surfaces. Everything you do with the product is "open one of these four; act inside it". Three are read-mostly content surfaces — Plays, Automations, Runs. One is the only action surface — Inbox.

### Inbox

The single home for everything that needs human disposition. When a Play asks you a clarifying question, proposes an improvement, fails repeatedly, requests an approval, or wants a policy exception, an Inbox item lands with **one** owner.

| Field | Meaning |
|---|---|
| **In the console** | The `/inbox` page. Filter by `Mine` / `My team` / `Unassigned`, by type, by status. Item detail shows typed disposition buttons + reassign + snooze + an audit trail. |
| **Item types** | `clarification` · `improvement` · `failure` · `approval` · `exception`. Each has its own allowed dispositions — see [RFC-0010 § Inbox model](./protocol/rfc-0010-plays-and-inbox.md#inbox-model). |
| **Lifecycle** | `new` → `snoozed` (auto-returns) → `resolved` (terminal, positive) or `dismissed` (terminal, won't-do). |
| **In `.ship/config.yml`** | Nothing. Inbox lives in the workspace database, not on disk. Routing rules live under `Settings → Inbox routing`. |
| **Threshold** | A Run that needs nothing from a human does not generate an Inbox item. Manual one-shot runs always escalate failures; scheduled runs escalate after three consecutive failures. See [RFC-0010 § Run → Inbox threshold](./protocol/rfc-0010-plays-and-inbox.md#run--inbox-threshold). |

Single-owner discipline is enforced: group-typed routing resolves to one user at intake (round-robin / on-call / first), and reassignment is a first-class disposition. Multi-owner queues are explicitly out of scope for v1.

### Plays

The catalog of operational procedures. A Play is what you pick when you want Ship to *do something* — *PR review*, *Technical audit*, *Release readiness*, *Security deps scan*, and so on. Plays are organised into seven categories (Code review · Health checks · Release ops · Incident response · Knowledge & Docs · Planning & Process · Reviewers); the live catalog browses at [/patterns](/patterns).

| Field | Meaning |
|---|---|
| **In the console** | The `/plays` page. Cards grouped by category. Each card has two CTAs: **Run now** (one-shot dispatch) and **Automate** (open the assignment wizard). |
| **Atomic vs composite** | A Play maps to one underlying pattern (atomic) or several patterns running together with a default execution mode (composite). Composites are Ship-curated only in v1; operators don't define new ones. |
| **Inbox profile** | Each Play declares an `inbox.profile` (one of nine: `silent`, `scan_default`, `scan_with_autofix`, `flow_pr`, `flow_release`, `flow_incident`, `flow_reporting`, `role_reviewer`, `onboarding`) that bundles default `(handle, when[])` per Inbox type. |
| **In `.ship/config.yml`** | A Play is a `pattern:` (or `patterns: [...]`) reference inside a `lanes:` entry. The catalog itself is sourced from `artifacts/patterns/<id>/ARTIFACT.md`; see [RFC-0008](./protocol/rfc-0008-catalog-reform.md). |

#### Coverage

A sub-concept under Plays: **how many of the activated repos in your workspace have a given Play assigned**, expressed as `N/M repos`. Coverage drives the *Coverage* tab on `/automations`: the list is sorted by uncovered count descending, with a red badge on critical Plays (`scan-security-deps`, `scan-license-deps`, `flow-pr-self-review`, `flow-incident-postmortem`, `flow-release-notes`, `flow-cert-compliance`, `scan-pii-leakage`) that aren't at full coverage. Each row drills down into a covered / uncovered split with an *Apply to all uncovered* CTA so you can close gaps in one click. The matrix view is deferred to v2.

### Automations

A Play assigned to a scope with a cadence. "Run *PR review* on every pull request in this repo." "Run *Technical audit* on the fleet every Monday at 06:00." That's an Automation.

| Field | Meaning |
|---|---|
| **In the console** | The `/automations` page (operator reference: [Automations](./automations.md)). List view with scope filter (`fleet` / `repo` / `all`), Coverage tab, edit / pause / delete per row. |
| **Scope** | One of `repo` (one repo), `selected` (a chosen subset), or `fleet` (every activated repo). Fleet vs per-repo is a parameter, not a parallel hierarchy. |
| **Cadence** | A trigger: `event` (webhook), `schedule` (cron), or `once` (idempotent bootstrap). |
| **In `.ship/config.yml`** | One entry under `process.routines` per scheduled/manual routine. Legacy `lanes:` blocks are still accepted as compatibility input. |
| **Lifecycle** | Created by seed/config generation or edited through a PR against `.ship/config.yml`. Runtime invocation uses `shipctl run --routine <id>` (`--lane` remains a legacy alias). |

### Runs

The outcome-first execution history. A Run is a single execution of a Play — manual or scheduled. The list view reads as outcomes ("3 issues found · 1 PR opened"), not events ("workflow dispatched at 10:14:02").

| Field | Meaning |
|---|---|
| **In the console** | The `/runs` page. Filter by Play, repo, status, trigger (`manual` / `scheduled` / `event`), or `has escalations`. Each row shows the outcome sentence + a findings-by-severity strip + escalation badges. |
| **Escalations** | A Run that produced Inbox items carries deeplinks to them. The detail page lists every artifact the Run produced (`pr` / `issue` / `comment` / `doc` / …) and every escalation it raised. |
| **In `.ship/config.yml`** | Nothing. Runs live in the `pipeline_runs` table; the structured outcome lives in `pipeline_runs.outcome` (see [RFC-0010 § RunSummary](./protocol/rfc-0010-plays-and-inbox.md#runsummary-contract)). |
| **Lane vs request mode** | Every Play runs in either *lane mode* (scheduled / event-driven, declared in `lanes:`) or *request mode* (one-shot dispatch). Operators experience this as **Automations** vs. one-off **Run** dispatch from a Play card; the orchestration distinction is internal. |

## Navigator

The in-product chat agent at `/chat`. The Navigator can read the full Inbox / Plays / Automations / Runs / Coverage / Knowledge surface and (for admin users) take action on it — dispose Inbox items, run a Play now, automate a Play, toggle an Automation. Tool inventory and audit story: [navigator-tools](./internal/navigator-tools.md) (internal). The Navigator is available to every workspace member; mutating tools are admin-gated.

## Knowledge bucket

A scoped collection of retrievable **articles** the agent (Navigator or a Play) can consult at render time. Scopes form a ladder — `workspace → project → repo → user` — and the resolver returns the most-specific hit first. Sources include `.ship/knowledge/*.md` files mirrored from a repo, external uploads, Notion / Linear connectors, and per-user memory; all pass through the Distiller classifier before landing. Patterns wire buckets via `spec.knowledge_topics: [...]`. Full reference: [Knowledge buckets](./knowledge-buckets.md).

### Scope ladder

The resolution order for `knowledge_buckets`: `workspace → project → repo → user`. The console's scope pill in AppShell switches the active context; the backend resolver walks the ladder and returns the most-specific article per topic, with the `user` overlay acting as a parallel private layer for the signed-in user. Authoritative enum in `backend/app/db/models/agent_memory.py:BucketScope`.

### Distiller

The LLM-backed classifier (`backend/app/services/distiller.py`) that turns raw inbound content — repo-mirrored markdown, uploads, connector fetches — into structured `bucket_articles` rows tagged with topic + scope. Endpoint: `POST /v1/workspaces/{ws}/buckets/{slug}/distill`; run history lives at `/buckets/{slug}/distill/runs` and is visible on the bucket detail page. Inbound adapters live under `backend/app/services/distiller_sources.py`.

## Routing handles, groups, and dispositions

Three small but load-bearing concepts that govern who an Inbox item lands with and what they can do with it.

- **Handle** — a symbolic role declared by a Play (`code_owner`, `pr_author`, `release_manager`, `ops_oncall`, `workspace_owner`, …). Plays don't know about your users; they know about handles. The workspace maps each handle to a target via routing rules under `Settings → Inbox routing`.
- **Group** — a workspace-level operational set of users (`secops`, `on-call`, `release-managers`). Distinct from permission roles. A user can be `member` (permission) and `secops` (group); both axes are visible on `Settings → Members`. Roles control what people *can* do; groups control what people *should* do.
- **Disposition** — a typed action that resolves an Inbox item. Type-specific: an `approval` item allows *Approve / Reject / Request changes / Reassign*; an `improvement` item allows *Accept / Decline / Defer / Reassign*. *Accept* on an improvement enqueues an Automation creation. The full matrix is in [RFC-0010 § Types and dispositions](./protocol/rfc-0010-plays-and-inbox.md#types-and-dispositions).

## Internal terms (for engineers)

The operator surface above is what every Manual page now uses. Underneath, the protocol layer keeps a slightly different vocabulary — the same one used in `.ship/config.yml`, in `shipctl` output, in the API endpoints, and across the Python codebase. None of these are renamed by RFC-0010; they remain authoritative inside their layer.

### Pattern

The atomic executable artifact. Lives at `artifacts/patterns/<id>/ARTIFACT.md`. Carries the YAML front-matter (`spec.modes`, `spec.category`, `spec.install_target`, `spec.knowledge_topics`, `spec.inbox.profile`, …) plus the agent-facing body. Referenced everywhere by `pattern:<id>@<version>`. The wire and folder shape are normalised in [RFC-0001](./protocol/rfc-0001-artifacts-protocol.md) and [RFC-0005](./protocol/rfc-0005-artifact-folder-spec-v2.md). Operators see a *Play* on top of a Pattern (atomic) or several Patterns (composite); see [Plays](#plays) above.

### Lane

The persisted record under `lanes:` in `.ship/config.yml` representing one Automation. One entry binds a trigger (`event`, `schedule`, or `once`) to one or more `pattern:` references. `shipctl lanes install` renders `.github/workflows/ship-<lane>.yml` wrappers; `shipctl run <lane>` is the dispatch entry point. Full schema: [Configuration → `lanes`](./configuration.md#lanes); normative spec in [RFC-0007](./protocol/rfc-0007-lanes-and-run-agent.md). Operators see an *Automation* in the console; the `lane` term lives in YAML and CLI output.

### Workflow

The orchestration mode of a *composite* Play: `parallel` (patterns run in parallel branches), `sequential` (patterns run one after another, short-circuiting on failure), or `separate_workflows` (each pattern gets its own GitHub Actions workflow). Operators don't choose the mode in v1 — each composite Play has one default. The standalone `workflow` artifact kind was retired by [RFC-0007 Phase 6](./protocol/rfc-0007-lanes-and-run-agent.md); pinning `workflow/<id>` is now a config validation error.

### Pipeline / pipeline_run

The backend term for what operators call a *Run*. The `pipeline_runs` table holds one row per execution, with the structured outcome JSON in `pipeline_runs.outcome` (RunSummary contract). The Console `/runs` page reads from this table; legacy URLs `/pipelines` and `/pipelines/[pid]/runs/[rid]` 301-redirect to `/runs` and `/runs/[rid]`.

### Request

The historical name for a one-shot ad-hoc dispatch of a pattern (the old `/requests` page). Operators now do this via **Run now** on a Play card. Patterns with `modes` containing `request` expose their `spec.inputs` as a dynamic form. The `/requests` URL 301-redirects to `/plays?mode=request`.

### Kind, Category, Mode, Include

Catalog-level pattern frontmatter, unchanged by RFC-0010.

- **Kind** — the artifact's category. Today: `pattern` (a role or lane prompt), `tool` (an integration / adapter description), `collection` (a curated bundle: preset, addendum, or agent-rules set). Reserved: `doc`. Drives the URL (`/<kind>s`), the cache subdirectory, and the CLI subcommand. The earlier `workflow` kind was retired by RFC-0007.
- **Category** — the sub-type of a `pattern`. [RFC-0008](./protocol/rfc-0008-catalog-reform.md) fixes six values — `role`, `flow`, `scan`, `op`, `onboard`, `common` — matching the id prefix (`role-developer`, `flow-daily-retro`, `scan-tech-debt`, …). Drives Library grouping, default starter YAML, and whether a pattern is directly invokable.
- **Mode** — the invocation shape a pattern supports. `spec.modes: [lane, request]` lists one or both of *lane mode* (scheduled or event-driven, wired from `.ship/config.yml`) and *request mode* (one-shot dispatched from a Play card). `common-*` fragments declare `modes: []` — pulled in via `spec.include`, never invoked directly.
- **Include** — `spec.include: [...]`: a list of pattern ids whose body is composed into the host pattern's prompt at render time (max depth 2; cycles raise). Canonical way to share boilerplate — `pattern:common-base` is included by every `role-*` pattern.

### Artifact

A versioned unit of methodology Ship distributes — a `pattern`, `tool`, or `collection`. An artifact is a folder under `artifacts/<kind>/<id>/` whose required `ARTIFACT.md` carries the YAML front-matter (the single source of truth for metadata) plus the agent-facing body. Artifacts are referenced everywhere by `<kind>:<id>@<version>`, e.g. `pattern:role-developer@1.4.2`. See [RFC-0001](./protocol/rfc-0001-artifacts-protocol.md) and [RFC-0005](./protocol/rfc-0005-artifact-folder-spec-v2.md); browse the live catalog under [/patterns](/patterns), [/tools](/tools), and [/collections](/collections).

### Version, Channel, Manifest, Cache

Plumbing for the artifact catalog. Unchanged by RFC-0010.

- **Version** — strict semver triple (`MAJOR.MINOR.PATCH`, no `v` prefix) pinning one byte-identical body. The lint refuses any change to a folder's `content_sha256` without a version bump on the same PR. Bump rules in [RFC-0001 § Version bump rules](./protocol/rfc-0001-artifacts-protocol.md#version-bump-rules).
- **Channel** — the release track an artifact is published on. Two values today: `stable` (default) and `edge` (opt-in, pre-release). Selected via `api.channel` in `.ship/config.yml` or `SHIP_CHANNEL`. See [RFC-0001 § Channels](./protocol/rfc-0001-artifacts-protocol.md#channels).
- **Manifest** — the catalog index of all artifacts, served live by the methodology API. Under v2 ([RFC-0005](./protocol/rfc-0005-artifact-folder-spec-v2.md#catalog-manifests--removed-from-git)) the per-kind `manifest.json` files are removed from git; the API derives the index from `artifacts/**/ARTIFACT.md` at startup.
- **Cache** — the local read-through copy of artifacts that `shipctl` keeps under `.ship/cache/`. One folder per resolved version: `.ship/cache/<kind>/<id>@<version>/ARTIFACT.md` plus a sibling `.meta.json`. `.gitignore`d by default; the air-gapped escape hatch is `cache.vcs_tracked: true`. See [Configuration](./configuration.md).
- **Pin and disable** — a pin freezes the version `shipctl sync` will accept for an artifact. Pins live under `artifacts.pins` keyed by `<kind>/<id>`. To disable an artifact for a repo, remove its pin and remove its id from any preset/agent-rules collection; Ship has no separate "disabled" flag, only "not selected".

```yaml
artifacts:
  pins:
    pattern/role-developer: "1.4.2"
    tool/methodology-api: "~2.1"
```

### Adapter, Preset, Collection, Addendum

The composition layer for stack hints.

- **Adapter** — a versioned, declarative integration that teaches `shipctl` about a new tracker, CI, language, or agent. Each adapter belongs to one of four classes implemented under `cli/lib/adapters/{trackers,ci,language,agents}/<id>.mjs` and exposes three hooks normalised in [RFC-0004](./protocol/rfc-0004-adapters.md): `detect(cwd)`, `bootstrap(cfg)`, `verify(cfg)`. Adapters are also published as `tool` artifacts on the catalog so the methodology surface and the runtime detector stay in sync.
- **Preset** — a `collection` whose `spec.subkind` is `preset`: a starter bundle for a stack shape (`web-app`, `api-backend`, `mobile-app`, `cli`, `monorepo`, `adoption-minimum`). Selected by `shipctl init --preset <id>` and rendered with `--bootstrap`.
- **Collection** — the artifact kind for any curated bundle. A collection composes other artifacts by id reference under `spec.composes`. Three subkinds: `preset`, `agent-rules`, `addendum`.
- **Addendum** — a `collection` whose `spec.subkind` is `addendum`: a vertical-specific overlay that **tightens** an existing preset without ever relaxing one of its rules ([RFC-0004 § Addendums](./protocol/rfc-0004-adapters.md#addendums)). Today's addendums: `addendum-pharma`, `addendum-fin`, `addendum-health`.

### Install target, Marker, Agent footprint, Stack hint

The agent-installation plumbing.

- **Install target** — the repo-relative path where the rendered body of an `agent-rules-*` collection should land. Front-matter field on the collection (`spec.install_target`) read by `shipctl init --copy-rules`, with a per-agent fallback in `cli/lib/detect.mjs:KNOWN_AGENTS` (e.g. Cursor → `.cursor/rules/ship-artifacts-protocol.mdc`, Claude → `CLAUDE.md`, Codex → `.codex/SHIP_API.md`). The CLI writes a marker-delimited block; re-runs are idempotent. Per-agent table on [the agent matrix page](./agent-matrix.md).
- **Marker** — a pair of HTML comments that delimits the block `shipctl` owns inside a file it does not own. Default rule-installation pair: `<!-- ship-cli: artifacts-protocol v1 -->` and `<!-- ship-cli:end artifacts-protocol -->`; an `agent-rules` collection may override the start sentinel via `spec.marker`. Adapters use the same convention for append-safe merges via `## Patch` blocks tagged `marker="ship-managed:<id>"` ([RFC-0004 § Append-safe merges](./protocol/rfc-0004-adapters.md#append-safe-merges-via--patch)).
- **Agent footprint** — the on-disk signal `shipctl doctor` uses to decide which agents a repo already runs. Examples: `.cursor/`, `AGENTS.md`, `CLAUDE.md`, `.codex/`, `.github/copilot-instructions.md`, `.aider.conf.yml`. Full mapping (`KNOWN_AGENTS`) in `cli/lib/detect.mjs`. Footprints are evidence; the source of truth for which agents are active is `stack.agents` in `.ship/config.yml`.
- **Stack hint** — one of the [`shipctl init`](/cli) flags that bind the methodology to a concrete stack: `--tracker`, `--ci`, `--preset`, `--language` (with `--agents` as the fifth flag). Each value is an enum normalised in [RFC-0002 § stack](./protocol/rfc-0002-shipctl-config.md#stack); the same values land in `stack.*` of `.ship/config.yml`.

```bash
shipctl init --yes \
  --agents cursor,codex --tracker linear --ci gh-actions --preset web-app
```

### Discovery contract, Telemetry event, Feedback draft

Operator-touching but historically named protocol terms.

- **Discovery contract** — the five-phase interview (Phase 0 machine preamble, Phase 1 discovery, Phase 2 plan, Phase 3 execution, Phase 4 follow-up) that an agent integrating with Ship MUST run before opening its first PR. Full text at [`/docs/discovery`](./discovery.md).
- **Telemetry event** — a single JSON line `shipctl` may emit when `telemetry.share=true`. Five `type` values are allowed: `artifact.fetch`, `artifact.use`, `artifact.sync`, `feedback.submit`, `doctor.result`. Events are first appended to `.ship/telemetry-outbox.jsonl`, then flushed in batches. Opt out: `shipctl telemetry off` or `SHIP_TELEMETRY=false`. Full schema in [RFC-0003](./protocol/rfc-0003-telemetry-and-feedback.md).
- **Feedback draft** — a markdown file `shipctl feedback draft` writes under `.ship/feedback-drafts/YYYY-MM-DD-HHMMSS-<kind>-<id>.md`, with YAML front-matter and free-form `## Summary` / `## Suggestion` / `## Context` sections. Drafts are private until `shipctl feedback submit <draft>` runs the sanitizer and `POST /feedback`s the result. Pipeline and sanitizer rules in [RFC-0003 § Submit flow](./protocol/rfc-0003-telemetry-and-feedback.md#submit-flow).

## Glossary

Reproduced from [RFC-0010 § Terms](./protocol/rfc-0010-plays-and-inbox.md#terms) for ready reference.

| Term | Definition |
|---|---|
| **Play** | A ready-made operational procedure in the catalog. Atomic or composite. |
| **Automation** | A Play assigned to a scope with a cadence. |
| **Run** | A single execution of a Play (manual or automated). |
| **Inbox item** | A work item requiring human disposition. Always has one owner. |
| **Handle** | A symbolic role declared by a Play; resolved by workspace routing rules. |
| **Group** | A workspace-level operational set of users. Distinct from permission role. |
| **Disposition** | A typed action that resolves an Inbox item. |
| **Coverage** | How many activated repos have a given Play assigned. |
| **Lane** | *(internal)* Persisted record in `.ship/config.yml` for an Automation. Not user-facing. |
| **Pattern** | *(internal)* Atomic executable definition (markdown + frontmatter). Not user-facing. |
| **Workflow** | *(internal)* Orchestration mode of a composite Play (`parallel` / `sequential` / `separate_workflows`). Not user-facing. |

## Where to next

Read [Configuration](./configuration.md) to see the internal terms expressed as fields of `.ship/config.yml` and the rest of the `.ship/` layout. Read [Automations](./automations.md) for the operator-first reference for the Automations console page and the `lanes:` they compile to. Read [Knowledge buckets](./knowledge-buckets.md) for the scoped knowledge model and the Distiller. Read [Authoring](./authoring.md) to write a new artifact end-to-end against the v2 folder spec. Read the [Protocol RFCs](./protocol/index.md) for the normative definitions — [RFC-0010](./protocol/rfc-0010-plays-and-inbox.md) for the operator IA, [RFC-0001](./protocol/rfc-0001-artifacts-protocol.md) for the wire and version rules, [RFC-0002](./protocol/rfc-0002-shipctl-config.md) for the config schema, [RFC-0003](./protocol/rfc-0003-telemetry-and-feedback.md) for telemetry and feedback, [RFC-0004](./protocol/rfc-0004-adapters.md) for adapters, [RFC-0005](./protocol/rfc-0005-artifact-folder-spec-v2.md) for the folder layout, [RFC-0007](./protocol/rfc-0007-lanes-and-run-agent.md) for `lanes:`, [RFC-0008](./protocol/rfc-0008-catalog-reform.md) for the catalog reform.
