# GitHub Actions — migrating from a product monorepo

Many teams first wire Ship **beside application code** (e.g. `website/`) under `.github/workflows/`. **[Examples → Reference org](../../documentation/examples/elmundi/index.md)** describes one public layout and filenames; your tree may differ.

| Pattern (typical) | Purpose |
|-------------------|---------|
| `linear-agent-sdlc-scheduled.yml` | SDLC grid (intake, clarification, BA, developer) |
| `linear-agent-daily-audits.yml` | Tech / QA / security audit roles |
| `linear-agent-autonomous.yml` | Autonomous developer lane |
| `workflow-self-heal.yml` | Diagnostics / recovery |
| `linear-agent-release-check-on-deploy.yml` | Post-deploy checks |
| `check-failure-recovery.yml` | PR check failure → Linear updates (depends on your PR workflow **name**) |
| `e2e-regression-dev.yml` | Hosted Playwright (needs your app repo layout) |

## What to change when `ship` is the only checkout

1. **Checkout** — use a normal `actions/checkout@v4` of this repo, or sparse-checkout only what you need; remove monorepo-only paths such as `website/` unless your app still lives alongside Ship in the same workspace.
2. **`working-directory`** — use the **Ship** repository root (`.` when this repo is the checkout). Invoke scripts as **`node runtime/scripts/…`** (or `cd runtime` and `node scripts/…` if your workflow sets cwd to `runtime/`).
3. **Triggers** — `check-failure-recovery.yml` listens for `workflow_run` on a workflow **name** that must match your repo (e.g. not `"PR Checks + Preview Deploy"` unless you keep that exact title).
4. **Secrets / vars** — unchanged conceptually (`LINEAR_API_KEY`, `CURSOR_API_KEY`, etc.); scope them to the repo that runs the workflows.

We do **not** ship blindly copied YAML here: upstream monorepo workflows often embed **org-specific** names, schedules, and parent-workflow coupling. Start from your existing YAML or the reference example, apply the steps above, then diff.
