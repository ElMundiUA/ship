# E03 — Golden path: signup → first run, end-to-end

**Priority:** P0
**Effort:** L (~7–10 days, half investigation, half fixes)
**Owner:** TBD

## Goal

Walk a brand-new user from "I clicked Sign in" to "I see a routine that ran, with evidence". Every break in the chain becomes a fix. The deliverable is a green checklist plus a hand-recorded session that completes inside 15 minutes.

## Why

The maintainer said the app "doesn't fully work yet" — that statement is too broad to fix. This epic forces it into an enumerable list. Every other P0 epic depends on this audit's bug list.

Reference flows:
- 5-step WOW wizard: `console/src/app/onboarding/page.tsx` — `github` → `repos` → `tracker` → `confirm` → `done`.
- First scheduled execution: `.github/workflows/ship-trigger-schedule.yml` (every 30 min) → `shipctl trigger --event schedule` → due routines → dispatch → `pipeline_runs`.

## Tasks

### T01 — Reset & document fresh-state assumptions **[S]**

- Pick a clean Auth0 user (or wipe an existing one's workspaces).
- Reset the sandbox repo to "no Ship" state (`e2e/lib/reset.ts` already does this — confirm).
- Document the precondition: which env vars set, which dashboard tabs to have open.

**Acceptance:** a fresh shell + a reset checklist living in this file or referenced from `e2e/README.md`.

### Precondition checklist

This section documents all prerequisites before running the E03 golden-path audit on a fresh Auth0 user against `app.ship.elmundi.com`.

#### Auth0 user state

- Create a new Auth0 test user at https://manage.auth0.com → Users & Roles → Users → Create user.
  - Email: use a unique `e2e+<timestamp>@example.com` address (avoid reusing accounts within 24 hours to clear session caches).
  - Password: auto-generated is fine; note it for sign-in.
- Verify the email domain is configured in the Auth0 Application Settings (Allowed Callback URLs, Origins).
- **Or:** wipe an existing user's workspaces via the Ship backend:
  ```bash
  # From backend root (requires DB access):
  psql $DATABASE_URL -c "DELETE FROM workspaces WHERE user_id = (SELECT id FROM users WHERE email = 'your-email@example.com');"
  ```

#### Sandbox GitHub repo

- Use a dedicated test repository (e.g., `your-org/e2e-sandbox`) or create a fresh one.
- Ensure the repo is empty or at a clean `e2e-baseline` tag (no `.ship/config.yml` or `.github/workflows/run-agent.yml` yet).
- Have admin access to push branches, create issues, and receive webhook deliveries.
- To reset the repo to a known baseline after a previous audit run:
  ```bash
  # Via e2e/lib/reset.ts (see e2e/README.md "Reset sandbox"):
  export E2E_RESET_SANDBOX=1
  export E2E_SANDBOX_REPO=your-org/e2e-sandbox
  export GITHUB_TOKEN=ghp_... (repo admin: issues+pulls+contents write)
  export E2E_SHIP_API_BASE=https://api.dev.example.com
  export E2E_SHIP_API_TOKEN=ship_... (workspace admin)
  cd e2e && npx playwright test --project=sandbox-api tests/full-journey-reset.sandbox.spec.ts
  ```
  This closes `[e2e]` issues, Ship bot PRs, and unwires the workspace without rewriting history.

#### Console browser profile

- Open `https://app.ship.elmundi.com` in a **fresh, logged-out browser context** (use Firefox Private Window or Chrome Guest Profile).
- Have the following dashboard tabs open for quick navigation during the audit:
  - **Sentry**: https://sentry.io (for error tracking during onboarding steps).
  - **Bunny**: Bunny CDN dashboard (for asset delivery verification if needed).
  - **Linear**: https://linear.app (for tracker integration step T05).
  - **GitHub Apps**: https://github.com/settings/apps (for GitHub App install callback verification).
  - **Console Dashboard**: https://app.ship.elmundi.com (the Ship app itself).

#### Environment variables on operator's laptop

Copy and set these variables in your shell (source from `e2e/.env` or `.env.local`):

```bash
# Core console + API
export E2E_CONSOLE_BASE_URL=https://app.ship.elmundi.com
export E2E_SHIP_API_BASE=https://api.dev.example.com  # or production API host

# Auth: minted CLI token (create in console: Settings → CLI Token, workspace admin scope)
export E2E_SHIP_API_TOKEN=ship_your_token_here

# Sandbox repo (for T04–T07)
export E2E_SANDBOX_REPO=your-org/e2e-sandbox
export GITHUB_TOKEN=ghp_...  # fine-grained PAT with Contents+Issues+Pull requests read/write

# (Optional) Workspace ID if not the first workspace in your account
# export E2E_WORKSPACE_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# (Optional) Auth0 session (for rerunning without re-logging in)
# export E2E_STORAGE_STATE=e2e/.auth/user.json

# (Optional) GitHub App install automation
# export E2E_RUN_GITHUB_APP_INSTALL=1
# export E2E_GITHUB_INSTALL_ACCOUNT=YourOrg
# export E2E_GITHUB_REPO_FULL_NAME=your-org/e2e-sandbox

# (Optional) Linear integration secrets (for T05)
# export E2E_LINEAR_API_KEY=lin_api_...
```

**Total env vars required:** 5 (with optional: up to 14). See `e2e/.env.deployed.example` for the full template and comments.

#### Reset commands before each audit walk

Run these commands once at the start of each fresh audit to ensure a clean state:

```bash
# 1. Reset sandbox repo via e2e/lib/reset.ts
export E2E_RESET_SANDBOX=1
export E2E_SANDBOX_REPO=your-org/e2e-sandbox
export GITHUB_TOKEN=ghp_...
export E2E_SHIP_API_BASE=https://api.dev.example.com
export E2E_SHIP_API_TOKEN=ship_...
cd e2e && npx playwright test --project=sandbox-api tests/full-journey-reset.sandbox.spec.ts

# 2. Clear browser cache (manually: Ctrl+Shift+Delete → All Time, or use a fresh profile)

# 3. Verify Auth0 session is logged out
#    Navigate to https://app.ship.elmundi.com/login (should show "Continue with Auth0")
```

**What `full-journey-reset.sandbox.spec.ts` does:**
- Closes issues tagged `[e2e]` or labelled `ship:needs-clarification`.
- Closes PRs authored by Ship or on `ship/*` branches.
- Via Ship API: disconnects activated repos and removes `github|linear|notion` integrations.
- Leaves the workspace row so the next run reuses the same tenant ID.

See `e2e/lib/reset.ts` for implementation details and `e2e/README.md` for full phase documentation.

### T02 — Step 0: signup + workspace JIT **[M]**

- Hit `/login`, click Auth0, complete OIDC flow.
- Land back in console: the `/v1/auth/me` should return a user, and `/v1/workspaces` should auto-provision a personal workspace if none exists.
- Verify cookie `ship_session` set, middleware redirects unauthenticated traffic.

**Bugs to expect:** Auth0 callback URL drift, JWKS expiry, workspace JIT race when two sessions land at once.

### T03 — Step 1: GitHub App install **[M]**

- Click "Install GitHub App" → installer flow on github.com.
- Grant on a chosen account (test org).
- Callback hits `/v1/integrations/github/install/callback`.
- Verify webhook subscription, installation_id stored.

**Bugs to expect:** webhook secret mismatch, callback redirect loop, repo list empty after install.

### T04 — Step 2: repo activation **[M]**

- Pick repo from the live install list.
- POST to `/v1/workspaces/{id}/repos/activate` triggers `seed_default_pipelines`.
- Seed PR appears in the chosen repo with `.ship/config.yml`, `.github/workflows/run-agent.yml`, agent rule files.

**Bugs to expect:** seed PR does not include the latest bundle version; CODEOWNERS auto-derived rules collide with existing CODEOWNERS; repo intel harvest fails silently.

### T05 — Step 3: tracker bind (Linear) **[M]**

- Click "Connect Linear" → Linear OAuth.
- Callback at `/v1/integrations/linear/callback`.
- Pick team + project on the workspace.
- Per-repo: pick the team to use for this repo (default falls back to workspace).

**Bugs to expect:** OAuth state token mismatch, missing `team_id` in the picker, tracker selection not persisted.

### T06 — Step 4: confirm seed → step 5: done **[S]**

- "What will land" preview accurate.
- "Open seed PR" CTA opens GitHub PR draft.
- Done page shows tangible next links: dashboard, inbox, knowledge.

**Bugs to expect:** preview drift from actual seed contents, done-page links going to 404 because the workspace state is not yet primed.

### T07 — First scheduled run actually executes **[L]**

- Wait for the next 30-min mark.
- `ship-trigger-schedule.yml` cron should fire `shipctl trigger --event schedule`.
- Due routines dispatch into the customer repo's `.github/workflows/run-agent.yml`.
- A `pipeline_runs` row appears in Postgres; dashboard shows it.

**Bugs to expect:** workflow_dispatch JWT failure (see `github_app.py:1051`), missing `run_token` propagation, agent rules not yet committed, `shipctl trigger` not finding the workspace because cron container has no DB credentials.

### T08 — First clarification reaches the Inbox **[M]**

- Cursor agent (or stub) emits `shipctl callback --outcome=clarification`.
- POST `/v1/clarifications/pipeline` accepts.
- Inbox surface in console shows the new item within 30s.

**Bugs to expect:** routing rules not seeded for the workspace; clarifications writing to a deprecated table after RFC-0010 rename.

### T09 — Bug list snapshot **[S]**

- After the walk, write a section here: "Bugs found during E03 audit, 2026-MM-DD".
- Each entry: where, what failed, what the fix should be, what task was opened (or P0 hot-fix done).

**Acceptance:** the bug list exists and was read by the maintainer.

### T10 — Record the walk **[S]**

- 10–15 minute screen recording (this is the **raw** dogfood video, not the polished demo from E12).
- Stored in `output/` (gitignored) or uploaded to Bunny private.
- Linked from the bug list.

**Acceptance:** a watchable record of the golden path on video.

## Definition of done

- [ ] All 8 path-steps (T02–T08) completed without manual database fixes.
- [ ] Bug list captured and triaged into P0 / P1 / P2.
- [ ] At most three bugs remain P0 after this epic; the rest fold into E04–E07.

## Risks / unknowns

- Auth0 production tenant config drift between maintainer's local and Bunny prod.
- GitHub App permissions might be slightly off after recent rename / scope changes.
- `shipctl trigger --event schedule` runs from inside the cron worker container — needs DB + GitHub App private key.

## Out of scope

- Fixing every bug found inside this epic (only the smallest / blocking ones are hotfixed; bigger ones go to dedicated epics).
- Performance / cold-start tuning.
- Adding new wizard steps.
