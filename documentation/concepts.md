# Concepts

The vocabulary. Every other Manual page assumes the words below mean the same thing they mean here. The page is a glossary, not a tutorial: each entry defines a Ship-specific noun, points at where that noun lives on disk or in the protocol, and links to the page that owns its longer story. Read top-to-bottom on first pass, or jump in via the in-page anchors when another page links back here. If you want the full normative text, follow the RFC links; if you want commands, follow the [`/cli`](/cli) link.

## Artifact

A versioned unit of methodology that Ship distributes — a `pattern`, `tool`, `workflow`, or `collection`. An artifact is a folder under `artifacts/<kind>/<id>/` whose required `ARTIFACT.md` carries the YAML front-matter (the single source of truth for metadata) plus the agent-facing body. Artifacts are referenced everywhere by `<kind>:<id>@<version>`, e.g. `pattern:cloud-developer@1.4.2`. The wire and folder shape are normalized in [RFC-0001](/docs/protocol/rfc-0001-artifacts-protocol) and [RFC-0005](/docs/protocol/rfc-0005-artifact-folder-spec-v2); browse the live catalog under [/patterns](/patterns), [/tools](/tools), [/workflows](/workflows), and [/collections](/collections).

## Kind

The artifact's category. Ship ships exactly four kinds: `pattern` (a role or lane prompt), `tool` (an integration or adapter description), `workflow` (an end-to-end runbook stitching roles and tools), and `collection` (a curated bundle: a preset, an addendum, or an agent-rules set). The wire surface in [RFC-0001](/docs/protocol/rfc-0001-artifacts-protocol#artifact-kinds) also reserves `doc` for indexed long-form pages under `documentation/`, but `doc` is not authored as a folder — it is anything the site exposes by path. Kind drives the URL (`/<kind>s`), the cache subdirectory, and the CLI subcommand: `shipctl pattern …`, `shipctl tool …`, `shipctl workflow …`, `shipctl collection …`.

## Version

A strict semver triple (`MAJOR.MINOR.PATCH`, no `v` prefix) that pins one byte-identical body. The lint refuses any change to a folder's `content_sha256` without a version bump on the same PR. MAJOR is reserved for breaking changes (renamed gates, moved roles, contracts dropped); MINOR for additive changes (new optional section, new tag); PATCH for clarifications (typos, link updates). Bump rules and the publish-time hash check are normative in [RFC-0001 § Version bump rules](/docs/protocol/rfc-0001-artifacts-protocol#version-bump-rules).

## Channel

