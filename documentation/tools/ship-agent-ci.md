# CI adapters

For the RFC-level protocol, see [`documentation/rfc/rfc-0004-adapters.md`](../rfc/rfc-0004-adapters.md).
This page is the operational quick-reference per supported CI surface.

Per RFC-0002, `stack.ci` is one of: `gh-actions`, `gitlab-ci`, `buildkite`,
`circleci`, `azure-pipelines`, `jenkins`, `manual`. Each value maps to a
`tools/ci/<name>` adapter that declares detection signals, bootstrap files,
required secrets, and verification checks.

## Stages Ship expects

Every CI adapter is expected to expose six stages, in order, even if some of
them are no-ops for a particular preset:

1. **lint** — formatter, type-check, static analysis.
2. **build** — compile / bundle / package.
3. **test** — unit + integration tests.
4. **e2e** — end-to-end smoke (optional for `cli`, `adoption-minimum`).
5. **delivery** — publish a deploy candidate (artifact, container, prerelease).
6. **release** — promote to production behind the configured gate.

A passing `shipctl doctor` reports which stages are detected; `shipctl init
--bootstrap` writes the missing skeletons (or, for unsupported preset
triples, a `SHIP_BOOTSTRAP_PLAN.md` with the gap as TODOs).

## Per-CI subsections

Each subsection shares the same shape:

- **Detection** — what `tool/ci/<name>` looks for in the repo.
- **Stage mapping** — how the six stages land in this CI's vocabulary.
- **Required secrets per preset** — names only; concrete values come from
  `.env.example` / `SHIP_BOOTSTRAP_PLAN.md`. Adapter `## Secrets` sections
  enumerate them in each artifact.
- **Live check** — the `shipctl verify` invocation that exercises the
  adapter.

### gh-actions

Detection: `.github/workflows/*.yml`. Default for most public repos and the
canonical reference adapter (full skeletons today).

Stage mapping:

| Ship stage | gh-actions surface |
|------------|--------------------|
| lint, build, test | `jobs.*` in `.github/workflows/ship-ci.yml` triggered on `pull_request`. |
| e2e | dedicated job in the same workflow, `needs: [test]`. |
| delivery | `.github/workflows/ship-delivery.yml` triggered on push to `main`. |
| release | manual `workflow_dispatch` or scheduled `workflow_run` with environment-protected approval. |

Required secrets (per preset):

| Preset | Secret names |
|--------|--------------|
| `web-app` | `VERCEL_TOKEN` *or* `NETLIFY_AUTH_TOKEN`, `SHIP_API_TOKEN` (private mirror only) |
| `api-backend` | `REGISTRY_USERNAME`, `REGISTRY_PASSWORD`, deploy target token |
| `mobile-app` | `APPSTORE_CONNECT_KEY`, `GOOGLE_PLAY_SERVICE_ACCOUNT`, signing material |
| `cli` | `NPM_TOKEN` (or registry equivalent) |
| `monorepo` | per-package secrets; consult `SHIP_BOOTSTRAP_PLAN.md` |
| `adoption-minimum` | none beyond optional `SHIP_API_TOKEN` |

Live check:

```bash
shipctl verify --check ci-secrets
```

### gitlab-ci

Detection: `.gitlab-ci.yml` at repo root.

Stage mapping:

| Ship stage | GitLab CI surface |
|------------|-------------------|
| lint, build, test, e2e | `stages:` in `.gitlab-ci.yml`; one `job` per stage. |
| delivery | `deploy:staging` job gated to `main`. |
| release | `deploy:prod` with `when: manual` (or scheduled pipeline). |

Required secrets per preset are the same surface as `gh-actions`, exposed
via GitLab CI variables (project- or group-scoped). The adapter writes a
`SHIP_BOOTSTRAP_PLAN.md` listing each required variable name and scope.

Live check:

```bash
shipctl verify --check ci-secrets
```

### buildkite

Detection: `.buildkite/pipeline.yml`.

Stage mapping:

