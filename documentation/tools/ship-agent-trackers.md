# Tracker adapters

For the RFC-level protocol, see [`documentation/rfc/rfc-0004-adapters.md`](../rfc/rfc-0004-adapters.md).
This page is the operational quick-reference per supported tracker; the
**adaptation contract** in the second half is unchanged and remains the
human-facing contract that an agent must satisfy when introducing Ship to a
project.

Ship does not require a fixed tracker. It requires a tracker surface that
agents can reason about and audit. Per RFC-0002, `stack.tracker` is one of:
`linear`, `jira`, `github-issues`, `azure-boards`, `clickup`, `spreadsheet`,
`none`.

## Per-tracker subsections

Each subsection follows the same shape:

- **Minimum field support** — what Ship needs from that tool.
- **Environment variables** — secret names referenced by the adapter (no values).
- **`.ship/labels.yml` subset** — labels the adapter renders / verifies.
- **Live check** — the `shipctl verify` invocation that exercises the adapter.

The `## Minimum field support` columns are identical across trackers (the
columns enforce the cross-tracker semantics in §2 below); only the
"native equivalent" column changes.

### linear

| Ship semantic | Linear equivalent |
|---------------|-------------------|
| Stable work item key | Issue identifier (e.g. `ENG-123`) |
| Queue state (`Todo`) | Workflow state in the `Backlog` / `Todo` group |
| Execution states | Workflow states (`In Progress`, `In Review`, `Done`, `Blocked`) |
| Routing labels | Linear labels (`ready:*`, `stage:*`, `result:*`) |
| Evidence trail | Issue comments + attachments |

Environment variables:

- `LINEAR_API_KEY` — personal or service-account key.
- `LINEAR_WORKSPACE_SLUG` — workspace slug (variable, not secret).
- `LINEAR_DEFAULT_TEAM` — default team for newly-created issues.

`.ship/labels.yml` subset:

```yaml
tracker: linear
labels:
  - intake
  - spec
  - implementation
  - review
  - release
  - ready:developer
  - stage:plan
  - stage:build
  - stage:review
  - result:passed
  - result:failed
  - result:blocked
  - human:review-required
```

Live check:

```bash
shipctl verify --check tracker-labels
```

### jira

| Ship semantic | Jira equivalent |
|---------------|-----------------|
| Stable work item key | Issue key (`PROJ-123`) |
| Queue state | Workflow status in the `To Do` category |
| Execution states | `In Progress`, `In Review`, `Done`, `Blocked` |
| Routing labels | Labels or single-select custom fields |
| Evidence trail | Comments + attachments |

Environment variables:

- `JIRA_BASE_URL` — `https://<org>.atlassian.net` for cloud; on-prem uses host URL.
- `JIRA_EMAIL` — service-account email (cloud).
- `JIRA_API_TOKEN` — token from `id.atlassian.com`.
- `JIRA_PROJECT_KEY` — primary project key.

`.ship/labels.yml` subset (labels become Jira labels by default; mapping to a
custom field is a per-project override declared in the same file):

```yaml
tracker: jira
labels:
  - ship-intake
  - ship-spec
  - ship-implementation
  - ship-review
  - ship-release
  - ready-developer
  - stage-plan
  - stage-build
  - stage-review
  - result-passed
  - result-failed
  - result-blocked
  - human-review-required
```

Live check:

```bash
shipctl verify --check tracker-labels
```

### github-issues

| Ship semantic | GitHub Issues equivalent |
|---------------|--------------------------|
| Stable work item key | Issue number (`#123`) within the repo |
| Queue state | Project board column or label `state:todo` |
| Execution states | Labels (`state:in-progress`, …) or Project columns |
| Routing labels | GitHub labels (mapped 1:1) |
| Evidence trail | Issue + PR comments |

Environment variables:

- `GITHUB_TOKEN` — repo-scoped token; in CI, `${{ secrets.GITHUB_TOKEN }}`.
- `GITHUB_REPOSITORY` — `owner/repo` for the queue.

`.ship/labels.yml` subset:

```yaml
tracker: github-issues
labels:
  - ship:intake
  - ship:spec
  - ship:implementation
  - ship:review
  - ship:release
  - ready:developer
  - stage:plan
  - stage:build
  - stage:review
  - result:passed
  - result:failed
  - result:blocked
  - human:review-required
```

Live check:

```bash
shipctl verify --check tracker-labels
```

### azure-boards

| Ship semantic | Azure Boards equivalent |
|---------------|-------------------------|
| Stable work item key | Work-item id |
| Queue state | Backlog `New` / `Approved` state |
| Execution states | `Active`, `Resolved`, `Closed`, `Blocked` (state plus tag) |
| Routing labels | Tags (`ready:*`, `stage:*`, `result:*`) |
| Evidence trail | Discussion comments + linked PRs |

Environment variables:

- `AZURE_DEVOPS_ORG` — organization name.
- `AZURE_DEVOPS_PROJECT` — project name.
- `AZURE_DEVOPS_PAT` — Personal Access Token (Boards: Read & write).

