export function printHelp() {
  console.log(`shipctl — local workbench for a Ship-connected repo.

The console is for the operator. The CLI is for the engineer. shipctl
runs locally for setup, validation, and diagnostics. It does not
orchestrate workflows, does not touch workspace state, and does not
write to the audit log. The customer-side CI uses 'shipctl trigger'
(and the trigger-spawned 'shipctl run') as a thin entry-point that
hands work to the workspace runner; everything else is local.

GLOBAL FLAGS
  --base-url URL   Workspace API (default: SHIP_API_BASE or
                   https://api.ship.elmundi.com)
  --json           Machine-readable JSON output where supported
  --version, -v    Print shipctl version and exit
  --help, -h       Print this help

COMMANDS

  Daily-use (local)
    shipctl doctor [--json] [--cwd <dir>] [--write-inventory] [--no-network]
                                       — cheap repo health checks: agent rule
                                         files installed, config valid,
                                         credentials present, network reachable.
    shipctl verify [--no-network] [--check <id,...>] [--severity warn|error|info] [--json]
                                       — heavier post-adoption checks
                                         (config schema, agent rule presence,
                                         tracker / CI wiring).
    shipctl config init|get|set|validate|show|path
                                       — .ship/config.yml management.

  Setup
    shipctl init [--yes] [--force] [--dry-run] [--json] [--cwd <dir>]
                 [--agents <csv>] [--tracker <name>] [--ci <name>]
                 [--preset <name>] [--language <name>] [--channel stable|edge]
                 [--copy-playbook] [--bootstrap]
                 [--telemetry on|off|ask]
                                       — bootstrap .ship/ in an existing repo.
                                         Agent rule files are baked into the
                                         wizard's seed PR — Phase 2.5 retired
                                         the local --copy-rules + sync flow.

  Knowledge (read-only)
    shipctl knowledge fetch <bucket-slug> [--workspace <id>] [--json]
                                       — read a Ship-owned bucket's articles
                                         and source sync state. Bucket
                                         authoring + ingestion live server-side;
                                         this is the agent's read path.

  CI entry-point (used by the seed workflow)
    shipctl trigger --event schedule [--workspace <id>] [--repo <id|owner/name>] [--json]
                                       — compute due routines from
                                         .ship/config.yml and claim each
                                         schedule window in Ship.
    shipctl run --routine <id> [--dry-run] [--json] [--cwd <dir>]
                                       — execute one routine end-to-end:
                                         resolve specialist, fetch a ticket
                                         (if FSM-staged), launch the agent
                                         runtime, exit on terminal status.
                                         Spawned per routine by 'shipctl trigger'
                                         in the seed workflow.
    shipctl local "<ask>" [--role <slug>] [--provider <name>] [--dry-run] [--escalate] [--json]
                                       — local scratch session (trigger a):
                                         throwaway worktree, no ticket, no
                                         lease, stops before push; offers
                                         a→b escalation via ticket create.
                                         Gated by local_executor.enabled.

  Telemetry & feedback
    shipctl telemetry status|on|off|show-id|reset-id|flush|export|delete-my-data|buffer
                                       — opt-in anonymous usage (RFC-0003); default OFF.
    shipctl feedback draft|list|show|edit|submit|remove
                                       — local markdown drafts; submit creates a
                                         GitHub issue against the cited artifact
                                         (POST /feedback) and moves the draft to sent/.

  Misc
    shipctl help                       — show this help.

INIT FLAGS
  --yes              Non-interactive apply (use --dry-run first)
  --force            Replace existing rule blocks and overwrite generated files
  --dry-run          Preview only
  --json             Emit a JSON summary suitable for CI
  --agents <csv>     Comma-separated agent ids (informational; the wizard's
                     seed PR is the install path now)
  --tracker <name>   Stack tracker: linear|jira|github-issues|azure-boards|clickup|spreadsheet|none
  --ci <name>        Stack CI: gh-actions|gitlab-ci|buildkite|circleci|azure-pipelines|jenkins|manual
  --preset <name>    Stack preset: web-app|api-backend|mobile-app|cli|monorepo|adoption-minimum
  --language <name>  Stack language: ts|js|py|go|rust|java|kotlin|swift|dart|multi
  --channel <name>   Override api.channel: stable|edge
  --copy-playbook    (no-op after Phase 2.5; flag retained for back-compat)
  --bootstrap        Render CI/tracker scaffolding (mobile-app+gh-actions+linear skeletons today;
                     other combos emit SHIP_BOOTSTRAP_PLAN.md)
  --telemetry        on|off|ask — override the interactive telemetry prompt
  --cwd              Target repo root

SUPPORTED AGENTS
  cursor, codex, claude, aider, cline, continue, windsurf, zed,
  gemini, opencode, copilot, cursor-cloud, agents-md, claude-md

Package: @elmundi/ship-cli (binary: shipctl).
`);
}
