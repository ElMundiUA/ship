# DevOps bundle — output rubric

Scoring the PR the devops bundle opened against an infra ticket.
DevOps changes have wider blast radius than feature PRs, so the
rubric leans heavily on safety hygiene (rollback path, secret
discipline, observability) — not just feature correctness.

Total: **100 points**. Threshold: **70**.

Inputs the judge sees:
- `inputs.spec` — ticket body (the brief the devops bundle was
  given; usually a problem statement + target surface)
- `outputs.pr_title` — PR title from the sidecar's `pr.title`
- `outputs.pr_body` — PR body (with the runner-appended `Closes`
  footer)
- `outputs.pr_diff_summary` — `{additions, deletions,
  changed_files, files: [paths]}` from `gh pr view`
- `outputs.comments` — audit comments on the ticket
- `outputs.outcome` — `ready_next_step` | `needs_clarification`
  | `blocked` | `out_of_scope`

## Criteria — ready_next_step shape

### C1 — PR title shape (5 pts)
`infra({TICKET}): <headline>` conventional-commit shape.
- 5 pts: matches `^infra\({TICKET}\): .+$`, ≤72 chars
- 3 pts: ticket reference present but type prefix wrong (`feat`,
  `chore`) — devops PRs should use `infra` so audit greps surface
  them separately
- 0 pts: no conventional prefix

### C2 — Blast-radius section (15 pts)
PR body carries a `## Blast radius` section that names what
production-shaped surface can break if the change is wrong.
- 15 pts: one paragraph per affected surface, concrete (e.g.,
  "a typo here gates prod readiness probes")
- 8 pts: present but generic ("could affect production")
- 0 pts: missing — the rubric's load-bearing miss for devops

### C3 — Rollback section (20 pts)
`## Rollback` section spells out the exact revert / disable
command. Devops changes that can't be rolled back must carry a
feature flag.
- 20 pts: one concrete command (`kubectl rollout undo …`, git
  revert SHA, "flip flag X to false in admin UI")
- 10 pts: present but vague ("revert this PR")
- 0 pts: missing

### C4 — Observability hook (10 pts)
For changes that touch a code path running in prod, telemetry is
added or explicitly confirmed (Sentry breadcrumb / structured log
field / Prometheus counter). Config-only edits are exempt.
- 10 pts: telemetry added OR confirmation comment naming the
  existing surface ("existing `agent_run.dispatch` audit covers
  this")
- 5 pts: change touches a code path but no telemetry note
- 0 pts: code path change with no observability mention

### C5 — Secret hygiene (15 pts)
No real secrets in the diff (no `sk-…`, `gho_…`, `ship_pat_…`,
Fernet key, AWS/GCP key shapes). Non-secret config values land
committed; secrets land as `<set-in-k8s>` placeholders.
- 15 pts: clean — no real key prefix anywhere; placeholders
  used where needed
- 5 pts: clean BUT placeholder pattern inconsistent
- 0 pts: real secret committed (would already be caught by
  runner — score reflects that the role's pre-check failed)

### C6 — Staging/prod symmetry (10 pts)
Changes under `infra/k8s/overlays/prod/` have a matching staging
edit in the same PR (with optional explanatory comment when
values differ intentionally).
- 10 pts: prod edit paired with staging edit (or PR doesn't
  touch prod overlay)
- 5 pts: prod-only edit but PR body justifies why staging is
  exempt
- 0 pts: prod-only edit with no justification

### C7 — Scope discipline (10 pts)
Files touched stay within infra / CI / observability surfaces.
A devops PR that also changes `apps/backend/app/api/` or
`apps/console/src/` is doing two jobs at once — scope drift.
- 10 pts: all files under `infra/`, `.github/`, `tools/scripts/`,
  `apps/*/Dockerfile`, `package*.json` lockfiles, `.env*`, or
  matching pattern
- 5 pts: 1-2 files outside (Sentry config in app source counts
  as observability — allow)
- 0 pts: large drift (>3 unrelated product files)

### C8 — Diff sanity (5 pts)
Devops PRs tend to be small or surgical; large diffs need
justification. Soft bar: ≤150 net additions, ≤6 files.
- 5 pts: within bounds OR diff size justified in PR body
- 2 pts: 1.5× bounds, no justification
- 0 pts: massive (>400 lines OR >12 files) with no justification

### C9 — Audit tag (5 pts)
Audit comment ends with `[Ship SDLC:role-devops]`.
- 5 pts: tag present
- 0 pts: missing

### C10 — Test plan section (5 pts)
`## Test plan` checklist with verification steps for staging +
prod (separate items), not just "CI green".
- 5 pts: staging + prod verification items, each concrete
- 2 pts: present but only one environment
- 0 pts: missing

## Penalties

- **−20** if the diff contains a known secret prefix
  (`sk-ant-`, `sk-`, `gho_`, `ship_pat_`, AWS / GCP key shapes).
  Should have been blocked at the runner; surface in audit so a
  human notices the regression of the secret detector.
- **−15** if the PR title is `feat(...)` or `fix(...)` instead
  of `infra(...)` — devops PRs need to be greppable in commit
  history.
- **−10** if the diff touches `apps/backend/app/services/` or
  `apps/console/src/` in a way that looks like feature work
  rather than observability instrumentation.
- **−10** if any commit message in the diff includes
  `--no-verify` or skips a pre-commit hook.

## Criteria — non-ready_next_step shapes

For `needs_clarification`, `blocked`, `out_of_scope`:
- comment must name the specific question / blocker / scope
  mismatch (no "doesn't work" hand-waves)
- no PR body to score (sidecar's `pr` was null)
- score this branch out of 30 on:
  - **N1** (10 pts) — outcome chosen matches the actual situation
  - **N2** (15 pts) — comment is concrete + actionable
  - **N3** (5 pts) — audit tag present

Scale this branch's score to 100 by multiplying by 10/3.

## Output format

```json
{
  "score": <0-100, post-penalty>,
  "outcome_shape": "ready_next_step" | "needs_clarification" | "blocked" | "out_of_scope",
  "breakdown": {
    "C1": {"pts": <int>, "rationale": "<one sentence>"},
    ...
  },
  "penalties": [...],
  "overall_rationale": "<two sentences>",
  "would_ship": <bool, true iff score >= 70 AND no secret-prefix penalty>
}
```
