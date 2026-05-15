# Planning bundle — output rubric

Scoring the **ticket body** the planning bundle wrote (replacing the
PO Brief with a full implementation-grade spec) plus the audit
comment.

Total: **100 points**. Threshold for "ship-able": **70**.

Inputs the judge sees:
- `inputs.po_brief` — the original Brief the PO put on the ticket
- `outputs.body` — what the bundle wrote to the ticket
- `outputs.comments` — audit comments tagged `[Ship SDLC:role-planning]`

## Criteria

### C1 — Classification (5 pts)
The spec opens by classifying the work as one of:
`feature` / `bug` / `refactor` / `improvement` / `infra` / `out_of_scope`.
- 5 pts: classification line present + correct given the brief
- 3 pts: present but mis-classified (e.g. bug labelled feature)
- 0 pts: no classification

### C2 — Problem statement (10 pts)
A "Problem" or equivalent section in the operator's voice — what's
broken / missing, not what to build.
- 10 pts: concrete, operator-voice, names the pain
- 5 pts: present but vague ("we should make X better")
- 0 pts: missing or fabricated beyond the brief

### C3 — Goal / done definition (10 pts)
"Goal" / "Done means" section — what success looks like for the
human ordering the work.
- 10 pts: concrete success criterion, observable from outside the
  code
- 5 pts: implementation-flavoured ("add a route") rather than
  outcome-flavoured ("operators can purge old rows")
- 0 pts: missing

### C4 — Acceptance criteria (20 pts)
Numbered ACs, ideally Given/When/Then or equivalent precise shape.
- 20 pts: ≥3 ACs, each concrete enough to write a test against,
  no overlap, edge cases covered separately
- 12 pts: ≥3 ACs but vague ("works correctly") or overlapping
- 5 pts: 1-2 ACs, missing edges
- 0 pts: missing

### C5 — Scope + non-goals (10 pts)
Explicit "Scope" / "Out of scope" / "Non-goals" — names what's IN
*and* what's deliberately OUT.
- 10 pts: both halves present, non-goals concrete (not just "no
  refactor")
- 5 pts: only one half
- 0 pts: missing

### C6 — Architecture plan (15 pts)
A "Technical architecture" / "Architecture plan" section that names
real files / contracts / data shapes — not pseudo-code.
- 15 pts: names actual files (e.g. `src/api/feedback.ts`), contracts
  (return shape), risk + rollback note
- 8 pts: present but vague ("update the API"), no specific files
- 3 pts: stub only
- 0 pts: missing

### C7 — Test architecture (15 pts)
A "Test architecture" / "QA architecture" section: unit / integration
/ e2e split, mapped to the ACs.
- 15 pts: ACs → tests mapping is explicit; unit + HTTP layer split
- 8 pts: tests listed but unmapped, or only one layer
- 3 pts: stub only
- 0 pts: missing

### C8 — Impacted components / boundary discipline (10 pts)
Spec names the files / components that will change AND the ones that
shouldn't (e.g. "do not touch FeedbackStore.add"). Catches scope creep
before dev starts.
- 10 pts: both names AND boundaries
- 5 pts: only the will-change list
- 0 pts: missing

### C9 — Audit comment (5 pts)
The `[Ship SDLC:role-planning]`-tagged comment summarises the run in
one short paragraph (classification + key architecture decision +
trade-off).
- 5 pts: 2-4 sentences, hits the three notes above
- 3 pts: present but too long or missing one note
- 0 pts: missing or just "Done."

## Penalties

- **−10** if the body fabricates content beyond the PO Brief (invents
  features the PO didn't ask for).
- **−15** if the body rewrites the original PO Brief instead of
  appending the spec (PO Brief must stay verbatim).
- **−5** for each empty section header (`## Foo\n\n## Bar` with no
  content under Foo).

## Output format

Return JSON exactly:

```json
{
  "score": <0-100, post-penalty>,
  "breakdown": {
    "C1": {"pts": <int>, "rationale": "<one sentence>"},
    "C2": {"pts": <int>, "rationale": "<one sentence>"},
    ...
    "C9": {"pts": <int>, "rationale": "<one sentence>"}
  },
  "penalties": [
    {"code": "fabrication" | "rewrite_brief" | "empty_section", "pts": <-int>, "rationale": "<one sentence>"}
  ],
  "overall_rationale": "<two sentences: what went well, what's the biggest gap>",
  "would_ship": <bool, true iff score >= 70 and no penalty in [rewrite_brief]>
}
```
