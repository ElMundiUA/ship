# Pattern vs knowledge — when each shape applies

Knowledge buckets shipped in April 2026 (see
[Knowledge buckets](/docs/knowledge-buckets) for the operator surface).
The editorial line below — what belongs in a **pattern** vs a
**knowledge bucket** — is now the first decision a contributor should
make, because stuffing reference material into patterns is still the
easiest way to bloat the catalog, and filing procedures as buckets is
an equally real new failure mode.

## One-line definition

- **Pattern** — a *method*. A short, universal procedure an agent runs
  when a trigger fires. **HOW** to do something. Few of them, slow to
  change, owned by Ship core.
- **Knowledge bucket** — *content*. Facts, rules, references, brand
  assets, examples that an agent (or a pattern) reads when it needs to
  know something specific. **WHAT** is true in this project / repo /
  user overlay. Many of them, fast to change, owned by the workspace
  that imported them.

A pattern is the recipe; the knowledge bucket is the pantry it walks
to when an ingredient is unfamiliar.

## The 90-second decision rubric

| Question                                                                 | Pattern                                | Knowledge bucket                         |
|--------------------------------------------------------------------------|----------------------------------------|------------------------------------------|
| What does the artifact answer?                                           | "How do I do X?"                       | "What is true about Y here?"             |
| Who is the audience?                                                     | An agent at decision time              | An agent looking something up            |
| Is it a procedure with steps and a success criterion?                    | Yes                                    | No                                       |
| Does it stay valid across many projects unchanged?                       | Yes                                    | No — it describes one workspace / repo   |
| Does it fire from a trigger (state, label, event, role pick)?            | Yes                                    | No — it is read on demand                |
| Should adopting Ship in a new repo bring it along by default?            | Yes (via preset + `enabled_on_install`)| No (harvested or uploaded per workspace) |
| How often does its content change in steady state?                       | Quarters                               | Weeks or days                            |
| Can two competing versions coexist for the same audience?                | No (it is a method)                    | Yes — scopes ladder through `workspace → project → repo → user` |
| Is it correct to say "this is documentation"?                            | No (it is an instruction)              | Yes                                      |

If the answer column flips between the two for the same artifact, you
probably have one pattern *plus* one or more knowledge buckets it
should reference — split them.

## Litmus test (three questions)

Before you write a new pattern, answer these three. If **all** answers
are "yes", it is a pattern. If any answer is "no", it is a knowledge
bucket — or a pattern hiding a bucket inside it.

