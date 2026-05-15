# Decomposition bundle — output rubric

Scoring the project body the decomposition bundle wrote (WBS +
Architecture + Test architecture sections) plus the child tickets it
carved out and the audit comment on the anchor.

Total: **100 points**. Threshold: **70**.

Inputs the judge sees:
- `inputs.anchor_brief` — PO Brief on the anchor (stays untouched)
- `outputs.project_body` — project body with the sections the
  bundle wrote
- `outputs.child_tickets` — list of `{ticket_ref, title, body}` for
  every child the bundle created
- `outputs.comments` — audit comments on the anchor

## Criteria

### C1 — WBS section quality (20 pts)
"## WBS" section: ordered list of work slices that cover the
project end-to-end.
- 20 pts: 3-7 slices, each is a coherent unit of work (1-3 days
  sized), no overlap, covers the brief
- 12 pts: present but slices overlap or one slice swallows the
  whole project
- 5 pts: too few (<3) or too many (>10) slices
- 0 pts: missing

### C2 — Architecture section (15 pts)
"## Architecture" — project-scale design: components, data flow,
contracts. Not per-child detail.
- 15 pts: names components, contracts, deployment / sequencing
  notes
- 8 pts: present but generic ("we'll use Express")
- 0 pts: missing

### C3 — Test architecture section (15 pts)
"## Test architecture" — coverage strategy at the project level:
which layers (unit/integration/e2e) cover which slices.
- 15 pts: maps WBS slices → test layers explicitly
- 8 pts: present but unmapped
- 0 pts: missing

### C4 — Tasks section auto-rendered (5 pts)
"## Tasks" section lists the freshly-created child identifiers
(`- **MEM-XX** — <title>` shape). The **server** renders this from
the agent's ``child_tickets`` array — the agent should NOT
pre-write it.

Check ``meta.tasks_section_source`` in the artifact:
- ``"server"`` → identifiers came from server-side rendering; not
  agent fabrication. Score on format quality only.
- absent / ``"agent"`` → agent pre-wrote it; treat as fabrication.

Scoring:
- 5 pts: present, lists every child by identifier + title, format
  matches `- **<id>** — <title>`, and ``tasks_section_source`` is
  either ``"server"`` or absent with no other evidence of
  fabrication
- 0 pts: missing entirely, OR ``tasks_section_source == "agent"``

### C5 — Child tickets — count + sizing (15 pts)
Each WBS slice spawns exactly one child ticket. Child count should
roughly match WBS slice count.
- 15 pts: 1:1 match WBS ↔ children, each child is a coherent unit
- 8 pts: count off by 1-2 (extra "polish" or "documentation"
  bucket) but no slice un-covered
- 5 pts: count off by >2 OR a slice has no matching child
- 0 pts: no children OR children unrelated to WBS

### C6 — Child body discipline (15 pts)
Each child's body is 3-6 lines: scope statement, no per-child spec
yet (the per-ticket SDLC chain refines it later). Agents that
pre-spec each child here are wasting tokens.
- 15 pts: every child body is 3-6 lines, scope-only
- 8 pts: bodies vary but mostly within bounds
- 3 pts: some children carry full pre-specs (overshoot)
- 0 pts: empty child bodies OR all over-specced

### C7 — Brief preserved (10 pts)
The anchor's `## PO Brief` must stay verbatim. Decomp NEVER
rewrites the anchor description.
- 10 pts: anchor body unchanged (still ≤ original brief length)
- 0 pts: anchor body rewritten or expanded

### C8 — Audit comment (5 pts)
`[Ship decomposition:role-decomposition]` comment on the anchor,
one short paragraph on the WBS shape + sizing decisions.
- 5 pts: 2-4 sentences, names slice count + sizing trade-off
- 3 pts: present but rote
- 0 pts: missing

## Penalties

- **−10** if any child's body (from ``outputs.child_tickets[*].body``)
  is *fewer than 2 lines* (skeletal). Note: titles alone don't count
  as body — check the ``body`` field on each row.
- **−10** if the agent fabricated `MEM-XX` identifiers in the
  body instead of letting the server render the `## Tasks` section
  (i.e. ``meta.tasks_section_source == "agent"`` OR the section
  exists with no matching server side-effect). Do NOT apply this
  penalty when ``meta.tasks_section_source == "server"``.
- **−5** for each section that's empty (header only, no content).

## Output format

```json
{
  "score": <0-100>,
  "breakdown": {
    "C1": {"pts": <int>, "rationale": "<one sentence>"},
    ...
    "C8": {"pts": <int>, "rationale": "<one sentence>"}
  },
  "penalties": [...],
  "overall_rationale": "<two sentences>",
  "would_ship": <bool>
}
```
