export function printHelp() {
  console.log(`Ship CLI — artifacts protocol on ship.elmundi.com (or SHIP_API_BASE) + init.

ARTIFACTS PROTOCOL (RFC-0001)
  1) shipctl search <query>          — vector search (POST /search) over docs + prompts
  2) shipctl docs fetch <path>       — full markdown body by repo-relative path
     shipctl pattern|tool|workflow|collection show|fetch <id>
                                     — versioned artifact body (POST /fetch { kind, id, version? })
  3) shipctl docs feedback …         — improvement / retro note (POST /feedback)

Every consumed artifact should be recorded in the PR as \`<kind>:<id>@<version>\`.

COMMANDS
  shipctl help
  shipctl search <query> [--top-k N]

  shipctl docs fetch <repo-relative-path>
  shipctl docs feedback --title "..." --summary "..." [--recommendation "…"]... [--source-context "…"]

  shipctl pattern list | shipctl pattern show <id> | shipctl pattern fetch <id> | shipctl pattern search <query> [--top-k N]
  shipctl tool … | shipctl workflow … | shipctl collection …   (same subcommands; plural aliases: patterns, tools, …)

  shipctl init [--yes] [--force] [--dry-run] [--json] [--cwd <dir>]
               [--agents <csv>]
               [--tracker <name>] [--ci <name>] [--preset <name>]
               [--language <name>] [--channel stable|edge]
               [--copy-rules] [--copy-playbook] [--bootstrap]
               [--telemetry on|off|ask]

  shipctl doctor [--json] [--cwd <dir>] [--write-inventory] [--no-network]
                                     — inspect the repo, propose a stack, optionally write
                                       .ship/inventory.json for 'shipctl init --bootstrap'.

  shipctl config init|get|set|validate|show|path     — .ship/config.yml management
  shipctl sync [--check-only] [--only <kind:id>...] [--channel <c>] [--force-unpin] [--dry-run]

  shipctl new <name> [--preset ...] [--tracker ...] [--ci ...] [--agents ...] [--here] [--yes]
                                     — bootstrap a fresh repo: git init + README + .ship/config.yml
  shipctl verify [--no-network] [--check <id,...>] [--severity warn|error|info] [--json]
                                     — post-adoption liveness checks (local + config + network)
  shipctl telemetry status|on|off|show-id|reset-id|flush|export|delete-my-data|buffer
                                     — opt-in anonymous usage (RFC-0003); default OFF.
                                       '--scope artifact_usage,improvement_drafts,errors' on 'on'
                                       '--dry-run' on 'flush'; '--out <file>' on 'export'
  shipctl feedback draft|list|show|edit|submit|remove
                                     — local markdown drafts; submit creates a GitHub issue
                                       via POST /feedback and moves the draft to sent/.
  shipctl callback --status <ok|fail|cancelled> [--summary "..."] [--metric k=v]...
                                     — report a pipeline run's terminal status to Ship.
                                       Used inside workflow.yml 'if: always()' steps;
                                       reads SHIP_RUN_TOKEN + SHIP_CALLBACK_URL from env.
  shipctl kickoff [--pattern kickoff] [--version …] [--raw] [--json] [--cwd …]
                                     — print the kickoff / workload pattern body for piping
                                       into the customer's agent in CI (see artifacts/patterns/kickoff).
  shipctl migrate [--dry-run] [--yes] [--json] [--cwd …]
                                     — upgrade .ship/config.yml from v1 to v2 (lanes-as-config).
  shipctl run --lane <id> [--trigger event|schedule|manual|once]
              [--dry-run] [--offline] [--json] [--cwd …]
              [--ship-run-id …] [--ship-callback-url …] [--ship-run-token …]
                                     — RFC-0007 entry-point: resolve a lane from
                                       .ship/config.yml, fetch its pattern, check idempotency,
                                       emit the prompt, and report the callback.
  shipctl knowledge init [--workspace <id>] [--repo <id|owner/name>] [--only <csv>] [--json]
                                     — open a PR that seeds .ship/knowledge/*.md starter
                                       buckets (code-style, ui-runbook). Reads SHIP_API_TOKEN.
  shipctl bootstrap   (stub)

GLOBAL FLAGS
  --base-url URL   Methodology API (default: SHIP_API_BASE or https://ship.elmundi.com/api/methodology)
  --json           Machine-readable JSON

LOCAL TREE
  pattern / tool / workflow / collection list|show|fetch scan
  artifacts/<plural>/<id>/ARTIFACT.md on disk when cwd or SHIP_REPO is inside
  the Ship monorepo (search always uses HTTP).

INIT FLAGS
  --yes              Non-interactive apply (use --dry-run first)
  --force            Replace existing rule blocks and overwrite generated files
  --dry-run          Preview only
  --json             Emit a JSON summary suitable for CI
  --agents <csv>     Comma-separated agent ids. See list below.
  --tracker <name>   Stack tracker: linear|jira|github-issues|azure-boards|clickup|spreadsheet|none
  --ci <name>        Stack CI: gh-actions|gitlab-ci|buildkite|circleci|azure-pipelines|jenkins|manual
  --preset <name>    Stack preset: web-app|api-backend|mobile-app|cli|monorepo|adoption-minimum
  --language <name>  Stack language: ts|js|py|go|rust|java|kotlin|swift|dart|multi
  --channel <name>   Override api.channel: stable|edge
  --copy-rules       Install collection/agent-rules-<agent> files at their install_target
  --copy-playbook    Fetch collection/adoption-playbook into .ship/cache/ (skipped on 404)
  --bootstrap        Render CI/tracker scaffolding (mobile-app+gh-actions+linear skeletons today;
                     other combos emit SHIP_BOOTSTRAP_PLAN.md)
  --telemetry        on|off|ask — override the interactive telemetry prompt
  --cwd              Target repo root

SUPPORTED AGENTS
  cursor, codex, claude, aider, cline, continue, windsurf, zed,
  gemini, opencode, copilot, cursor-cloud, agents-md, claude-md

For HTTP schemas see artifacts/tools/methodology-api/ARTIFACT.md in the Ship repo.
Package: @elmundi/ship-cli (binary: shipctl).
`);
}
