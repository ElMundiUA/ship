---
version: 1
project_type: backend
display_name: Backend service / API
delivery: docker
environments: [dev, prod]
# Machine-readable probes for the classifier + readiness assessor.
detect:
  project_type_signals:
    - "requirements.txt"
    - "pyproject.toml"
    - "fastapi"
    - "flask"
    - "django"
    - "go.mod"
    - "pom.xml"
    - "build.gradle"
    - "Cargo.toml"
    - "express"
    - "nestjs"
    - "fastify"
  capabilities:
    unit_tests:
      ["pytest", "jest", "vitest", "go test", "**/*_test.go", "**/test_*.py",
       "**/*.test.*", "**/*.spec.*", "junit", "cargo test"]
    containerization: ["Dockerfile"]
    dev_env: ["docker-compose*.yml", "compose*.yml"]
    prod_promotion: [".github/workflows/*deploy*", ".github/workflows/*release*"]
required:
  - unit_tests
  - containerization
  - dev_env
  - prod_promotion
# Secrets the readiness assessor checks against the repo's GitHub Actions
# secrets. ``required`` gates readiness; ``optional`` is advisory because
# the deploy-target + datastore creds are conditional (VPS vs k8s vs PaaS,
# Postgres vs Mongo — the operator picks in the bootstrap ticket).
secrets:
  required:
    - CURSOR_API_KEY
  optional:
    - REGISTRY_USERNAME
    - REGISTRY_TOKEN
    - DEV_DATABASE_URL
    - PROD_DATABASE_URL
---

# Backend service — SDLC blueprint (minimal)

## Where this gets you

A connected backend/API repo that: runs its unit + integration tests in
CI on every PR, ships as a **single Docker image built once per commit**,
deploys that image to a **dev** environment automatically, and **promotes
the same image to prod** on your approval (or a rule). The image is the
one delivery artifact — built once, promoted unchanged. No hand-built
prod artifacts, no "works on my machine".

This blueprint is stack-agnostic: Python (FastAPI/Flask/Django), Node
(Express/Nest/Fastify), Go, Java/Kotlin, Rust — the agents detect the
toolchain from the repo and scaffold the matching runner + Dockerfile.

---

## What YOU set up outside Ship and hand over

Ship's agents write the code and pipelines, but they can't create your
accounts or hold your credentials. Do these once, then add the secrets to
the repo (Settings → Secrets and variables → Actions). Ship reads them at
run time — it never stores the values.

1. **Container registry** — pick one and create a push token:
   - GitHub Container Registry (`ghcr.io`) — simplest, uses the repo's
     `GITHUB_TOKEN`, nothing to add; **or**
   - Docker Hub / other → add `REGISTRY_USERNAME` + `REGISTRY_TOKEN`.
2. **A datastore per environment** (if the service has one). Provide the
   connection string the app reads: `DEV_DATABASE_URL` / `PROD_DATABASE_URL`
   (or your framework's equivalent). Run migrations as a deploy step.
3. **Two deploy targets** (dev + prod). Any of: a VPS, a managed container
   host (Fly/Render/Railway), or a k8s cluster. For each env provide the
   access the deploy step needs (SSH creds, a kubeconfig, or the PaaS API
   token), plus a health-check path the promotion gate can probe.

## What Ship's agents scaffold

- **Unit + integration tests** wired as a required CI check. Integration
  tests run the service against an ephemeral datastore (a compose service
  or a CI service container).
- **A multi-stage, prod-lean Dockerfile** + `.dockerignore` building one
  runnable image per commit.
- **A `docker-compose.yml`** bringing the service + its datastore up with
  one command for local dev.
- **Build-once deploy**: build a sha-tagged image, deploy to dev on merge,
  promote the SAME digest to prod behind the chosen gate (a manual GitHub
  Environment approval by default; a rule job if configured). Migrations
  run as an explicit, ordered deploy step before the new image takes
  traffic.

The bootstrap epic Ship generates from this blueprint files one infra
ticket per missing capability — pick the deploy target + datastore in the
epic body and the DevOps agent fills in the specifics.
