export function printHelp() {
  console.log(`Ship CLI — methodology on ship.elmundi.com (or SHIP_API_BASE) + init.

ONE FLOW
  1) ship search <query>     — vector search (POST /search) over docs + prompts + README
  2) ship docs fetch <path>  — full markdown file by repo-relative path (POST /fetch { path })
     ship pattern|tool|workflow|collection fetch <id> — catalog entry body (POST /fetch { kind, id })
  3) ship docs feedback …   — improvement / retro note (POST /feedback)

COMMANDS
  ship help
  ship search <query> [--top-k N]

  ship docs fetch <repo-relative-path>
  ship docs feedback --title "..." --summary "..." [--recommendation "…"]... [--source-context "…"]

  ship pattern list | ship pattern show <id> | ship pattern fetch <id> | ship pattern search <query> [--top-k N]
  ship tool … | ship workflow … | ship collection …   (same subcommands; plural aliases: patterns, tools, …)

  ship init [--yes] [--force] [--dry-run] [--only <id>] [--cwd <dir>]

GLOBAL FLAGS
  --base-url URL   Methodology API (default: SHIP_API_BASE or https://ship.elmundi.com/api/methodology)
  --json           Machine-readable JSON

LOCAL TREE
  pattern / tool / workflow / collection list|show|fetch read manifests from disk when cwd or SHIP_REPO
  is inside the Ship monorepo (search always uses HTTP).

INIT FLAGS
  --yes       Non-interactive apply (use --dry-run first)
  --force     Replace existing injected blocks
  --dry-run   Preview only
  --only      cursor | agents-md | claude-md | codex | copilot
  --cwd       Target repo root

For HTTP schemas see documentation/tools/backend-api.md in the Ship repo.
`);
}
