---
rfc: 0005
title: "Artifact folder spec v2 — frontmatter as single source of truth"
status: Proposed
created: 2026-04-19
supersedes_in_part: [rfc-0001]
---

# RFC-0005 — Artifact folder spec v2

## Summary

Each Ship artifact becomes a **folder** containing an `ARTIFACT.md` whose YAML frontmatter is the **single source of truth** for the artifact's metadata, version, and integration contract. The body of `ARTIFACT.md` is the agent-facing instruction. Optional sibling files (`examples/`, `reference/`, `scripts/`, `tests/`, `i18n/`, `CHANGELOG.md`) ship inside the same folder. The catalog `manifest.json` files (one per kind) are removed from git; the methodology API serves a live, FS-derived index from the same folders. CLI fetches an artifact as a folder, not a file. Drift is detected by lint, not by hand-syncing JSON.

## Motivation

Today, four facts about every artifact are repeated in two places — the body file (`prompts/cloud-agent/developer.md`) and the kind manifest (`patterns/manifest.json`). Each release pays a tax to keep them aligned. The pain in concrete form:

- Title, summary, tags, version, sha, channel, `min_shipctl` live in JSON; the body lives elsewhere; an editor who updates one but not the other ships a broken record. CI catches some of this, but only after a PR is opened.
- An artifact has nowhere to keep examples, evals, runnable helpers, or localised translations: every kind today is a single `.md` and a JSON row. Anything richer requires picking an arbitrary spot in `documentation/` and praying nobody moves it.
- The path key in the manifest is fragile: bodies live under three different roots (`prompts/`, `documentation/`, `tools/`), each with its own depth. The `path` column hides this.
- `description` is rendered as a marketing summary (`"Implementation role: branch contract..."`), not as a discoverable hint for an agent picking artifacts by what + when. Anthropic's `SKILL.md` format demonstrates the value of treating discovery as a first-class authoring concern.
- `content_sha256` is computed over the body file alone. Adding a sibling `examples/foo.md` changes nothing the cache or telemetry can see, even though the artifact has materially changed.
- `tools/` as a directory name was already taken by an unrelated MCP-server experiment, which made the catalog physically uncomfortable to edit. (Resolved out-of-band by removing the experiment in commit `d77f920`.)

The goal of this RFC is to remove duplicate state, give artifacts room to grow, and make the description a first-class discovery surface — without changing the wire protocol that clients already rely on (`shipctl fetch <kind>/<id>` continues to work).

## Layout on disk

```
artifacts/
├── patterns/
│   ├── cloud-developer/
│   │   ├── ARTIFACT.md          (required, single source of truth)
│   │   ├── examples/            (optional)
│   │   │   └── implementation-pr.md
│   │   ├── reference/           (optional, deep-dives)
│   │   │   └── branch-naming.md
│   │   ├── scripts/             (optional, runnable helpers)
│   │   │   └── verify-branch.mjs
│   │   ├── tests/               (optional, evals)
│   │   │   └── golden.yaml
│   │   ├── i18n/                (optional, localisations)
│   │   │   └── uk/ARTIFACT.md
│   │   └── CHANGELOG.md         (recommended for v ≥ 1.0)
│   └── ...
├── workflows/
│   └── scheduled-sdlc-lane/
│       └── ARTIFACT.md
├── tools/
│   └── linear/
│       └── ARTIFACT.md
└── collections/
    └── preset-mobile-app/
        └── ARTIFACT.md
```

The `artifacts/` root sits at the repo root, not under `documentation/`, because artifacts are the **product** that the CLI distributes. `documentation/` keeps its current role as long-form prose for humans (the book, RFCs, getting started, examples).

## Frontmatter — required for every kind

```yaml
---
artifact_kind: pattern              # pattern | workflow | tool | collection
id: cloud-developer                 # kebab-case, ≤ 64 chars, unique within kind
name: Developer (cloud lane)        # human title, ≤ 80 chars
description: >                      # SKILL.md style: third person, what + when
  Implementation prompt for the scheduled developer role: defines branch
  contract, PR shape, and evidence requirements. Use when wiring the
  developer slot of a Ship cron grid, when an agent picks an issue tagged
  `agent:developer`, or when reviewing what a cloud agent is allowed to do
  on a picked ticket.
version: 1.2.0                      # semver, no `v` prefix
channel: stable                     # stable | edge | experimental
min_shipctl: "0.3.0"
updated_at: 2026-04-19T00:00:00Z
content_sha256: <auto>              # over folder contents — written by lint
deprecated: false
replaced_by: null
yanked: false
group: cloud-agent                  # was the "group" column in the manifest
tags: [pr, implementation, scheduled]
authors: ["@elmundi/ship-core"]
license: Apache-2.0

spec:
  # kind-specific block, see below
---
```

