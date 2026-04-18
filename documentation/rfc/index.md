# RFCs

Request-for-Comments documents describe Ship's protocols, configuration surfaces, and extension points. Each RFC is self-contained and dated. Accepted RFCs describe the intended design; they do not automatically reflect the state of the reference implementation, but the reference implementation should converge on them.

RFCs live under `documentation/rfc/` and follow a fixed front-matter:

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
| [0001](rfc-0001-artifacts-protocol.md) | Artifacts protocol | Accepted (amended by 0005) | Versioned artifacts served over HTTP, cached locally by `shipctl`, with semver + content hashes. |
| [0002](rfc-0002-shipctl-config.md) | `.ship/config.yml` schema | Accepted | Standalone per-repository config; same schema regardless of language or stack. |
| [0003](rfc-0003-telemetry-and-feedback.md) | Telemetry and feedback | Accepted | Opt-in anonymous telemetry about artifact usage; client-drafted feedback becomes GitHub issues. |
| [0004](rfc-0004-adapters.md) | Adapters | Accepted | CI / tracker / agent / rules adapters as versioned artifacts, not bundled into `shipctl`. |
| [0005](rfc-0005-artifact-folder-spec-v2.md) | Artifact folder spec v2 | Proposed | Each artifact is a folder with `ARTIFACT.md` (frontmatter as single source of truth); catalog manifests removed from git; backend serves a live FS-derived index. |
