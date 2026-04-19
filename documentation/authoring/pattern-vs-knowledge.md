# Pattern vs knowledge — when each shape applies

> **Status: draft.** The `knowledge` artifact kind is not yet shipped. This
> page captures the editorial rubric we will use the moment it lands, so that
> existing patterns can be audited against the same line and so that a new
> contributor reaches for the right shape on the first try. The
> backend-side spec (folder layout, frontmatter, API surface) will land as
> a separate RFC; cross-link from there once it exists.

The catalog already has four artifact kinds (`pattern`, `tool`, `workflow`,
`collection`). A fifth kind, `knowledge`, is being added to host **reference
material** that patterns, workflows, and tools want to consult lazily —
brand books, PDF generation guidelines, code-style standards, internal
runbooks, prior decisions, and so on. The risk in introducing it is the
opposite risk we already have: instead of stuffing reference material into
patterns (where it does not belong), authors may start writing patterns
that are really just knowledge with a verb on top. This page is the line
between the two.

## One-line definition

- **Pattern** — a *method*. A short, universal procedure an agent runs
  when a trigger fires. **HOW** to do something. Few of them, slow to
  change, owned by Ship core.
- **Knowledge** — *content*. Facts, rules, references, brand assets,
  examples that an agent (or a pattern) reads when it needs to know
  something specific. **WHAT** is true in this project. Many of them,
  fast to change, owned by the project that imported them.

A pattern is the recipe; knowledge is the pantry it walks to when an
ingredient is unfamiliar.

## The 90-second decision rubric

| Question                                                                 | Pattern                                | Knowledge                                |
|--------------------------------------------------------------------------|----------------------------------------|------------------------------------------|
| What does the artifact answer?                                           | "How do I do X?"                       | "What is true about Y here?"             |
| Who is the audience?                                                     | An agent at decision time              | An agent looking something up            |
| Is it a procedure with steps and a success criterion?                    | Yes                                    | No                                       |
| Does it stay valid across many projects unchanged?                       | Yes                                    | No — it describes one project / domain   |
| Does it fire from a trigger (state, label, event, role pick)?            | Yes                                    | No — it is read on demand                |
| Should adopting Ship in a new repo bring it along by default?            | Yes (via preset)                       | No (only if the repo opts in / harvests) |
| How often does its content change in steady state?                       | Quarters                               | Weeks or days                            |
| Can two competing versions coexist for the same audience?                | No (it is a method)                    | Yes (different scopes, frameworks)       |
| Is it correct to say "this is documentation"?                            | No (it is an instruction)              | Yes                                      |

If the answer column flips between the two for the same artifact, you
probably have one pattern *plus* one or more knowledge buckets it should
reference — split them.

## Litmus test (three questions)

Before you write a new pattern, answer these three. If **all** answers
are "yes", it is a pattern. If any answer is "no", it is knowledge — or a
pattern hiding a knowledge bucket inside it.

