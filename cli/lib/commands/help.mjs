export function printHelp() {
  console.log(`shipctl — adopt Ship in a repo, sync the catalog, run routines, report outcomes.

Bootstrap a new or existing repo (init / new / doctor), pull the
methodology catalog into .ship/cache (sync), execute one-shot routines or
emit prompts for the workspace runner (trigger / run / kickoff /
callback). Talks to the methodology + orchestration APIs over HTTPS.

VOCABULARY
  process.routines: (.ship/config.yml) → repo-local scheduled/manual work
  pattern:          (artifact kind)    → cached role/playbook prompt body
  routine claim:    (Ship API)         → idempotent schedule-window claim
  attention surface                    → operator console: Inbox

Legacy 'lanes:' configs and '--lane' flags are still accepted as aliases
so already-seeded repositories keep working while new repos use
'process.routines'.

GLOBAL FLAGS
  --base-url URL   Methodology API (default: SHIP_API_BASE or
                   https://ship.elmundi.com/api/methodology)
  --json           Machine-readable JSON output where supported
  --version, -v    Print shipctl version and exit
  --help, -h       Print this help

COMMANDS

  Setup
    shipctl init [--yes] [--force] [--dry-run] [--json] [--cwd <dir>]
                 [--agents <csv>]
                 [--tracker <name>] [--ci <name>] [--preset <name>]
                 [--language <name>] [--channel stable|edge]
                 [--copy-rules] [--copy-playbook] [--bootstrap]
                 [--telemetry on|off|ask]
                                       — bootstrap .ship/, fetch artifacts, install
                                         agent rules in an existing repo.
    shipctl new <name> [--preset ...] [--tracker ...] [--ci ...] [--agents ...]
                       [--here] [--yes]
                                       — bootstrap a fresh repo: git init + README +
                                         .ship/config.yml.
    shipctl doctor [--json] [--cwd <dir>] [--write-inventory] [--no-network]
                                       — inspect the repo, propose a stack, optionally
                                         write .ship/inventory.json for
                                         'shipctl init --bootstrap'.
    shipctl config init|get|set|validate|show|path
                                       — .ship/config.yml management.

  Catalog
    shipctl search <query> [--top-k N]
                                       — vector search over docs + prompts (POST /search).
    shipctl docs fetch <repo-relative-path>
    shipctl docs feedback --title "..." --summary "..." [--recommendation "..."]...
                          [--source-context "..."]
                                       — fetch markdown bodies; submit improvement /
                                         retro notes (POST /feedback).
    shipctl pattern list | shipctl pattern show <id> | shipctl pattern fetch <id>
                                     | shipctl pattern search <query> [--top-k N]
                                       — versioned artifact bodies (POST /fetch
                                         { kind, id, version? }). 'pattern' is the
                                         protocol-stable artifact kind; in the operator
                                         console it shows up as a Play.
    shipctl tool …       | shipctl collection …
                                       — same subcommands; plural aliases:
                                         patterns, tools, collections.
    shipctl sync [--check-only] [--only <kind:id>]... [--channel <c>]
                 [--force-unpin] [--dry-run] [--lock] [--json] [--cwd <dir>]
                                       — fetch artifacts into .ship/cache. With --lock,
                                         also writes .ship/shipctl.lock.json covering
                                        every pattern the declared routines depend on.

  Run
    shipctl trigger --event schedule --repo <id|owner/name> [--workspace <id>] [--json]
                                       — compute due routines locally, then claim
                                         the schedule window in Ship.
    shipctl run --routine <id> [--dry-run] [--json] [--cwd <dir>]
                                       — execute one routine end-to-end:
                                         resolve pattern, fetch a ticket
                                         (if FSM-staged), launch the agent
                                         runtime, exit on terminal status.
                                         'shipctl agent-run' is a back-compat
                                         alias.
    shipctl lanes install [--only <csv>] [--ref <git-ref>] [--owner <gh>] [--repo <name>]
                          [--shipctl-version <v>] [--dry-run] [--force] [--json] [--cwd <dir>]
    shipctl lanes list   [--json] [--cwd <dir>]
    shipctl lanes remove [--only <csv>] [--dry-run] [--json] [--cwd <dir>]
                                       — generate / inspect / delete the
                                         .github/workflows/ship-<lane>.yml thin wrappers
                                         that delegate to the reusable run-agent.yml.
    shipctl kickoff [--pattern <id>] [--version <v>] [--raw] [--json] [--cwd <dir>]
                                       — print a pattern body for piping into the
                                         customer's agent in CI.
    shipctl callback --status <ok|fail|cancelled> [--summary "..."] [--metric k=v]...
                     [--outcome-text "..."] [--findings-count N] [--severity high=N]...
                     [--artifact pr:"..."]... [--escalation clarification:"..."]...
                                       — report a Run's terminal status (and
                                         RunSummary outcome) back to Ship so it can
                                         render the outcome row and route any
                                         escalations into the Inbox.
    shipctl process prompt --state <id> [--ticket-json <json>] [--policies-file <path>]
                           [--cwd <dir>] [--json]
                                      — assemble a Process/FSM specialist prompt
                                        bundle with ticket context, allowed
                                        transitions, policies, and mandatory
                                        knowledge-first guardrails.
    shipctl process tickets --workspace <id> [--query <text>] [--tracker <kind>] [--json]
                                      — read-only tracker picker for selecting
                                        ticket context before building a process
                                        prompt. Does not create, comment, or
                                        transition tickets.

  Knowledge
    shipctl knowledge init [--workspace <id>] [--repo <id|owner/name>] [--only <csv>] [--json]
                                       — compatibility: open a PR that seeds
                                         .ship/knowledge starter docs.
    shipctl knowledge fetch <bucket-slug> [--workspace <id>] [--json]
                                       — read Ship-owned bucket articles and
                                         source sync state.
    shipctl knowledge bootstrap [--workspace <id>] [--repo <id|owner/name>] [--json]
                                       — post-merge action entry point: analyze
                                         repo and open generated knowledge PR.
    shipctl knowledge refresh-intel [--workspace <id>] [--repo <id|owner/name>] [--json]
                                       — refresh the generated repository-context
                                         bucket for an activated repo.
                                         Reads SHIP_API_TOKEN.

  Telemetry & feedback
    shipctl telemetry status|on|off|show-id|reset-id|flush|export|delete-my-data|buffer
                                       — opt-in anonymous usage (RFC-0003); default OFF.
                                         '--scope artifact_usage,improvement_drafts,errors'
                                         on 'on'; '--dry-run' on 'flush';
                                         '--out <file>' on 'export'.
    shipctl feedback draft|list|show|edit|submit|remove
                                       — local markdown drafts; submit creates a
                                         GitHub issue via POST /feedback and moves the
                                         draft to sent/.

  Misc
    shipctl verify [--no-network] [--check <id,...>] [--severity warn|error|info] [--json]
                                       — post-adoption liveness checks
                                         (local + config + network).
    shipctl migrate [--dry-run] [--yes] [--json] [--cwd <dir>]
                                       — upgrade .ship/config.yml from v1 to v2
                                         (lanes-as-config).
    shipctl bootstrap   (stub)
    shipctl help                       — show this help.

LOCAL TREE
  pattern / tool / collection list|show|fetch scan
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

REFERENCE
  Artifacts protocol: RFC-0001 (POST /search, POST /fetch). Every consumed
  artifact should be recorded in the PR as \`<kind>:<id>@<version>\`.
  HTTP schemas: artifacts/tools/methodology-api/ARTIFACT.md in the Ship repo.
  Operator IA (Plays / Automations / Runs / Inbox): RFC-0010.

Package: @elmundi/ship-cli (binary: shipctl).
`);
}