`.ship/labels.yml` subset (labels = Azure Boards tags):

```yaml
tracker: azure-boards
labels:
  - ship-intake
  - ship-spec
  - ship-implementation
  - ship-review
  - ship-release
  - ready-developer
  - stage-plan
  - stage-build
  - stage-review
  - result-passed
  - result-failed
  - result-blocked
  - human-review-required
```

Live check:

```bash
shipctl verify --check tracker-labels
```

### clickup

| Ship semantic | ClickUp equivalent |
|---------------|--------------------|
| Stable work item key | Task id |
| Queue state | Custom status `Todo` (or list-level default status) |
| Execution states | Custom statuses; map names in `tracker-adaptation.md` |
| Routing labels | Tags (`ready:*`, `stage:*`, `result:*`) |
| Evidence trail | Task comments + linked docs |

Environment variables:

- `CLICKUP_API_TOKEN` — personal token.
- `CLICKUP_WORKSPACE_ID` — workspace numeric id.
- `CLICKUP_LIST_ID` — list id for the queue.

`.ship/labels.yml` subset:

```yaml
tracker: clickup
labels:
  - intake
  - spec
  - implementation
  - review
  - release
  - ready:developer
  - stage:plan
  - stage:build
  - stage:review
  - result:passed
  - result:failed
  - result:blocked
  - human:review-required
```

Live check:

```bash
shipctl verify --check tracker-labels
```

### spreadsheet

| Ship semantic | Spreadsheet equivalent |
|---------------|------------------------|
| Stable work item key | `id` column (UUID or auto-incrementing int) |
| Queue state | `state` column (`Todo`) |
| Execution states | `state` column (`In Progress`, `In Review`, `Done`, `Blocked`) |
| Routing labels | One column per label family (`ready`, `stage`, `result`) |
| Evidence trail | `notes` column + linked URLs |

Environment variables (Google Sheets variant):

- `GOOGLE_APPLICATION_CREDENTIALS` — path to service-account JSON (CI-only).
- `SHIP_SHEET_ID` — sheet identifier.
- `SHIP_SHEET_TAB` — tab name with the queue.

`.ship/labels.yml` subset (declares column-based routing rather than free-form labels):

```yaml
tracker: spreadsheet
columns:
  id: A
  title: B
  state: C
  ready: D
  stage: E
  result: F
  notes: G
```

Live check:

```bash
shipctl verify --check tracker-labels
```

### none

For agentless / single-author teams. The tracker surface is GitHub PR labels
plus PR description fields.

Environment variables: none.

`.ship/labels.yml` subset (labels live on PRs only):

```yaml
tracker: none
labels:
  - ready:developer
  - stage:build
  - stage:review
  - result:passed
  - result:blocked
```

Live check:

```bash
shipctl verify --check tracker-labels
```

---

## Adaptation contract (vendor-neutral)

This section is the human-facing contract; agents must satisfy it regardless
of which adapter is selected above.

### 1) Minimum tracker interface

Your tracker (Linear, Jira, GitHub Issues, Azure Boards, ClickUp, spreadsheet, custom DB) should support:

- **Stable work item key** (human-readable, unique enough for PR/comments).
- **Queue state** (canonical name in Ship docs: `Todo`).
- **Execution states** (`In Progress`, `In Review`, `Done`, `Blocked`) or explicit mapping.
- **Machine-readable tags/labels/fields** for routing (`ready:*`, `stage:*`, `result:*`) or equivalents.
- **Comment/evidence trail** where agents and humans leave traceable decisions.

If your tool cannot provide one of these, the adoption agent must define a workaround explicitly.

### 2) Label/field semantics to preserve

Ship semantics (names can be mapped, meaning must stay):

| Semantic | Typical label/field |
|----------|---------------------|
| Ready for developer pickup | `ready:developer` |
| Current owner stage | `stage:*` |
| Failed / blocked outcome | `result:failed`, `result:blocked` |
| Human override required | `human:review-required` |

For tools without labels (e.g. spreadsheets), create equivalent columns and document mapping.

### 3) State mapping rules

Ship expects a queue-first flow:

`Backlog -> Todo -> In Progress -> In Review -> Done` (+ `Blocked` from anywhere)

If your tracker uses different names, define mapping in the project onboarding notes.

### 4) Evidence contract

Every automated transition should leave at least one of:

- tracker comment,
- CI run URL,
- PR URL,
- test report reference.

No silent state mutation without evidence.

### 5) Discovery questions the agent must ask

1. Which system is your source of truth for delivery queue?
2. What are your real state names today?
3. How do you represent labels/tags/custom fields?
4. Where does audit evidence live (comments, docs, Slack mirror)?
5. What is the fallback if the tracker API is unavailable?

### 6) Recommended output from adoption

The agent should produce a short `tracker-adaptation.md` in the target repo with:

- selected tracker system,
- state mapping table,
- label/field mapping table,
- evidence policy,
- known limitations.