### Description quality bar

Borrowed straight from `SKILL.md`. The lint validates:

- written in **third person** (no `I/we/you`);
- contains both **WHAT** the artifact does and **WHEN** an agent should reach for it;
- includes at least one **trigger term** an agent could hit on (state names, label names, role names, file paths, vendor names);
- length ≤ **1024 characters**.

A description that fails any of these blocks merge. Description quality is part of the artifact's API; we do not skip it just because the body is good.

## `spec:` payloads (kind-specific)

```yaml
# patterns/<id>/ARTIFACT.md
spec:
  role: developer                              # if it is a cloud-agent role; else omit
  triggers:                                    # discoverability cues for agents
    - linear-state:Ready
    - label:agent:developer
  inputs: [issue-key, repo-snapshot]
  outputs: [pull-request, evidence-comment]
  install_target: prompts/cloud-agent/developer.md
  evals: tests/golden.yaml                     # path inside this folder, optional
```

```yaml
# workflows/<id>/ARTIFACT.md
spec:
  intent: cron                                 # cron | event | manual
  cadence: "0 */2 * * *"                       # informational, not enforced
  runtime: github-actions                      # github-actions | gitlab-ci | cron-anywhere
  inputs: [tracker-tickets, repo]
  outputs: [pr, ci-run, ticket-evidence]
  install_target: .github/workflows/sdlc-scheduled.yml
  requires:
    tools: [tracker, ci]
    patterns: [cloud-developer, cloud-intake]
```

```yaml
# tools/<id>/ARTIFACT.md
spec:
  capability: tracker                          # tracker | ci | e2e | agents | platform
  vendor_neutral_id: tracker-contract
  interfaces: [graphql-api, web-app]
  auth: [api-key, oauth]
  contracts: [issue-state, label-set, evidence-comment]
```

```yaml
# collections/<id>/ARTIFACT.md
spec:
  subkind: preset                              # preset | addendum | starter
  applies_to: [mobile-app]
  composes:
    patterns: [cloud-developer, cloud-intake, cloud-qa-architect]
    workflows: [scheduled-sdlc-lane, hosted-e2e-regression]
    tools: [tracker-contract, github-actions, playwright]
  addendums_compatible_with: [pharma]
  regulatory_frameworks: []                    # only addendums populate this
```

Unknown `spec` fields are ignored by today's CLI but not stripped by the server, so a future minor version can add fields without breaking older clients (consistent with RFC-0001's forward-compatibility rule).

## `content_sha256` — over the folder, not the file

The hash is a deterministic merkle over every file in the folder **except** `CHANGELOG.md` and the frontmatter `content_sha256` field itself (it is rewritten by the lint). Adding `examples/foo.md` changes the hash → telemetry sees a real change → the version field must be bumped on the same PR. The lint enforces "sha changed without version bump" as a hard error.

The exact algorithm, sorted file list, and canonical byte representation are normative and live in `documentation/rfc/rfc-0005-artifact-folder-spec-v2/sha-algorithm.md` (added in Wave 1).

## Catalog manifests — removed from git

The current `patterns/manifest.json`, `workflows/manifest.json`, `tools/manifest.json`, `collections/manifest.json` files are deleted in Wave 1. They are replaced by:

- An **in-memory index** built by the methodology API at startup from `artifacts/**/ARTIFACT.md`, refreshed on filesystem watch in dev. Served as `GET /api/<kind>` (list) and `GET /api/<kind>/<id>` (single artifact, including a directory listing for nested files).
- An **optional release-time bundle** (`shipctl release-bundle.tar.gz` + `index.json`), produced by `scripts/build_release_bundle.py`, attached to GitHub releases for offline / firewalled adoption. This is **not** committed to the repo.

The CLI never reads a committed manifest. `shipctl list patterns` and friends always go through the API.

## API surface — what changes, what stays

Stays compatible:

- `GET /api/patterns` (was: `GET /patterns`) returns the same list shape as today, projected from frontmatter; clients keyed by `id`/`version`/`content_sha256` keep working.
- `GET /api/<kind>/<id>` returns the artifact body plus metadata; older clients ignore the new `files[]` array.

New:

- `GET /api/<kind>/<id>/files/<rel>` — fetch a sibling file (`examples/foo.md`, `scripts/helper.sh`, `tests/golden.yaml`) by path relative to the artifact folder.
- `GET /api/<kind>/<id>/tree` — list everything in the folder (used by `shipctl fetch` to mirror the whole folder into `.ship/cache/`).

