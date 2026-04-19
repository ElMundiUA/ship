# Knowledge artifact kind — task source

Captured from a frontend / methodology design conversation on 2026-04-19.
This file is a **task source** for the colleague who is shipping the new
`knowledge` artifact kind on the backend. It is not a spec; it records
goals, the editorial line we are committing to, and the conditions the
backend implementation has to honour so the kind does not become a
second `pattern` in disguise.

The matching editorial rubric (the one that operators will read) lives at
[`documentation/authoring/pattern-vs-knowledge.md`](../../documentation/authoring/pattern-vs-knowledge.md)
and ships at [/docs/authoring/pattern-vs-knowledge](https://ship.elmundi.com/docs/authoring/pattern-vs-knowledge)
once landed. Keep the two in sync — if the RFC changes the shape, update
the rubric in the same PR, and vice versa.

## Why we are introducing `knowledge`

We have four artifact kinds today: `pattern`, `tool`, `workflow`,
`collection`. Several existing patterns are not patterns at all — they
are reference material wearing the pattern envelope. Concrete smell:
`web-design-guidelines` reads like brand documentation, not a procedure
with a trigger. The risk of leaving it as a pattern is two-fold:

1. **Pattern proliferation.** Every new project will add its own
   "pattern" for its design rules, its PDF generation handbook, its
   internal voice guide, etc. Patterns become a wiki.
2. **Wrong audience and lifecycle.** Patterns are universal, slow to
   change, owned by Ship core. Reference material is project-specific,
   fast to change, owned by the project. Forcing the second into the
   first means either Ship core owns content it has no business owning,
   or the project forks the pattern (and Ship loses upgrade leverage).

The fifth kind `knowledge` exists to host that reference material as a
first-class artifact, so patterns stay small and procedural and
projects own their own content explicitly.

## User goals (verbatim, paraphrased to English)

The two goals stated by the operator who triggered this work:

1. **Init can harvest.** The `shipctl init` flow should be able to walk
   an existing project and collect what is already there — design
   standards, code standards, brand books, runbooks — into knowledge
   buckets, so a freshly adopted Ship repo immediately knows the local
   rules without anyone authoring a pattern.
2. **Quality, not quantity.** We do not want 1000 patterns. The
   methodology surface stays small (universal patterns); the
   project-specific surface grows in knowledge buckets. Pattern bloat
   is the failure mode we are designing against.

## The editorial line (recap)

This is the line the rubric makes operators commit to. The backend
spec must keep both shapes distinguishable on the wire so this line is
enforceable.

- **Pattern = method.** HOW to do something. Trigger + steps + success
  criterion. Universal. Few. Owned by Ship core.
- **Knowledge = content.** WHAT is true here. Facts, rules, references.
  Project-specific. Many. Owned by the project that imported it.

If you can copy paragraphs of a pattern body into a wiki page and they
still read fine, those paragraphs are knowledge.

## Proposed shape (open for the RFC)

The frontmatter shape we are designing the rubric against. Treat as a
strawman; the RFC owns the canonical version.

```yaml
artifact_kind: knowledge
id: web-design-guidelines
name: Web design guidelines (ElMundi)
version: 0.3.1
channel: stable
spec:
  enforcement: advisory          # advisory | required
  topics: [presentation, web]    # small controlled vocabulary (see below)
  scope: project                 # project | org | universal
  applies_to:                    # optional, narrows when 'required' fires
    surfaces: [marketing-site, docs-site]
  provenance:                    # set by `shipctl init` when harvested
    source: documentation/brand/web.md
    imported_at: "2026-04-19T10:00:00Z"
    drift_check: sha256
```

Two enforcement levels:

- **`advisory`** — opportunistic. A pattern may consult the bucket; it
  is never required to. Default for harvested material.
- **`required`** — pre-flight. A pattern that touches a matching topic
  must read and acknowledge the bucket. Promoting from `advisory` to
  `required` is a deliberate operator action, not a default.

`topics` is the join key between patterns and knowledge. Keep the
vocabulary small (10–20 entries) and centrally curated. Strawman list:
`presentation`, `delivery`, `branding`, `code-style`, `architecture`,
`security`, `compliance`, `data`, `ops`, `support`, `legal`,
`accessibility`, `performance`, `localization`, `infra`.

Patterns that opt into knowledge consultation:

```yaml
# pattern frontmatter
spec:
  install_target: prompts/cloud-agent/developer.md
  knowledge_topics: [code-style, architecture]   # optional, additive
```

Runtime exposes matching buckets to the pattern body via
`{{KNOWLEDGE.<topic>}}` (same shape as today's `{{BASE}}` interpolation
for `cloud-base`).

## Conditions for success (non-negotiable)

The kind ships only if these hold; otherwise it becomes a fancy
documentation drop and reproduces the patterns problem at one level
of indirection.

1. **Top-level kind, not a `subkind` of `collection`.** Consumption
   model is different: `collection` *bundles* artifacts; `knowledge`
   *informs* a pattern's runtime context. Conflating them muddles the
   API and the catalog UI.
2. **Harvest is staged.** Files imported by `shipctl init` land in
   `.ship/cache/knowledge/<id>/` with `enforcement: advisory` and
   `channel: experimental`. Activation into the live config is a
   second confirmation step. **Never auto-promote to `required`.**
3. **Provenance + drift detection.** Every harvested bucket records
   `provenance.source` and a content hash; `shipctl verify` flags
   drift between the bucket and the source path so the bucket cannot
   silently fork from the project's living docs.
4. **Human-in-the-loop classification.** LLM-assisted topic /
   enforcement / scope assignment is fine, but the operator approves
   the classification before the bucket is published. No silent
   enforcement promotions.
5. **Topic vocabulary is closed.** Adding a new topic is an RFC-level
   change (or at least a guarded addition with a deprecation policy).
   If projects can mint topics freely, the join surface decays into
   string matching.
6. **`description` rules carry over.** Same SKILL.md-style rule as
   patterns: third person, what + when, ≤ 1024 chars. The discovery
   surface has to be uniform across kinds.
7. **Bucket lifecycle is observable.** Add a `knowledge.read`
   telemetry event (RFC-0003) so we can retire dead buckets and see
   which patterns actually consult which buckets in practice.

## Open questions for the RFC

These are the points the editorial side cannot decide for the backend.

- Pin precedence: `artifacts.pins` per-repo vs `spec.knowledge_topics`
  per-pattern. Probably both — needs an explicit precedence rule on
  day one.
- `required` drift: hard-fail on `shipctl verify`, soft-warn, or a new
  check category? Recommendation: hard-fail, because the whole point
  of `required` is that the operator opted into enforcement.
- Localisation: one bucket per language, or `i18n/` siblings the way
  `ARTIFACT.md` does it today? The harvest step will pass through
  whatever the source repo has, so the spec needs to define what the
  API serves.
- Folder layout: same `artifacts/<kind>/<id>/ARTIFACT.md` envelope as
  RFC-0005, or does knowledge get to ship multiple body files (think
  brand book with image assets)? The author leans on "same envelope,
  with `assets/` and `examples/` siblings as RFC-0005 already permits".
- Catalog URL: `/knowledge` as a fifth top-nav slot, or does it live
  under the existing `/kit` hub alongside Patterns / Workflows /
  Collections / Tools? The author leans on "under `/kit`" so the top
  nav stays at four items (Use cases · Get started · Kit · CLI), but
  the knowledge cards on `/kit` get visual differentiation.
- API surface: `GET /knowledge`, `GET /knowledge/{id}`,
  `POST /knowledge/search` (topic + scope filters)? Mirroring the
  pattern endpoints keeps the CLI simple.

## What the frontend will need from the backend

Not blockers for the kind landing; flagged so we can stage the work.

- `GET /knowledge` and `GET /knowledge/{id}` mirroring the pattern
  endpoints, so `/kit` can list buckets and a detail page can render
  the body with the same `DocsMarkdown` renderer.
- `topics` and `enforcement` exposed in the list response so the
  catalog filter chips work without fetching every body.
- `provenance.source` exposed in the detail response so the UI can
  show "Imported from `documentation/brand/web.md` on 2026-04-19".
- Consistent `description` quality so the catalog cards do not look
  empty next to the pattern cards.

## Audit work that follows the kind landing

Once the kind ships, we owe the catalog an audit. Initial suspects to
re-classify (verdict in the rubric):

- `web-design-guidelines` → `knowledge:web-design-guidelines`
  (`presentation`, advisory).
- Any "PDF generation handbook" / brand book content currently filed
  as a pattern → knowledge buckets, scoped `delivery` /
  `presentation`, mostly mandatory when the surface matches.
- Any pattern whose body is mostly "always do X, never do Y" without
  a trigger → split into one (small) pattern + one knowledge bucket.

The audit lands as a separate PR after the knowledge kind has at least
one release cycle of operator feedback on `channel: edge`.

## Related

- Editorial rubric (operator-facing): [`/docs/authoring/pattern-vs-knowledge`](../../documentation/authoring/pattern-vs-knowledge.md).
- Today's authoring contract: [`/docs/authoring`](../../documentation/authoring.md).
- Folder + frontmatter envelope to extend: [RFC-0005](../../documentation/protocol/rfc-0005-artifact-folder-spec-v2.md).
- Telemetry envelope (for the `knowledge.read` event): [RFC-0003](../../documentation/protocol/rfc-0003-telemetry-and-feedback.md).
- Concepts glossary that will need a `Knowledge` entry once the kind ships: [`/docs/concepts`](../../documentation/concepts.md).
