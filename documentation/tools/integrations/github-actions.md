# GitHub Actions (scheduler)

**Role in Ship:** the **scheduler** — deterministic clock, retries, concurrency, and `workflow_dispatch` for humans when the board is messy.

## What you wire

- **Cron** — one serious job per slot where possible; separate **SDLC** cadence from **self-heal** / diagnostics so failures are interpretable.
- **Secrets & variables** — GitHub holds credentials; mirror only what agents need into approved runtime envs (never paste keys into prompts).
- **Artifacts** — logs and reports from each run are part of the **evidence trail** alongside tracker comments and PRs.

## Agent touchpoints

- Workflows call small **Node entrypoints** (pick, launch, verify) that stay boring and testable; heavy reasoning stays in prompts executed by the agent runtime.
- Keep **idempotent** picks: the same slot should not fight itself (concurrency groups, single-flight labels).

## Read next

- [Tools — capability map](/docs/tools) — why scheduler is one of five capabilities.
- [Delivery, quality & release](/docs/adoption/delivery-quality-and-release-process) — release policy vs schedule.
- [Examples → ElMundi](/docs/examples/elmundi) — sample workflow filenames and minutes (reference only).
