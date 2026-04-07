# Ship Agent CLI and tracker adapters

The Node CLI lives under **`runtime/`** as **`dist/cli.js`**. It orchestrates multi-role workflow commands (`next`, `start`, `complete`, `release-check`, `pr-create`, …) against an **issue tracker**. The npm package is **`ship-agent`** (workspace under `runtime/package.json`); it still exposes a **`linear-agent`** bin name for backward compatibility with scripts and muscle memory.

**Choose a backend** with environment variable **`TRACKER_PROVIDER`** (wins over config file) or with **`tracker.provider`** in a JSON config file (see below).

| Value | Backend |
|-------|---------|
| `linear` | Linear (GraphQL) — default |
| `jira` | Jira Cloud (REST v3) |
| `github` | GitHub Issues (REST + Search API) |
| `azure-devops` | Azure DevOps Boards (work items) |
| `clickup` | ClickUp (list tasks) |

Alias: `azdo` in `TRACKER_PROVIDER` is normalized to **`azure-devops`**.

---

## What stays the same across backends

- **Role labels** from `runtime/dist/agent-contracts.js` — e.g. `ready:developer`, `stage:ba`, `flow:no-ba`, `result:failed`. Automation and docs assume these **exact strings** (as labels, tags, or GitHub labels depending on the system).
- **Workflow state names** the CLI passes through (`In Progress`, `In Review`, `Ready`, `Blocked`, `Done`, …) must exist in **your** process/columns. If your tool uses different names, either align the board or extend the CLI with a mapping (not shipped yet).

**Scope note:** Pick scripts under **`runtime/scripts/`** and the reference GitHub workflows in this repo are still **Linear-oriented** (project IDs, GraphQL). The CLI multi-tracker work lets you run **`node runtime/dist/cli.js …`** from the repository root (or **`cd runtime && node dist/cli.js …`**) locally or from custom workflows against Jira, GitHub Issues, Azure Boards, or ClickUp without changing command names.

---

## Config files

Merged with defaults by `loadConfig()` (first file found wins):

1. Path from **`SHIP_AGENT_CONFIG`** or legacy **`LINEAR_AGENT_CONFIG`**
2. `ship-agent.config.json` or `.ship-agent.json` in the current working directory
3. `linear-agent.config.json` or `.linear-agent.json` (legacy)

Example skeleton:

```json
{
  "tracker": {
    "provider": "linear",
    "jira": {},
    "github": {},
    "azureDevops": {},
    "clickup": {}
  },
  "linear": {
    "apiKeyEnv": "LINEAR_API_KEY",
    "teamKey": "ELM"
  }
}
```

Optional nested objects only override **names of environment variables** (e.g. `hostEnv`, `patEnv`) where supported — see each backend below.

---

## Linear {#configure-linear}

**`TRACKER_PROVIDER`:** omit or `linear`.

### Environment

