---
artifact_kind: collection
id: preset-marketing
name: Preset — Marketing site
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-21T00:00:00+00:00"
content_sha256: ded2d40b4c7c7479f16f492ca2737de04f5d316e8c4033d0600de3fc61ebecf5
deprecated: false
replaced_by: null
yanked: false
group: preset
tags: [preset, marketing, content, landing]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Preset for marketing / content-first repositories: landing pages,
  documentation sites, blog and campaign microsites. Optimised for
  copy review, content cadence, and site-structure mapping. Use when
  bootstrapping a Ship project whose primary output is pages (not a
  shipping product runtime).
spec:
  subkind: preset
  compatible_trackers: [linear, jira, github-issues, notion]
  compatible_ci: [gh-actions, gitlab-ci, netlify, vercel, manual]
  compatible_agents: [cursor, codex, claude, copilot]
  required_tools: [tool/tracker/<current>, tool/ci/<current>, collection/agent-rules-<agent>]
  optional_tools: [tool/preview/vercel, tool/preview/netlify, tool/cms/contentful, tool/cms/sanity]
  addendums: "[]   # preset itself declares no addendum; user opts in separately"
  preset_id: marketing
  install_target: documentation/collections/preset-marketing.md
---

# Preset — Marketing site

## Product shape

Content-first repository: landing pages, documentation sites,
microsites, blogs, campaign pages, or a marketing component library.
The bounded context is **"the story we tell on the web"** — copy,
imagery, SEO metadata, content schemas, and the publishing calendar.

Unlike the `web-app` preset, the *product* isn't a long-lived user
session; it's the page itself. Reviews are copy-heavy, release cycles
are fast and chatty, and regressions usually mean broken links, stale
pricing, or off-brand tone — not runtime crashes. The pipelines below
reflect that asymmetry.

## Default Ship pipelines (seed on activate)

Picking this preset wires a copy-and-cadence-first lane grid:

- `pr_review` — PR gate tuned for copy and component changes.
  Blocks on broken links, missing alt text, and obvious tone drift.
- `daily_standup` — scheduled content-cadence digest (what went
  live yesterday, what's queued for today, what's blocked on review).
- `code_map` — refresh the site-structure map so Ship can answer
  "where does this claim live?" when marketing asks to update copy.

`tech_debt` and `self_heal` are intentionally **off** by default —
marketing sites rarely have deep refactor lanes, and self-heal is
noisier than useful on repos where every push is a copy tweak.
Tenants flip those on from the Pipelines page whenever they want.

## SDLC columns the preset expects

- `Idea → Drafted → In Review → Approved → Published → Archived`
- `Blocked` as a parallel state for legal / brand / compliance holds.
- `Staged` checkpoint between `Approved` and `Published` when a
  CMS or preview deploy needs to settle before the piece goes live.

## Label contract (preset-specific)

- `content:copy` — copy-only change (no code touched).
- `content:visual` — imagery, layout, or asset change.
- `content:seo` — metadata, structured data, or canonical change.
- `content:legal-hold` — awaiting legal / compliance sign-off.
- `content:translation-pending` — awaiting localisation pass.
- `campaign:<slug>` — ties the page to a campaign for rollup reports.
- Plus the base Ship labels (`type:*`, `lane:*`, `promote:*`).

## CI stages (pseudocode)

```
on: pull_request
jobs:
  install:        # cache deps (static-site generator)
  build:          # Next.js / Astro / Eleventy / Docusaurus build
  link-check:     # broken internal + external links
  html-lint:      # a11y + metadata + structured-data sanity
  preview:        # Vercel / Netlify / Amplify preview deploy
  copy-review:    # Ship's copy reviewer pass (PR review pipeline)
  doctor:         # shipctl doctor (artifact pins, labels)
```

The preview URL is the key artifact: reviewers, legal, and the
campaign owner all land on the same link for sign-off, with Ship's
copy-review pipeline posting inline notes on the PR.

## Evidence types

- Preview URL in the PR body, re-posted on every push.
- Copy-review summary: tone drift hits, broken link count, missing
  alt text, SEO warnings.
- Staging URL for post-approval visual QA.
- Publish receipt (CMS sync id or deploy id) on merge to main.

## Promote gates

`preview green → copy-review clean → legal sign-off (if flagged) →
PR approved → main merge → staging deploy → CMS publish → CDN
purge`.

Each gate writes a line into the ticket: preview URL, copy-review
run id, legal reviewer, staging URL, CMS publish id.

## Required secrets (generic names)

- Tracker API key (Linear / Jira / GitHub Issues / Notion).
- CI token for the bot user.
- Preview host token (Vercel / Netlify / Amplify).
- CMS token (Contentful / Sanity / Prismic), if the site pulls
  content at build.
- Analytics write key (PostHog / Plausible / GA4), if Ship should
  annotate rollouts with campaign tags.

## Recommended addendums

- None required by default. Add `addendum-pharma` if the site
  publishes regulated medical claims; `addendum-fin` if it
  publishes regulated financial claims — both layer legal-hold
  gates on top of the base preset.

Addendums layer on top of this preset; they never relax a rule
the base preset enforced.