The release track an artifact is published on. Two values today: `stable` (default; only versions the project has marked released) and `edge` (opt-in, pre-release). A repo selects the channel via `api.channel` in [`/.ship/config.yml`](/docs/configuration) or the `SHIP_CHANNEL` env var; one-shot overrides use `--channel` on `shipctl init` and `shipctl sync`. The same artifact id may carry different version timelines per channel; clients on `stable` never see `edge`-only versions. See [RFC-0001 § Channels](/docs/protocol/rfc-0001-artifacts-protocol#channels).

## Manifest

The catalog index of all artifacts, served live by the methodology API. Under v2 ([RFC-0005](/docs/protocol/rfc-0005-artifact-folder-spec-v2#catalog-manifests--removed-from-git)) the per-kind `manifest.json` files are removed from git; the API derives the index from `artifacts/**/ARTIFACT.md` at startup and serves it via `GET /api/<kind>` and `GET /api/<kind>/<id>`. The legacy fan-out `GET /manifest` from [RFC-0001](/docs/protocol/rfc-0001-artifacts-protocol#http-api-surface) still drives `shipctl sync`'s freshness compare; the per-repo mirror of the last response is stored in `.ship/state.json:last_manifest_hash`.

## Cache

The local read-through copy of artifacts that `shipctl` keeps under `.ship/cache/`. One folder per resolved version: `.ship/cache/<kind>/<id>@<version>/ARTIFACT.md` plus a sibling `.meta.json` recording `source`, `content_sha256`, and `fetched_at`. The cache is `.gitignore`d by default; the air-gapped escape hatch is `cache.vcs_tracked: true` in `.ship/config.yml`. Methodology bodies never enter your repo's git history — every consumption is a verified read from cache. The fetch policy and TTL rules are in [RFC-0001 § Fetch policy](/docs/protocol/rfc-0001-artifacts-protocol#fetch-policy); the on-disk reference lives in [Configuration](/docs/configuration).

## Pin and disable

A pin freezes the version `shipctl sync` will accept for an artifact. Pins live under `artifacts.pins` in [`/.ship/config.yml`](/docs/configuration) keyed by `<kind>/<id>`, with values that are an exact semver (`1.4.2`), a caret (`^1.4.0`), a tilde (`~1.4`), or a bare major. `shipctl sync` never upgrades a pinned entry; an unsatisfiable pin is a hard error. To disable an artifact for a repo, remove its pin and remove its id from any preset/agent-rules collection your stack composes — Ship has no separate "disabled" flag, only "not selected". To temporarily ignore a pin for one run, use `shipctl sync --force-unpin`.

```yaml
artifacts:
  pins:
    pattern/cloud-developer: "1.4.2"
    workflow/scheduled-sdlc-lane: "~2.1"
```

## Adapter

A versioned, declarative integration that teaches `shipctl` about a new tracker, CI, language, or agent. Each adapter belongs to one of four classes implemented under `cli/lib/adapters/{trackers,ci,language,agents}/<id>.mjs` and exposes three hooks normalized in [RFC-0004](/docs/protocol/rfc-0004-adapters): `detect(cwd)` for `shipctl doctor` (returns `present`, `confidence`, `evidence`), `bootstrap(cfg)` for `shipctl init --bootstrap` (writes files and lists required secrets), and `verify(cfg)` for `shipctl verify`. Adapters are also published as `tool` artifacts on the catalog (`tools/linear`, `tools/github-actions`, …) so the methodology surface and the runtime detector stay in sync.

## Preset

A `collection` whose `spec.subkind` is `preset`: a starter bundle for a stack shape. The six presets shipped today are `web-app`, `api-backend`, `mobile-app`, `cli`, `monorepo`, and `adoption-minimum`, each living at [`artifacts/collections/preset-<id>/`](/collections). A preset declares `compatible_trackers`, `compatible_ci`, `compatible_agents`, and the `required_tools` / `optional_tools` it composes; `shipctl init --preset <id>` selects it and `--bootstrap` renders the preset's CI + tracker + secrets scaffolding for the chosen `--tracker` / `--ci` combination.

## Collection

The artifact kind for any curated bundle. A collection composes other artifacts by id reference under `spec.composes`, never by directory nesting. Three subkinds exist today: `preset` (stack starter), `agent-rules` (the rule body for a single agent surface, e.g. `agent-rules-cursor`), and `addendum` (regulated-vertical overlay). `shipctl init` resolves a collection by syncing it into `.ship/cache/`, then handing its parts to whichever post-step needs them (`--copy-rules`, `--bootstrap`).

## Addendum

A `collection` whose `spec.subkind` is `addendum`: a vertical-specific overlay that **tightens** an existing preset without ever relaxing one of its rules ([RFC-0004 § Addendums](/docs/protocol/rfc-0004-adapters#addendums)). Today's addendums are [`addendum-pharma`](/collections), `addendum-fin`, and `addendum-health`; each declares the presets it `applies_to` and the regulatory frameworks it covers (HIPAA, GDPR, 21 CFR Part 11, EU AI Act, …). Pick one when your repo is in a regulated context: pin it under `artifacts.pins` (e.g. `collection/addendum-pharma: "1.0.0"`) or pass `--addendum pharma` to `shipctl init`.

## Install target

The repo-relative path where the rendered body of an `agent-rules-*` collection should land. It is a front-matter field on the collection (`spec.install_target`) read by `shipctl init --copy-rules`, with a per-agent fallback in `cli/lib/detect.mjs:KNOWN_AGENTS` (e.g. Cursor → `.cursor/rules/ship-artifacts-protocol.mdc`, Claude → `CLAUDE.md`, Codex → `.codex/SHIP_API.md`). The CLI writes a marker-delimited block into the target so the file can host other content; re-runs are idempotent. The full per-agent table is on [the agent matrix page](/docs/agent-matrix).

## Marker

A pair of HTML comments that delimits the block `shipctl` owns inside a file it does not own. The default rule-installation pair is `<!-- ship-cli: artifacts-protocol v1 -->` and `<!-- ship-cli:end artifacts-protocol -->`; an `agent-rules` collection may override the start sentinel via `spec.marker` in its front-matter. The CLI also appends a one-line `<!-- ship-cli: installed-from collection/<id>@<version> -->` footer so re-runs can detect (and skip or `--force`) re-installs of the same version. Adapters use the same convention for append-safe merges via `## Patch` blocks tagged `marker="ship-managed:<id>"` ([RFC-0004 § Append-safe merges](/docs/protocol/rfc-0004-adapters#append-safe-merges-via--patch)).

## Agent footprint

The on-disk signal `shipctl doctor` uses to decide which agents a repo already runs — a directory, a config file, or a magic markdown filename. Examples: `.cursor/` for Cursor, `AGENTS.md` for Codex / generic, `CLAUDE.md` for Claude Code, `.codex/` for Codex CLI, `.github/copilot-instructions.md` for Copilot, `.aider.conf.yml` for Aider. The full mapping (`KNOWN_AGENTS`) lives in `cli/lib/detect.mjs` and drives both `shipctl doctor`'s detection and the install-target fallback above. Footprints are evidence, not configuration: the source of truth for which agents are active is `stack.agents` in [`/.ship/config.yml`](/docs/configuration).

## Stack hint

One of the four [`shipctl init`](/cli) flags that bind the methodology to a concrete stack: `--tracker`, `--ci`, `--preset`, `--language` (with `--agents` as the fifth, agent-list flag). Each value is an enum normalized in [RFC-0002 § stack](/docs/protocol/rfc-0002-shipctl-config#stack); the same values land in `stack.*` of `.ship/config.yml` and gate the addable adapters. Hints survive the run: passing them once at `init` time is what later `shipctl sync`, `shipctl doctor`, and `shipctl verify` read from config.

```bash
shipctl init --yes \
  --agents cursor,codex --tracker linear --ci gh-actions --preset web-app
```

## Discovery contract

The five-phase interview (Phase 0 machine preamble, Phase 1 discovery, Phase 2 plan, Phase 3 execution, Phase 4 follow-up) that an agent integrating with Ship MUST run before opening its first PR. The contract is the human-facing handshake — `shipctl doctor --json` plus a recorded answer block under each phase — and it is normative for any agent shipping a Ship integration. Full text and per-phase requirements are at [`/docs/discovery`](/docs/discovery).

## Telemetry event

A single JSON line `shipctl` may emit when `telemetry.share=true`. Five `type` values are allowed: `artifact.fetch`, `artifact.use`, `artifact.sync`, `feedback.submit`, `doctor.result`. Events are first appended to `.ship/telemetry-outbox.jsonl`, then flushed in batches of up to 100 to `POST /telemetry` (60 batches per minute per `anonymous_id`). The denylist (`path`, `code`, `diff`, `branch`, `remote`, `email`) is enforced both client-side (before write) and server-side (`400` on violation). To opt out: `shipctl telemetry off` or `SHIP_TELEMETRY=false`. Full schema and round-trip in [RFC-0003](/docs/protocol/rfc-0003-telemetry-and-feedback).

## Feedback draft

A markdown file `shipctl feedback draft` writes under `.ship/feedback-drafts/YYYY-MM-DD-HHMMSS-<kind>-<id>.md`, with YAML front-matter (`kind`, `id`, `version`, `title`, `tags`) and free-form `## Summary` / `## Suggestion` / `## Context` sections. Drafts are private until `shipctl feedback submit <draft>` runs the sanitizer and `POST /feedback`s the result, which the server turns into a labeled GitHub issue (deduplicating against existing open issues for the same `artifact:<kind>:<id>` + `version:<v>` labels). Submitted drafts move to `.ship/feedback-drafts/sent/`. Pipeline and sanitizer rules in [RFC-0003 § Submit flow](/docs/protocol/rfc-0003-telemetry-and-feedback#submit-flow); commands in [`/cli`](/cli).

## Where to next

Read [Configuration](/docs/configuration) to see these terms expressed as fields of `.ship/config.yml` and the rest of the `.ship/` layout; read [Authoring](/docs/authoring) to write a new artifact end-to-end against the v2 folder spec; read the [Protocol RFCs](/docs/protocol) for the normative definitions ([RFC-0001](/docs/protocol/rfc-0001-artifacts-protocol) for the wire and version rules, [RFC-0002](/docs/protocol/rfc-0002-shipctl-config) for the config schema, [RFC-0003](/docs/protocol/rfc-0003-telemetry-and-feedback) for telemetry and feedback, [RFC-0004](/docs/protocol/rfc-0004-adapters) for adapters, [RFC-0005](/docs/protocol/rfc-0005-artifact-folder-spec-v2) for the folder layout).
