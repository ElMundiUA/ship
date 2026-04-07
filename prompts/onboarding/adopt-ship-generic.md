# Ship — agent playbook: adopt framework into this repository

You are a **coding agent** integrating the **Ship** SDLC automation framework into the **current git repository** (the workspace you have open). Follow this playbook end-to-end unless the user explicitly narrows scope.

## Outcomes (definition of done)

1. **Ship artifacts** are present in a stable path (submodule **or** vendored copy — see below).
2. **Ticket system** is chosen and **documented** (env vars + `TRACKER_PROVIDER` if not Linear).
3. **`.env.example`** (or project equivalent) lists required secrets/vars for tracker + GitHub + Cursor Cloud Agent.
4. **At least one** GitHub Actions workflow can run pick/launch using the **correct `working-directory` / `node …/runtime/scripts/…` paths**.
5. **Verification**: `verify-setup` passes (or documented blockers if secrets missing in dev).
6. **Single PR** (or stacked PRs) with a short **Adoption notes** section listing paths, tracker, and manual follow-ups.

## Do not

- Commit API keys, PATs, or tokens.
- Rewrite application business logic unless required to wire CI paths.
- Remove existing production workflows without the user’s approval — **add** or **adapt** Ship lanes alongside.

## 1) Inventory (read-only)

- Monorepo layout: where is the app (`website/`, `apps/web/`, etc.)?
- Existing `.github/workflows/*` — names, triggers, `working-directory`, secrets used.
- Current ticket system (Linear / Jira / GitHub Issues / Azure Boards / ClickUp).
- Whether **Cursor Cloud Agent** will be used from GitHub Actions (needs `CURSOR_API_KEY` + tracker credentials in GitHub **and** Cursor repo env where applicable).

## 2) Bring Ship into the tree

Pick **one** pattern:

| Pattern | When to use | Rough steps |
|--------|-------------|-------------|
| **Git submodule** `tools/ship` | You want upstream updates via `git submodule update` | `git submodule add <ship-repo-url> tools/ship` |
| **Vendored copy** | Policy forbids submodules | Copy `documentation/`, `prompts/`, `runtime/` (+ root `package.json` workspace if you use npm workspaces at repo root) from [Ship](https://github.com/ElMundiUA/ship); keep `LICENSE` notice |

**Path variable:** below, `SHIP_ROOT` means the directory that contains `runtime/` and `prompts/` (e.g. `tools/ship` or repo root if Ship is the whole repo).

## 3) Node / CLI

- From repo root, if Ship is nested: `npm install` inside `SHIP_ROOT` **or** add `SHIP_ROOT` as an npm workspace in the monorepo root `package.json`.
- CLI entry: `SHIP_ROOT/runtime/dist/cli.js` (commands: `next`, `start`, `get`, `init`, `cloud-agent-launch` is separate).

## 4) Ticket system (ship-agent)

Supported via **`TRACKER_PROVIDER`**: `linear` (default), `jira`, `github`, `azure-devops`, `clickup`.

- Set env vars per backend (see Ship manual **Ship Agent & trackers**).
- For **GitHub Issues**, workflow state on open issues uses labels `ship-status:*` — document that for the team.
- **Pick scripts** under `runtime/scripts/pick-*.mjs` are still **Linear-oriented** today; if the project uses Jira/GitHub/Azure/ClickUp, either keep Linear for SDLC queue **or** plan a follow-up to adapt pick scripts (do not fake success — document gap in PR).

## 5) Environment file

- Prefer **repository root** `.env` for local dev (Ship scripts resolve repo root via git).
- Merge variables from Ship’s `.env.example` relevant to: tracker, `GITHUB_TOKEN`, `CURSOR_API_KEY`, optional Bunny/SendGrid, `LINEAR_SDLC_PROJECT_*` if Linear.

## 6) GitHub Actions

- Start from Ship **`examples/github-workflows/README.md`** philosophy: YAML = **when** + secrets; **Node** = pick/launch logic.
- Every `run: node scripts/...` must become `run: node $SHIP_ROOT/runtime/scripts/...` (or `working-directory: tools/ship` + `node runtime/scripts/...`).
- Ensure `actions/checkout` fetches **submodules** if you use them: `submodules: recursive` or `submodules: true`.

## 7) Cursor Cloud Agent (if used)

- GitHub Actions: secret **`CURSOR_API_KEY`**.
- Cursor dashboard: same repo must have **tracker API key** (e.g. `LINEAR_API_KEY`) in Cloud Agent environment so headless runs can update tickets.
- Launch entrypoint: `node SHIP_ROOT/runtime/scripts/cloud-agent-launch.mjs --role=... --issue=...`
- Prompts read from **`SHIP_ROOT/prompts/cloud-agent/*.md`** — do not point launch at `prompts/catalog/` unless files were promoted.

## 8) Linear-only hygiene (if tracker is Linear)

- Run (from repo root, with `cwd` = `SHIP_ROOT/runtime` or equivalent):  
  `node runtime/scripts/sync-linear-team-labels.mjs`  
  (path adjusted so `node` runs with package root = `runtime/`).
- Optional: `node runtime/scripts/ensure-audit-linear-projects.mjs` for audit projects.

## 9) Verify

From `SHIP_ROOT/runtime` (or with paths adjusted):

```bash
bash scripts/verify-setup.sh
```

If secrets are missing, still list **expected** secret names in PR body.

## 10) PR body template

```markdown
## Ship adoption
- SHIP_ROOT: `tools/ship` (or …)
- Tracker: Linear | Jira | GitHub | Azure DevOps | ClickUp (`TRACKER_PROVIDER=…`)
- Workflows touched: …
- Secrets required: …
- Follow-ups: pick scripts for non-Linear / …
```

## Reference (human docs)

- Ship manual: **Adoption** section and **Ship Agent & trackers**.
- ElMundi-specific deltas: `prompts/onboarding/adopt-ship-elmundi.md` (if this repo is ElMundi).
