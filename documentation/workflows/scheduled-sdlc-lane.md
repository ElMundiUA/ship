# Scheduled SDLC lane

**Intent:** run **intake → clarification → BA → developer** on a clock, **one role per slot**, with picks only from an agreed queue state (typically **Todo** in a dedicated Linear project).

## Invariants

- **Human triage** stays in **Backlog**; automation never “helps” by dragging cards out of human-only columns without policy.
- **Concurrency:** a slot should not start a second pick for the same lane while the first is still in flight—use workflow concurrency groups or equivalent.
- **Evidence:** every transition leaves a **tracker comment**, **PR URL**, or **CI run** pointer.

## What you ship in CI

- A scheduler workflow (GitHub Actions, GitLab, etc.) with **deterministic minutes** and `workflow_dispatch` for manual replay.
- Small **pick** scripts that return a single issue key—or nothing—never a vague “top of column” without filters.

## Read next

- [GitHub Actions](/tools/github-actions) — scheduler role.
- [Linear](/tools/linear) — tracker role.
- [Prompts & workflows — SDLC story](/docs/prompts-workflows#elmundi-sdlc-flow) — narrative + diagram.
- [Examples → ElMundi](/docs/examples/elmundi) — reference YAML names and minutes.
