---
version: 1
project_type: web
display_name: Web application
delivery: docker
environments: [dev, prod]
# Machine-readable probes for the classifier + readiness assessor.
detect:
  project_type_signals:
    - "package.json"
    - "next"
    - "react"
    - "vue"
    - "svelte"
    - "vite"
    - "angular"
  capabilities:
    unit_tests: ["jest", "vitest", "**/*.test.*", "**/*.spec.*", "pytest"]
    e2e_tests: ["@playwright/test", "cypress", "playwright.config.*", "cypress.config.*"]
    containerization: ["Dockerfile"]
    dev_env: ["docker-compose*.yml", "compose*.yml"]
    prod_promotion: [".github/workflows/*deploy*", ".github/workflows/*release*"]
required:
  - unit_tests
  - e2e_tests
  - containerization
  - dev_env
  - prod_promotion
# Secrets the readiness assessor checks against the repo's GitHub Actions
# secrets. ``required`` gates readiness; ``optional`` is advisory because
# the deploy-target creds are conditional (VPS vs k8s vs PaaS — the
# operator picks one in the bootstrap ticket).
secrets:
  required:
    - CURSOR_API_KEY
  optional:
    - REGISTRY_USERNAME
    - REGISTRY_TOKEN
    - DEV_DATABASE_URL
    - PROD_DATABASE_URL
---

# Web application — SDLC blueprint (minimal)

## Where this gets you

A connected web repo that: runs unit + e2e tests in CI, ships as a
**single Docker image built once per commit**, deploys that image to a
**dev** environment automatically, and **promotes the same image to
prod** on your approval (or a rule). No "works on my machine", no
hand-built prod artifacts.

---

## What YOU set up outside Ship and hand over

Ship's agents write the code and pipelines, but they can't create your
accounts or hold your credentials. Do these once, then add the secrets
to the repo (Settings → Secrets and variables → Actions). Ship reads
them at run time — it never stores the values.

1. **Container registry** — pick one and create a push token:
   - GitHub Container Registry (`ghcr.io`) — simplest, uses the repo's
     `GITHUB_TOKEN`, nothing to add; **or**
   - Docker Hub / other → add `REGISTRY_USERNAME` + `REGISTRY_TOKEN`.
2. **Two deploy targets** (dev + prod). Any of: a VPS, a managed
   container host (Fly/Render/Railway), or a k8s cluster. For each env
   provide the access the deploy step needs:
   - VPS: `DEV_SSH_HOST` / `DEV_SSH_KEY` and `PROD_SSH_HOST` /
     `PROD_SSH_KEY`; **or**
   - k8s: `DEV_KUBECONFIG` / `PROD_KUBECONFIG`; **or**
   - PaaS: the provider's deploy token per env.
3. **Runtime config per environment** — the app's own secrets, suffixed
   by env so dev and prod never share a database or key:
   `DEV_DATABASE_URL` / `PROD_DATABASE_URL`, plus any
   `DEV_*` / `PROD_*` API keys the app needs.
4. **(Optional) domains** — `DEV_APP_URL` / `PROD_APP_URL` if you want
   the deploy step to wire DNS / print the live URL.
5. **Promotion policy** — decide **manual** (a human approves dev→prod)
   or **rule-based** (e.g. "dev green for 2h"). For manual, create a
   GitHub **Environment** named `prod` with *Required reviewers* = you.
6. **Agent keys** — already part of Ship onboarding (`CURSOR_API_KEY`
   etc. on the repo). Confirm they're present or the runs won't start.

> Mark which of (1)/(2) you chose in the bootstrap ticket so the devops
> agent scaffolds the matching deploy step.

---

## What Ship scaffolds for you (devops agent)

- `Dockerfile` (multi-stage, prod-lean) + `.dockerignore`.
- `docker-compose.yml` for local/dev (app + datastore).
- CI: unit tests + e2e (Playwright) on every PR; required checks the
  auto-merger gates on.
- A deploy workflow: build-and-push `sha-<commit>` once → deploy to
  **dev** on merge → **promote the same image to prod** behind your
  chosen gate (GitHub Environment approval or rule job).
- `.env.example` documenting every `DEV_*` / `PROD_*` key (values stay
  in your secrets, never committed).

---

## Execution checklist (control)

Ship ticks these as the bootstrap epic lands; you verify the ones marked
**(you)**.

- [ ] **(you)** Registry token added (or `ghcr.io` confirmed).
- [ ] **(you)** Dev + prod deploy creds added (`DEV_*` / `PROD_*`).
- [ ] **(you)** Per-env runtime secrets added; dev ≠ prod datastore.
- [ ] **(you)** `prod` GitHub Environment created with required
  reviewer (manual promotion), **or** promotion rule chosen.
- [ ] `Dockerfile` builds a runnable image in CI.
- [ ] `docker-compose.yml` brings the app up locally.
- [ ] Unit tests run + pass in CI.
- [ ] E2e suite runs in CI (at least a smoke path).
- [ ] Merge to main builds `sha-<commit>` and deploys to **dev**.
- [ ] Promotion to **prod** uses the **same image digest** (no rebuild)
  and is gated (approval/rule).
- [ ] `.env.example` covers every required key.