| Ship stage | Buildkite surface |
|------------|-------------------|
| lint, build, test, e2e | `steps:` with `command:` blocks; group via `wait` separators. |
| delivery | block step + manual unblock or schedule. |
| release | `block:` step with environment-restricted unblock. |

Required secrets: managed via Buildkite Agent secret hooks or Vault. The
adapter enumerates names; concrete provisioning lives in `SHIP_BOOTSTRAP_PLAN.md`.

Live check:

```bash
shipctl verify --check ci-secrets
```

### circleci

Detection: `.circleci/config.yml`.

Stage mapping:

| Ship stage | CircleCI surface |
|------------|------------------|
| lint, build, test, e2e | `jobs:` orchestrated in `workflows:` with `requires:`. |
| delivery | workflow filtered to `branches: only: main`. |
| release | manual `approval` job before deploy job. |

Required secrets: CircleCI contexts. The adapter writes a context manifest
under `.ship/ci/circleci-contexts.yml` listing required names per preset.

Live check:

```bash
shipctl verify --check ci-secrets
```

### azure-pipelines

Detection: `azure-pipelines.yml` at repo root or `.azure-pipelines/`.

Stage mapping:

| Ship stage | Azure Pipelines surface |
|------------|-------------------------|
| lint, build, test, e2e | `stages:` → `jobs:` → `steps:`. |
| delivery | `Deploy to Staging` stage with `dependsOn: [Test]`. |
| release | `Deploy to Production` stage gated by an environment approval. |

Required secrets: Azure DevOps Library variable groups. Per-preset names
are listed in `SHIP_BOOTSTRAP_PLAN.md`; the secret store itself is a Key
Vault reference for organizations using one.

Live check:

```bash
shipctl verify --check ci-secrets
```

### jenkins

Detection: `Jenkinsfile`.

Stage mapping:

| Ship stage | Jenkins surface |
|------------|-----------------|
| lint, build, test, e2e | `stage('lint') { … }` blocks in a declarative pipeline. |
| delivery | `stage('Deliver')` with `when { branch 'main' }`. |
| release | `input` step or scheduled job promoted via `Jenkins -> Build with parameters`. |

Required secrets: Jenkins credentials store; adapter enumerates the
credential ids per preset.

Live check:

```bash
shipctl verify --check ci-secrets
```

### manual

No CI adapter is selected; Ship is documentation-only. The verify step
reminds the human about local checks and produces a printable runbook.

Stage mapping:

| Ship stage | Manual surface |
|------------|----------------|
| lint, build, test, e2e | local commands listed in `SHIP_BOOTSTRAP_PLAN.md`. |
| delivery | manual upload / publish step. |
| release | human approval recorded in the PR description. |

Required secrets: none in CI. Local secret material lives in `.env.local`,
referenced by name in `.env.example`.

Live check:

```bash
shipctl verify --check ci-secrets
```

---

## Adaptation contract (vendor-neutral)

Mirrors the tracker contract in shape; the adoption agent must satisfy these
points whichever CI is selected.

### 1) Minimum CI interface

Your CI must expose:

- a way to run lint / build / test on every PR,
- an artifact / container / package output for delivery,
- a release surface gated by either human approval or schedule,
- secret material via the platform's secret store (never in `.ship/config.yml`),
- a way to surface failures back to the tracker (`stage:*`, `result:*`) and
  the PR.

### 2) Discovery questions the agent must ask

1. Which CI surface owns the PR pipeline today?
2. Which secrets store backs deploy credentials?
3. What gates promotion to production (approver, schedule, both)?
4. Where do failures surface (PR comment, tracker comment, Slack)?
5. What is the fallback if CI is unavailable for an hour?

### 3) Recommended output from adoption

A short `ci-adaptation.md` (or section in `SHIP_BOOTSTRAP_PLAN.md`) listing:

- selected CI adapter,
- per-stage job names,
- secret-name → CI-variable mapping,
- gate policy,
- known limitations.
