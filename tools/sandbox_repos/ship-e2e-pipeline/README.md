# ship-e2e-pipeline

Sandbox repository for Ship's pipeline e2e tests. Compact-but-real
TypeScript "feedback API" service — enough surface area for the
Navigator / planning / dev / validation bundles to do meaningful
work without drowning the agent in context.

This skeleton lives in the Ship monorepo under
`tools/sandbox_repos/ship-e2e-pipeline/`. To bootstrap the actual
GitHub repo:

```bash
# 1) Create the empty repo on GitHub
gh repo create ElMundiUA/ship-e2e-pipeline --private \
  --description "Ship pipeline e2e sandbox (do not delete)"

# 2) Initialise + push this skeleton
cd tools/sandbox_repos/ship-e2e-pipeline
git init -b main
git add -A
git commit -m "init: ship-e2e-pipeline skeleton"
git remote add origin git@github.com:ElMundiUA/ship-e2e-pipeline.git
git push -u origin main

# 3) Install the Ship App on the new repo via the Console onboarding
#    flow (one-time manual step).

# 4) Seed the workspace:
DATABASE_URL=postgresql://ship:ship@localhost:5433/ship \
  PYTHONPATH=apps .venv/bin/python \
  tools/scripts/seed_e2e_pipeline_workspace.py

# 5) Running the e2e suite needs a shipctl that supports
#    `--ticket` + `--trigger`. The npm-published version still
#    lags (npm view @elmundi/ship-cli version → 0.16.10 as of
#    2026-05-14, no --ticket flag yet). Until 0.17.x publishes,
#    point at the monorepo checkout:
export E2E_SHIPCTL_BIN=/Users/denyskuzin/Projects/ship/packages/cli/bin/shipctl.mjs
```

The sandbox carries the minimum file set Ship still needs in a
customer repo after the E16 event-driven dispatch cutover:

- `.github/workflows/ship-agent-run.yml` — the workflow_dispatch
  target the backend fires when a ticket is eligible for an
  agent stage. GitHub Actions requires workflow files to live in
  the repo; this is the irreducible footprint.
- `.ship/config.yml` — `shipctl run` reads routines from here at
  agent-run time. Could move to a backend API later (see the E21
  cleanup epic, deferred), but for now it stays.

Everything else from the wizard's full seed (`tracker-fsm.md`,
`wizard-seed.v2.json`) is intentionally omitted — nothing in
shipctl reads them after the E16 cutover.
