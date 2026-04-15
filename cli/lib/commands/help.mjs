export function printHelp() {
  console.log(`Ship CLI — Ship methodology HTTP API (search, fetch, feedback, patterns, tools, workflows, collections) + init.

USAGE
  ship help
  ship docs search <query> [--top-k N]
  ship docs fetch <path>
  ship docs feedback --title "..." --summary "..." [--recommendation "…"]... [--source-context "…"]
  ship patterns list
  ship patterns show <pattern-id>
  ship tools list | ship tools show <id>
  ship workflows list | ship workflows show <id>
  ship collections list | ship collections show <id>
  ship init [--yes] [--force] [--dry-run] [--only <id>] [--cwd <dir>]

GLOBAL FLAGS
  --base-url URL   API root for ALL HTTP commands (default: SHIP_API_BASE or http://127.0.0.1:8100)
  --json           Machine-readable JSON to stdout

CATALOG COMMANDS (patterns, tools, workflows, collections)
  If cwd or SHIP_REPO points at a Ship clone: read manifests from disk.
  Otherwise: same base URL as docs — GET /patterns, /tools, /workflows, /collections (and /{id} for show).

INIT FLAGS
  --yes            Skip confirmation prompts (non-interactive; writes files — review plan with --dry-run first)
  --force          Overwrite / replace existing ship-cli blocks
  --dry-run        Print actions only
  --only <id>      Limit to one target: cursor | agents-md | claude-md | codex | copilot
  --cwd <dir>      Repository root (default: current directory)

BACKEND
  Start from Ship repo:  uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8100
  Env on server: OPENAI_API_KEY (/search), GITHUB_TOKEN (/feedback)

────────────────────────────────────────────────────────
Embedding in an agent (Cursor, Codex, Claude Code, etc.)
────────────────────────────────────────────────────────

1. Run the Ship backend locally (or deploy it) and set SHIP_API_BASE in the agent environment
   if the URL is not the default http://127.0.0.1:8100 .

2. Teach the agent this loop (or mirror it with curl):
   - POST /search with the user question → pick 1–3 paths from results (CLI: ship docs search)
   - POST /fetch for each chosen path → ground answers in those files (CLI: ship docs fetch)
   - Optionally list/show patterns (CLI: ship patterns list | ship patterns show <id> — GET /patterns on the same API, or disk in a Ship clone / SHIP_REPO)
   - POST /feedback only for retro-style notes (no secrets in free text) (CLI: ship docs feedback)

3. Run  ship init  in the TARGET repository (your product repo, not necessarily Ship).
   It detects .cursor/, AGENTS.md, CLAUDE.md, .codex/, or Copilot instructions and, after
   your confirmation, drops a focused rule or appends a markdown section the agent can read.

4. List patterns/tools/workflows/collections via the same API (or from disk in a clone / SHIP_REPO):  ship patterns list ,  ship tools list ,  etc.

5. From CI or headless agents, call the same HTTP API with curl or fetch; use  ship … --json
   for stable parsing.

For full request/response schemas see documentation/tools/backend-api.md in the Ship repo.
`);
}