1. **Trigger:** can you name the moment the agent reaches for it? ("When
   the role is `developer`", "When the weekly `scan-tech-debt` lane
   fires", "When an `issues.labeled` event matches `ready:developer`".)
   If the trigger is "any time the agent is thinking about brand
   colours", that is a bucket lookup, not a pattern fire.
2. **Steps:** does it list a small, ordered procedure that ends in a
   concrete artifact (PR, comment, file write, label move)? "Read these
   guidelines and use your judgement" is not steps — that is reference.
3. **Success criterion:** is there a check `shipctl verify` (or a
   reviewer) can apply that says *this pattern was followed*? "The PR
   has the evidence comment with marker `ship-managed:evidence`" is a
   criterion. "The output looks on-brand" is not.

A pattern that fails the trigger / steps / success-criterion test is
probably documentation. Move the body to a knowledge bucket and let
patterns reference it via `spec.knowledge_topics` when they need it.

## Audit examples

Concrete cases from today's catalog (or close paraphrases). Use them
to calibrate.

| Today                                                                    | Verdict                   | Where it should live                                                                                   |
|--------------------------------------------------------------------------|---------------------------|--------------------------------------------------------------------------------------------------------|
| `role-developer` — branch contract, PR shape, evidence comment marker    | Pattern                   | Stays as `pattern:role-developer`                                                                      |
| `flow-daily-retro` — scheduled retrospective producing a tracker comment | Pattern                   | Stays as `pattern:flow-daily-retro`; the scheduled cadence is a **lane** in `.ship/config.yml`         |
| `scheduled-sdlc-lane` — starter YAML + per-lane wiring                    | Neither — it's plumbing   | No longer a catalog artifact. The starter YAML lives in `backend/app/resources/starter_workflows/`; cadences live as lanes |
| `web-design-guidelines` — colour tokens, type scale, motion rules        | **Knowledge bucket**      | `repo`-scoped bucket under `.ship/knowledge/web-design-guidelines.md`; topics `[presentation, web]`    |
| `pdf-generation-handbook` — print bleed, embedded fonts, paginated TOC   | **Knowledge bucket**      | `project`-scoped bucket (shared across repos), topic `[delivery]`; patterns that touch PDFs reference it via `spec.knowledge_topics` |
| `brand-book-elmundi` — logo lockups, voice, do/don't pairs               | **Knowledge bucket**      | `workspace`-scoped bucket, topic `[branding]`; uploaded via the Distiller                              |
| `common-base` — guardrails composed as `{{BASE}}`                        | Pattern (fragment)        | Stays as `pattern:common-base` with `modes: []`; other patterns pull it in via `spec.include`          |
| Hypothetical `pattern:write-good-copy`                                   | Smell                     | Probably a knowledge bucket on tone + a pattern with a clear trigger ("when authoring landing copy")    |

The smell to look for: a "pattern" whose body is a long list of
"always do X, never do Y" without a trigger or a step list. That is a
knowledge bucket misfiled.

## Knowledge bucket shape

Buckets are rows in `knowledge_buckets` (see `backend/app/db/models/agent_memory.py`
for the authoritative schema). Each bucket carries a scope
(`workspace | project | repo | user`), a source adapter
(`repo_files | external_static | connector_proxy | agent_memory | audio_transcript`),
and a set of `bucket_articles` produced by the Distiller.

The **pattern-side** wiring is a single frontmatter field:

```yaml
spec:
  install_target: prompts/role/developer.md
  category: role
  modes: [lane, request]
  knowledge_topics: [code-style, architecture]   # optional
```

At render time the resolver walks the scope ladder
(`workspace → project → repo → user`, most-specific wins) and returns
the articles whose topics match. Agent tools
(`list_buckets`, `search_buckets`, `get_knowledge_bucket` in
`backend/app/services/agent/tools.py`) let the agent fetch a bucket by
name when it already knows which one it needs.

For the bucket-side surface — adapters, scope carriers, Distiller
runs, privacy guards — see [Knowledge buckets](/docs/knowledge-buckets).

## How a pattern reaches knowledge

Patterns do not embed knowledge; they declare which topics they may
need and let the runtime resolve the relevant buckets. Two entry
points:

- **Declarative:** `spec.knowledge_topics: [...]` on the pattern. The
  resolver finds every bucket whose topic set intersects and walks the
  scope ladder to pick the most specific one per topic. Buckets at
  repo scope shadow project scope, which shadows workspace scope; the
  `user` overlay sits in parallel and is private to the signed-in
  user.
- **Imperative:** during a pattern run the agent may call
  `list_buckets` / `search_buckets` / `get_knowledge_bucket` to resolve
  a bucket by id or semantic query when the pattern body can't
  pre-declare the topic.

The result: one universal `pattern:role-developer` ships in every Ship
install, but its rendered prompt in your repo composes with whichever
workspace / project / repo-scoped buckets the topic match brings in.
**No more "web-design-guidelines pattern" per project.**

## Harvesting at `shipctl init` / onboarding

Three paths land content into buckets today — all of them run through
the Distiller classifier
(`backend/app/services/distiller.py`) so new articles arrive with
consistent topic + scope tags:

1. **Repo mirror (Phase 2).** Any `.ship/knowledge/*.md` file in an
   activated repo is mirrored into a `source_kind='repo_files'`,
   `scope_kind='repo'` bucket by
   `backend/app/services/bucket_repo_files_sync.py`. Git is canonical:
   deletes archive the row, edits bump the article version.
   `shipctl knowledge init` seeds two starters (`code-style`,
   `ui-runbook`) via the backend's `knowledge_seed` endpoint.
2. **External uploads (Phase 6c).** The console's upload surface on
   the bucket detail page (and the `POST /workspaces/{ws}/buckets/{slug}/upload`
   route) accepts files or pasted content; the Distiller inbound
   adapter under `backend/app/services/distiller_sources.py` runs the
   classifier and writes a `source_kind='external_static'` bucket.
3. **Connector proxies (Phase 7b/7c).** The Notion and Linear
   connectors fetch a live source and project it into a
   `source_kind='connector_proxy'` bucket. Content is fetched on read,
   not stored in full — the bucket row is an index.

Three rules still apply regardless of the path:

1. **Staged, not active by default.** New articles land with
   `status='draft'` in `bucket_articles`; publishing is a confirm step
   on the distiller run.
2. **Provenance, then drift.** Every article carries a `provenance`
   JSON blob and a `content_sha`. `shipctl verify` flags drift between
   a repo-mirrored bucket and its source file so buckets don't
   silently fork from the living docs.
3. **Human in the loop on classification.** LLM-based topic / scope
   assignment is allowed (the Phase 6b classifier is LLM-backed) but
   the operator confirms topic / scope / publish in the console before
   the article is visible to agents.

This is the path that lets a freshly adopted repo go from "Ship has
no idea what we look like" to "the developer pattern composes with
our brand and code-style buckets" without anyone authoring a pattern.

## Quality gate: avoid pattern bloat

The whole point of knowledge buckets is so we keep `pattern` count
**bounded**. Concrete bar to clear before merging a new pattern:

- It passes the three-question litmus test above.
- The body is **method**, not **content** — if you can copy whole
  paragraphs into a wiki page and they still read fine, those
  paragraphs are knowledge.
- It is reusable across at least two unrelated repos in current or
  realistic future usage. (Patterns that exist for one team only are
  knowledge wearing a costume.)
- It either fires from an existing trigger surface (role / state /
  label / event / `default_trigger`) or proposes adding one in the
  same PR.
- Its `description` carries a *what* and a *when* — same rule as
  today.

If the new pattern fails any of these, the right move is to land a
knowledge bucket (upload, repo mirror, or connector) and extend an
existing pattern's `knowledge_topics` to consult it.

## Where to next

- The bucket model, Distiller, and console surface:
  [Knowledge buckets](/docs/knowledge-buckets).
- The general authoring contract:
  [Authoring artifacts](/docs/authoring).
- Vocabulary: [Concepts](/docs/concepts).
- Folder + frontmatter normative shape:
  [RFC-0005](/docs/protocol/rfc-0005-artifact-folder-spec-v2);
  pattern frontmatter metadata shipped in
  [RFC-0008](/docs/protocol/rfc-0008-catalog-reform).