| Variable | Purpose |
|----------|---------|
| `LINEAR_API_KEY` | API key ([Linear settings](https://linear.app/settings/api)) |

Override the env var name via config: **`linear.apiKeyEnv`**.

### Config overrides

```json
{
  "linear": {
    "apiKeyEnv": "LINEAR_API_KEY",
    "teamId": "optional-uuid",
    "teamKey": "ELM",
    "defaultProject": "optional"
  }
}
```

`teamKey` / `teamId` matter when the CLI **creates** labels in Linear.

---

## Jira Cloud {#configure-jira}

**`TRACKER_PROVIDER=jira`**

### Environment

| Variable | Purpose |
|----------|---------|
| `JIRA_HOST` | Site host, e.g. `your-org.atlassian.net` (with or without `https://`) |
| `JIRA_PAT` | **Preferred:** API token as Bearer |
| *or* `JIRA_EMAIL` + `JIRA_API_TOKEN` | Classic Cloud API token with Basic auth |

### Config overrides (`tracker.jira`)

| Key | Default env |
|-----|-------------|
| `hostEnv` | `JIRA_HOST` |
| `patEnv` | `JIRA_PAT` |
| `emailEnv` | `JIRA_EMAIL` |
| `tokenEnv` | `JIRA_API_TOKEN` |

### Behaviour notes

- Queue filtering uses **JQL** (unresolved, not Done) and the same **`ready:*` / `flow:*`** label names as Jira **labels**.
- **`updateIssueState`** picks a **transition** whose target status name matches the CLI string (case-insensitive). If nothing matches, check available transitions in the UI/API.

---

## GitHub Issues {#configure-github}

**`TRACKER_PROVIDER=github`**

Issues are scoped to **one repository**. PRs still use `GITHUB_TOKEN` as today; you may use a dedicated token for Issues-only scopes if you prefer.

### Environment

| Variable | Purpose |
|----------|---------|
| `GITHUB_REPOSITORY` | `owner/repo` (same as Actions) **or** set both below |
| `GITHUB_ISSUES_OWNER` | Owner if not using `GITHUB_REPOSITORY` |
| `GITHUB_ISSUES_REPO` | Repo name if not using `GITHUB_REPOSITORY` |
| `GITHUB_TOKEN` | Default token for API calls |
| `GITHUB_ISSUES_TOKEN` | Optional; if set, used **instead of** `GITHUB_TOKEN` for the Issues adapter |

### Config overrides (`tracker.github`)

| Key | Default env |
|-----|-------------|
| `tokenEnv` | `GITHUB_TOKEN` |
| `altTokenEnv` | `GITHUB_ISSUES_TOKEN` |
| `repoEnv` | `GITHUB_REPOSITORY` |
| `ownerEnv` | `GITHUB_ISSUES_OWNER` |
| `repoNameEnv` | `GITHUB_ISSUES_REPO` |

### Workflow state on open issues

GitHub has a single **open/closed** flag. For **open** issues, the CLI stores workflow state as an **exclusive** repository label:

`ship-status:<Name>`

Examples: `ship-status:In Progress`, `ship-status:In Review`. **`status-set`** and handoff commands add/remove these automatically. **`Done` / `Canceled`** close the issue and strip `ship-status:*` labels.

Use normal GitHub **labels** for `ready:*`, `stage:*`, `flow:*` (same names as other backends).

---

## Azure DevOps Boards {#configure-azure-devops}

**`TRACKER_PROVIDER=azure-devops`** (or **`TRACKER_PROVIDER=azdo`**)

### Environment

| Variable | Purpose |
|----------|---------|
| `AZURE_DEVOPS_ORG` | Organization name (dev.azure.com/**org**) |
| `AZURE_DEVOPS_PROJECT` | Project name |
| `AZURE_DEVOPS_PAT` | Personal access token with work item read/write |

Auth uses **HTTP Basic** with an empty username and the PAT as password (`:pat`).

### Config overrides (`tracker.azureDevops`)

| Key | Default env |
|-----|-------------|
| `orgEnv` | `AZURE_DEVOPS_ORG` |
| `projectEnv` | `AZURE_DEVOPS_PROJECT` |
| `patEnv` | `AZURE_DEVOPS_PAT` |

### Behaviour notes

- Ship **labels** map to work item **Tags** (`ready:developer`, `flow:no-ba`, …). Tags are semicolon-separated in Azure DevOps.
- **`updateIssueState`** sets **`System.State`**. Values must match your process template (e.g. Agile, Scrum, custom).
- WIQL queries exclude work items in **Closed** / **Removed** states.

---

## ClickUp {#configure-clickup}

**`TRACKER_PROVIDER=clickup`**

The adapter targets **one list** (folder/space hierarchy is fixed by that list ID).

### Environment

| Variable | Purpose |
|----------|---------|
| `CLICKUP_API_TOKEN` | API token |
| `CLICKUP_LIST_ID` | List ID whose tasks participate in the workflow |
| `CLICKUP_TEAM_ID` | Optional but recommended when resolving tasks by **custom task id** (short key) |

### Config overrides (`tracker.clickup`)

| Key | Default env |
|-----|-------------|
| `tokenEnv` | `CLICKUP_API_TOKEN` |
| `listIdEnv` | `CLICKUP_LIST_ID` |
| `teamIdEnv` | `CLICKUP_TEAM_ID` |

### Behaviour notes

- **Labels** map to ClickUp **tags** (same `ready:*`, `stage:*`, … names).
- **`updateIssueState`** passes the status **name** your list uses (`PUT /task/{id}`). It must match an existing status in that list.
- **`list`** / **`next`** paginate through tasks in the configured list (open tasks only by default).

---

## Commands and binaries

- Run: **`node runtime/dist/cli.js <command>`** from the repository root, or **`cd runtime && node dist/cli.js`**, or **`npx ship-agent`** / **`ship-agent`** after **`npm install`** at the repo root (workspace).
- **`linear-agent`** remains a **bin alias** pointing at the same `runtime/dist/cli.js`.

See also: **`.env.example`** at the repo root for copy-paste variable names.

---

## Related

- [Tools overview](index.md) — Linear as reference adapter, secrets, Cloud Agent.
- [Examples → Reference org](../examples/elmundi/index.md) — workflow filenames and ElMundi-specific paths (`tools/linear-agent/` mirror).
