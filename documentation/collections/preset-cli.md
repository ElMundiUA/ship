---
artifact_kind: collection
subkind: preset
preset_id: cli
compatible_trackers: [linear, jira, github-issues]
compatible_ci: [gh-actions, gitlab-ci, circleci, azure-pipelines, manual]
compatible_agents: [cursor, codex, claude, aider, copilot]
required_tools: [tool/tracker/<current>, tool/ci/<current>, collection/agent-rules-<agent>]
optional_tools: [tool/release/goreleaser, tool/release/changesets, tool/release/semantic-release]
addendums: []   # preset itself declares no addendum; user opts in separately
min_shipctl: "0.3.0"
---

# Preset — Developer CLI / SDK

## Product shape

Developer CLI or SDK — Node / Go / Rust / Python package
consumed by other developers via a registry (npm, PyPI,
crates.io, Go proxy) or as a raw binary. Bounded context is
**"the install + invocation"**: a user runs `foo --bar` and
expects the exit code, stdout, and stderr to be contract.

## SDLC columns the preset expects

- `Backlog → Todo → In Progress → In Review → Done`
- `Blocked` as a parallel state.
- Optional `Release Candidate` column between `Done` and a
  final `Released` marker: a change sits in `Release
  Candidate` while a prerelease (npm `next`, PyPI
  `rc`-suffixed, Go pre-release tag) bakes.

## Label contract (preset-specific)

- `cli:breaking` — flag, subcommand, or exit-code change
  that requires a MAJOR bump.
- `cli:additive` — new subcommand or optional flag.
- `platform:linux` / `platform:darwin` / `platform:windows` —
  when a change is platform-specific.
- `release:rc` — change needs prerelease verification before
  stable publish.
- Plus the base Ship labels.

## CI stages (pseudocode)

```
on: pull_request
jobs:
  install:
  lint-typecheck:
  unit:
  cross-build:     # matrix across linux/darwin/windows targets
  golden-tests:    # frozen stdout/stderr/exit-code fixtures
  fuzz:            # short fuzz run (optional but encouraged)
  doctor:
on: push tags
jobs:
  release:         # semantic-release / goreleaser / changesets
  publish:         # npm publish / pypi upload / crates publish
  smoke:            # pull from registry and run --version
```

## Evidence types

- Cross-build matrix result (all target OS/arch pairs green).
- Golden-test diff (byte-exact stdout/stderr for happy-path
  fixtures).
- Release notes + changelog entry.
- Registry smoke verification (post-publish install + invoke).

## Promote gates

`cross-build green → golden-tests green → RC publish →
real-user install smoke → stable publish → release note`.

Any `cli:breaking` label forces a MAJOR bump and a
documented deprecation notice in the release notes.

## Required secrets (generic names)

- Tracker API key.
- CI token for the bot user.
- Registry publish token (npm automation token / PyPI API
  token / crates.io token / Go module proxy, if private).
- Code-signing certificates for signed binaries (Apple
  Developer ID, Windows Authenticode), if distributed as
  binaries.
- GPG key for tag signing (recommended).

## Recommended addendums

- `addendum-pharma` — rarely applicable to a CLI, but if the
  CLI operates on PHI inputs (e.g. ETL for health data),
  apply it.
- `addendum-fin` — if the CLI handles payment data or
  regulated artifacts.

Most CLIs ship without any addendum.