Deprecated and removed in v0.x:

- `GET /manifest` — was a fan-out across kinds; recreate client-side as `Promise.all([list(patterns), list(workflows), …])` or as a release bundle.

## Migration plan

The plan is folded into the larger Wave 0–6 sequence already on the table; this RFC is the artifact for Wave 0.

| Wave | Days | Output                                                                                      |
|------|------|---------------------------------------------------------------------------------------------|
| 0    | 1–2  | This RFC + `documentation/rfc/rfc-0005-artifact-folder-spec-v2/inventory.csv` (60-row map). |
| 1    | 3–4  | `scripts/migrate_artifacts_v1_to_v2.py` populates `artifacts/<kind>/<id>/ARTIFACT.md` from old bodies and old manifest entries. Old manifests deleted. Old body locations replaced with stub redirect comments for one minor (or symlinked, if the OS allows). |
| 2    | 2–3  | Backend in-memory index, new routes, FS watch in dev. CLI: `shipctl fetch` mirrors a folder, `shipctl new <kind> <id>` scaffolds from `templates/<kind>/`, `shipctl verify` checks folder sha. Old `GET /manifest` removed. |
| 3    | 1–2  | `ship_artifact_check.py` v2: required frontmatter, description quality, sha drift, install_target reachable, id uniqueness across `artifacts/<kind>/`. CI workflow blocks PRs on drift. |
| 4    | 1–2  | Landing/Next reads from new layout; `/<kind>/<id>` shows examples, reference, tests, scripts as tabs. Authoring guide: `documentation/getting-started/authoring-artifacts.md`. |
| 5    | 1    | Telemetry events carry `kind/id/version/sha`; feedback drafts saved as `.ship/feedback/<kind>/<id>/draft.md`. RFC-0003 fixup. |
| 6    | 1    | Cleanup: archive old body locations, remove migration shims, declare v0.4.0. |

Total: ~8–10 working days for one engineer, or 4–6 with two parallel tracks (backend+lint vs CLI+landing).

## Interaction with prior RFCs

- **RFC-0001 (Artifacts protocol)**: superseded in part. The artifact kinds and version semantics stay; the manifest table moves into per-artifact frontmatter; the wire protocol gains `/files/<rel>` and `/tree`. RFC-0001 is amended in Wave 6 with a forward pointer to this RFC.
- **RFC-0002 (`shipctl` config)**: unchanged. `.ship/config.yml` and `.ship/cache/` keep their shape, but cache entries are folders, not single files.
- **RFC-0003 (Telemetry & feedback)**: telemetry event payloads gain `kind` and clarify `id`/`version`/`sha` semantics; `feedback.submit` gains `artifact_path` (folder-relative file the comment is about, e.g. `examples/foo.md`).
- **RFC-0004 (Adapters)**: unchanged. Adapters still consume frontmatter; `install_target` semantics are unchanged for the existing kinds.

## Out of scope for this RFC

- A new artifact kind beyond the existing four (`pattern`, `workflow`, `tool`, `collection`). Adding one is an RFC-0006-shaped follow-up and must satisfy the SKILL.md-style description bar from day one.
- An evals format. `tests/golden.yaml` is sketched here as a directory convention; the schema for evals is RFC-0007.
- A custom MCP surface. The methodology API stays HTTP. If a thin MCP wrapper is wanted, it is built later as a tiny adapter over the same routes.

## Open questions

- Should `artifacts/<kind>/<id>/` allow nested artifacts (e.g. a collection that physically contains its preset overlays)? Current answer: **no** — composition is by `id` reference under `spec.composes`, never by directory nesting. This keeps the cache uniform.
- Should `i18n/uk/ARTIFACT.md` carry its own `content_sha256` or roll up into the parent's? Current answer: **roll up** — translations track their parent and bump on the same release. A future RFC can split them if translation cadence demands it.
- Should removed/yanked artifacts leave a tombstone folder in git? Current answer: **yes** — the folder stays with `yanked: true` and a `tombstone: true` marker so `GET /api/<kind>/<id>` can return `410 Gone` without a database; pure git history of artifact ids stays linear.

## Acceptance

This RFC is `Proposed` until Wave 1's migration script lands and the first ten artifacts (the `cloud-agent/*` patterns plus the five workflows) round-trip through the new API end-to-end. At that point the RFC moves to `Accepted` and the remaining artifacts migrate without further discussion.
