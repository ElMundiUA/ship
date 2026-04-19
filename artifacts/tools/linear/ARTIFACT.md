---
artifact_kind: tool
id: linear
name: Linear
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-12T04:11:35+03:00"
content_sha256: eaad8383e82daaa0d877b276035118e9afff0fd5bffade5a989095ab9a06d611
deprecated: false
replaced_by: null
yanked: false
group: tracker
tags: [graphql, projects, states]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Tracker as system of record — projects, states, labels, evidence for agents. Use when integrating this surface into a Ship setup, when evaluating vendor neutrality for a procurement, or when an adapter under tracker needs to call into it.
spec:
  capability: tracker
  install_target: documentation/tools/integrations/linear.md
---

# Linear (tracker)

**Role in Ship:** system of record for the delivery queue — states, labels, comments, and evidence the agent and humans share.

## What you wire

- **Projects** — e.g. SDLC lane vs audit boards; keep automation lanes separate so intake never steals audit throughput.
- **States** — map your columns to Ship semantics (`Backlog` → `Todo` → `In Progress` → `In Review` → `Done`, plus `Blocked`). Document the mapping in adoption notes.
- **Labels / fields** — `ready:*`, `stage:*`, `result:*`, QA splits; if you rename, keep the *meaning* stable (see tracker contract below).

## Agent touchpoints

- Pick scripts and workflows assume **machine-readable** issue metadata (GraphQL or equivalent in your adapter).
- Cloud agents may **comment** and **transition** issues only where your policy allows; every move should cite a PR URL, CI run, or test artifact where possible.

## Read next

- [Tracker adaptation contract](/docs/tools/ship-agent-trackers) — vendor-neutral interface your tracker must satisfy.
- [Delivery, quality & release](/docs/operating) — gates and evidence habits.
- [Agent launch matrix](/docs/agent-matrix) — where Linear fits in runtime choices.

For a **reference** cron grid and YAML names (ElMundi-style), see [Examples → ElMundi](/use-cases/elmundi) — treat names and URLs as templates, not requirements.
