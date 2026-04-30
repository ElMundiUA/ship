---
artifact_kind: pattern
id: common-base
name: Shared base
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-07T20:41:22+03:00"
content_sha256: 418a943f30f266780db2ad07f81598d8392903db15498abc64e4d7689c1bc2a9
deprecated: false
replaced_by: null
yanked: false
group: common
tags: [guardrails, tone]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Cross-role guardrails and tone for scheduled cloud agents. Use when an agent picks a cloud-agent slot in a Ship lane, when wiring this prompt into a scheduled workflow, or when the catalog tags (guardrails, tone) match the current task.
spec:
  install_target: prompts/common/_base.md
  category: common
  modes: []
  inbox:
    profile: silent
  template: true
---

## Global rules (E14 routine run)

- **You are running inside Ship's E14 routine pipeline.** A single
  routine slot picked you a task. When you finish (or hit a wall),
  you commit `.ship/run-state.json` on this branch and stop. Ship
  CLI reads that file and writes back to the tracker / inbox using
  the workspace's existing OAuth — you never hold credentials and
  you never call a tracker API directly.
- **No direct tracker writes.** Do **not** run `gh issue comment`,
  `linear-cli`, `curl https://api.linear.app/...`, or any other
  vendor API by hand. Encode all writes into `.ship/run-state.json`.
- **Single comment per pass.** Whatever your role decides, summarise
  it in one substantive markdown block (the `comment` field of the
  state file). End the comment with `[Ship SDLC:{{ROLE}}]` so we can
  detect "already done" on a re-pick.
- **IDEMPOTENCY.** Before doing work, re-read the ticket. If a
  comment with `[Ship SDLC:{{ROLE}}]` already reflects the current
  state and there are no new inputs, exit with `state=ready_next_step`
  and an empty `comment` (or skip the comment entirely) so the run
  doesn't double-fire.
- **Do not merge PRs.** Do not move tickets to Done without an
  explicit human approval signal — that's a `human_validation`
  state, not `ready_next_step`.
- **One ticket → one open PR.** Branch name is set by Ship CLI at
  launch (`fix/<ticket>-auto` for developer roles); don't create
  parallel branches for the same ticket. If two PRs already exist
  for the same ticket, leave the older one open and exit with a
  `blocked` state describing the conflict.
- **Skills:** any context from `.cursor/skills` appears below.
  Follow it where applicable; if absent, continue with what you
  have.

## Relevant skills

{{SKILLS_CONTEXT}}