1. **Trigger:** can you name the moment the agent reaches for it? ("When
   the role is `developer`", "When `intent: cron` fires", "When the
   ticket leaves Ready".) If the trigger is "any time the agent is
   thinking about brand colours", that is a knowledge lookup, not a
   pattern fire.
2. **Steps:** does it list a small, ordered procedure that ends in a
   concrete artifact (PR, comment, file write, label move)? "Read these
   guidelines and use your judgement" is not steps; that is reference.
3. **Success criterion:** is there a check `shipctl verify` (or a
   reviewer) can apply that says *this pattern was followed*? "The PR
   has the evidence comment with marker `ship-managed:evidence`" is a
   criterion. "The output looks on-brand" is not.

A pattern that fails the trigger / steps / success-criterion test is
probably documentation. Move the body to a knowledge bucket and let
patterns reference it by topic when they need it.

## Audit examples

These are concrete cases from today's catalog (or close paraphrases).
Use them to calibrate.

| Today                                                                    | Verdict       | Where it should live                                                                  |
|--------------------------------------------------------------------------|---------------|---------------------------------------------------------------------------------------|
| `cloud-developer` — branch contract, PR shape, evidence comment marker   | Pattern       | Stays as `pattern:cloud-developer`                                                    |
| `scheduled-sdlc-lane` — pick scripts, concurrency groups, evidence types | Workflow      | Stays as `workflow:scheduled-sdlc-lane`                                               |
| `web-design-guidelines` — colour tokens, type scale, motion rules        | **Knowledge** | New bucket `knowledge:web-design-guidelines`, scope `presentation`, advisory          |
| `pdf-generation-handbook` — print bleed, embedded fonts, paginated TOC   | **Knowledge** | New bucket `knowledge:pdf-generation`, scope `delivery`, mandatory when generating PDFs |
| `brand-book-elmundi` — logo lockups, voice, do/don't pairs               | **Knowledge** | New bucket `knowledge:brand-elmundi`, scope `presentation`, mandatory when public-facing |
| `cloud-base` — guardrails the developer pattern interpolates as `{{BASE}}` | Pattern (template fragment) | Stays — it is composed at runtime, not consulted by name |
| Hypothetical `pattern:write-good-copy`                                   | Smell         | Probably a knowledge bucket on tone + a pattern with a clear trigger ("when authoring landing copy") |

The smell to look for: a "pattern" whose body is a long list of "always
do X, never do Y" without a trigger or a step list. That is a knowledge
bucket misfiled.

## Knowledge bucket shape (preview)

The colleague's RFC will own the normative spec. The shape we are
designing the rubric against today:

```yaml
artifact_kind: knowledge
id: web-design-guidelines
name: Web design guidelines (ElMundi)
version: 0.3.1
channel: stable
spec:
  enforcement: advisory          # advisory | required
  topics: [presentation, web]    # small controlled vocabulary, see below
  scope: project                 # project | org | universal
  applies_to:                    # optional, narrows when 'required' fires
    surfaces: [marketing-site, docs-site]
  provenance:                    # set by `shipctl init` when harvested
    source: documentation/brand/web.md
    imported_at: "2026-04-19T10:00:00Z"
    drift_check: sha256
```

Two enforcement levels matter for how patterns reach for them:

- **`advisory`** — opportunistic. A pattern can ignore the bucket; it
  reads it only when it would otherwise have to invent something
  ("I don't know how to render the PDF cover, let me check
  `knowledge:pdf-generation`"). The default for harvested material.
- **`required`** — pre-flight. A pattern that touches a matching topic
  *must* read the bucket and acknowledge it. Reserved for material the
  business cannot override silently: brand, regulated copy, security
  rules. Promoting a bucket from `advisory` to `required` is a
  deliberate act, not a default.

`topics` is the join key between patterns and knowledge. Keep the
vocabulary small (10–20 entries) and centrally curated; if every project
invents its own topics, lazy reference becomes lazy guessing. Today's
candidate list — to be ratified in the knowledge RFC:

`presentation`, `delivery`, `branding`, `code-style`, `architecture`,
`security`, `compliance`, `data`, `ops`, `support`, `legal`,
`accessibility`, `performance`, `localization`, `infra`.

## How a pattern reaches knowledge

Patterns do not embed knowledge; they declare which topics they may
need and let the runtime resolve the relevant buckets:

```yaml
spec:
  install_target: prompts/cloud-agent/developer.md
  knowledge_topics: [code-style, architecture]   # optional
```

At runtime, the agent runner exposes the matching buckets to the
pattern body via the same `{{KNOWLEDGE.<topic>}}` interpolation a
template fragment uses today. A pattern may also resolve a bucket by id
if it knows it must (e.g. PDF generation knows it needs
`knowledge:pdf-generation`).

The result: one universal `pattern:cloud-developer` ships in every Ship
install, but its rendered prompt in your repo composes with whichever
project-specific knowledge buckets the topic match brings in. **No more
"web-design-guidelines pattern" per project.**

## Harvesting at `shipctl init`

The `init` flow gains a knowledge-harvest step that scans an existing
project for files that look like reference material (`docs/`, `brand/`,
`design-system/`, `style-guides/`, top-level `STYLE.md` /
`CONTRIBUTING.md`, etc.) and *proposes* knowledge buckets to import.
Three rules to keep this honest:

1. **Staged, not active by default.** Harvested files land in
   `.ship/cache/knowledge/<id>/` with `enforcement: advisory` and
   `channel: experimental`. Adopting them into the live config is a
   second step the operator confirms.
2. **Provenance, then drift.** Each bucket records its import source
   path and a content hash. `shipctl verify` flags drift between the
   bucket and the source file so the bucket does not silently fork
   from the project's living docs.
3. **Human in the loop on clustering.** LLM-based topic classification
   is allowed but the operator approves the resulting topic / scope /
   enforcement before the bucket is published. No silent enforcement
   promotions, ever.

This is the path that lets a freshly adopted repo go from "Ship has no
idea what we look like" to "the developer pattern composes with our
brand and code-style buckets" without anyone authoring a pattern.

## Quality gate: avoid pattern bloat

The whole point of introducing knowledge is so we keep `pattern` count
**bounded**. Concrete bar to clear before merging a new pattern:

- It passes the three-question litmus test above.
- The body is **method**, not **content** — if you can copy whole
  paragraphs into a wiki page and they still read fine, those
  paragraphs are knowledge.
- It is reusable across at least two unrelated repos in current or
  realistic future usage. (Patterns that exist for one team only are
  knowledge wearing a costume.)
- It either fires from an existing trigger surface (role / state /
  label / event / `intent`) or proposes adding one in the same PR.
- Its `description` carries a *what* and a *when* — same rule as today.

If the new pattern fails any of these, the right move is to land a
knowledge bucket and (optionally) extend an existing pattern's
`knowledge_topics` to consult it.

## Open questions for the knowledge RFC

The colleague's RFC will own these; flagged here so the rubric and the
spec land on the same page.

- Is `knowledge` a fifth top-level kind, or a `subkind` under
  `collection`? (The author's lean: top-level kind, because the
  consumption model is different — `collection` *bundles*, `knowledge`
  *informs*.)
- How does `shipctl verify` express a `required` knowledge bucket
  whose source has drifted? Hard fail, soft warn, or a new check
  category?
- Are buckets pinned per-repo (`artifacts.pins`) or per-pattern
  (`spec.knowledge_topics` widens the search, pins narrow it)? Both
  is fine; we should pick the precedence rule on day one.
- Localisation: does a bucket live in one language or in `i18n/`
  siblings the way `ARTIFACT.md` does? The harvest step will produce
  whatever the source repo had — the spec needs to say what the API
  serves.
- Telemetry: a `knowledge.read` event would tell us which buckets
  patterns actually consult. Cheap to add, very useful for retiring
  dead buckets.

## Where to next

- The general authoring contract: [Authoring artifacts](/docs/authoring).
- Vocabulary: [Concepts](/docs/concepts).
- Folder + frontmatter normative shape:
  [RFC-0005](/docs/protocol/rfc-0005-artifact-folder-spec-v2) — the
  knowledge RFC will extend the same envelope.
