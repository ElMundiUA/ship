# Protocol

Normative specifications for Ship. Each RFC describes one surface — the artifacts protocol, the config schema, telemetry, adapters, the on-disk folder layout. Accepted RFCs describe the intended design; the reference implementation in this repo converges on them but may lag any single RFC by one minor release.

Read this section when you need the contract, not the how-to. For "how do I…" recipes, see [Operating](/docs/operating); for vocabulary, [Concepts](/docs/concepts); for command syntax, [/cli](/cli).

RFCs live under `documentation/protocol/` and follow a fixed front-matter:

```
---
rfc: 0001
title: "..."
status: Accepted | Draft
created: YYYY-MM-DD
---
```

When an RFC is superseded, its status moves to `Superseded` and the replacement RFC is linked from its summary. When only part of an older RFC is replaced, the new RFC carries `supersedes_in_part: [rfc-XXXX]` in its front-matter.

## Index

| RFC | Title | Status | Summary |
|---|---|---|---|
| [0001](rfc-0001-artifacts-protocol.md) | Artifacts protocol | Accepted — partially superseded by 0005, 0007 (Phase 6), 0008 | Versioned artifacts served over HTTP, cached locally by `shipctl`, with semver + content hashes. |
| [0002](rfc-0002-shipctl-config.md) | `.ship/config.yml` schema | Accepted — v2 defined by 0007 | Standalone per-repository config; same schema regardless of language or stack. |
| [0003](rfc-0003-telemetry-and-feedback.md) | Telemetry and feedback | Accepted | Opt-in anonymous telemetry about artifact usage; client-drafted feedback becomes GitHub issues. |
| [0004](rfc-0004-adapters.md) | Adapters | Accepted | CI / tracker / agent / rules adapters as versioned artifacts, not bundled into `shipctl`. |
| [0005](rfc-0005-artifact-folder-spec-v2.md) | Artifact folder spec v2 | Accepted — partial (workflow retired by 0007 Phase 6) | Each artifact is a folder with `ARTIFACT.md` (frontmatter as single source of truth); catalog manifests removed from git; backend serves a live FS-derived index. |
| [0006](rfc-0006-cloud-platform-foundations.md) | Cloud platform foundations | Accepted | Multi-tenant `Org → Workspace → Project` model; git-as-source-of-truth for human-authored content; Postgres + pgvector everywhere (Neon for SaaS); `/v1` API alongside backwards-compatible methodology routes. |
| [0007](rfc-0007-lanes-and-run-agent.md) | Lanes-as-config and single `shipctl run` | Accepted — Phase 6 done | Replace the workflow-artifact layer with lane entries in `.ship/config.yml` (v2) and a single `shipctl run --lane` entry-point; reusable workflow in Ship; `artifact_kind=workflow` retired (Phase 6). |
| [0008](rfc-0008-catalog-reform.md) | Catalog reform — naming, modes, expansion | Accepted — Phase 0/1 shipped | Canonical `<category>-<name>` naming for all patterns; required `modes: [lane\|request]` metadata; retire `DefaultPipelineSpec`; Requests UI becomes a catalog picker; Phase-1 expansion of 10 new patterns. |
| [0009](rfc-0009-catalog-phase-2.md) | Catalog Phase-2 — beyond web & backend | Accepted — all 4 waves shipped | 50 new patterns across 8 packs (mobile, desktop, hardware, ML, games, infra/SRE, compliance, cross-cutting quality); 7 new presets (`mobile-app-deep`, `desktop-app`, `firmware`, `ml-project`, `platform`, `regulated`, `game`); 4 rollout waves. |
| [0010](rfc-0010-plays-and-inbox.md) | Plays, Automations, Runs, Inbox — operator IA | Accepted — Phases 1–6 shipped; partially supersedes 0008 | Reframes the console around four operator-facing surfaces (Plays · Automations · Runs · Inbox), collapses fleet/per-repo navigation duplication, and introduces a single attention surface with handle-based routing, operational groups, and typed dispositions. Internal `lane`/`pattern`/`workflow` vocabulary preserved. |

RFC-0001 and RFC-0005 are partially superseded by
[RFC-0007](rfc-0007-lanes-and-run-agent.md) Phase 6
(`artifact_kind=workflow` retired) and
[RFC-0008](rfc-0008-catalog-reform.md) (pattern metadata and
`<category>-<name>` naming). RFC-0008 is in turn partially
superseded by [RFC-0010](rfc-0010-plays-and-inbox.md) for
user-facing terminology and IA only — the catalog contract,
naming convention, and pattern frontmatter additions from 0008 all
stand. See each RFC's top-of-file status block for the current
picture.
