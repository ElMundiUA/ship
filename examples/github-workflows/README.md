# GitHub Actions — migrating from the ElMundi monorepo

Ship’s automation was first wired in **[ElMundiUA/elmundi](https://github.com/ElMundiUA/elmundi)** next to `website/`. Workflow files live there under `.github/workflows/`, for example:

| Pattern (in elmundi) | Purpose |
|----------------------|---------|
| `linear-agent-sdlc-scheduled.yml` | SDLC grid (intake, clarification, BA, developer) |
| `linear-agent-daily-audits.yml` | Tech / QA / security audit roles |
| `linear-agent-autonomous.yml` | Autonomous developer lane |
| `workflow-self-heal.yml` | Diagnostics / recovery |
| `linear-agent-release-check-on-deploy.yml` | Post-deploy checks |
| `check-failure-recovery.yml` | PR check failure → Linear updates (depends on your PR workflow **name**) |
| `e2e-regression-dev.yml` | Hosted Playwright (needs your app repo layout) |

## What to change when `ship` is the only checkout

1. **Checkout** — use a normal `actions/checkout@v4` of this repo, or sparse-checkout only what you need; remove monorepo-only paths such as `website/` unless your app still lives alongside Ship in the same workspace.
2. **`working-directory`** — replace every `tools/linear-agent` with **`.`** (repository root of **ship**).
3. **Triggers** — `check-failure-recovery.yml` listens for `workflow_run` on a workflow **name** that must match your repo (e.g. not `"PR Checks + Preview Deploy"` unless you keep that exact title).
4. **Secrets / vars** — unchanged conceptually (`LINEAR_API_KEY`, `CURSOR_API_KEY`, etc.); scope them to the repo that runs the workflows.

We do **not** ship blindly copied YAML here: the monorepo versions embed ElMundi-specific names, schedules, and parent-workflow coupling. Copy from **elmundi**, apply the three steps above, then diff.
