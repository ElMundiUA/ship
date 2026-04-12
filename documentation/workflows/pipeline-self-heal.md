# Pipeline self-heal

**Intent:** detect and repair **workflow/config drift** (broken cron, stale secret names, runner starvation) **without** stealing the SDLC intake slot.

## Invariants

- Self-heal cadence is **not** the same job as intake/BA/developer—different clock or odd hours so operators can read logs without mixing stories.
- First response is **CLI/report evidence**; optional agent only when a ticket exists and policy allows.

## What you ship

- A diagnostics workflow that emits a human-readable report artifact.
- Optional follow-up that opens or updates a **tech-debt** ticket with links.

## Read next

- [GitHub Actions](/tools/github-actions).
- [Cloud agent — workflow self-heal](/patterns/cloud-workflow-self-heal) — prompt body.
- [Examples → ElMundi](/docs/examples/elmundi) — `workflow-self-heal` reference naming.
