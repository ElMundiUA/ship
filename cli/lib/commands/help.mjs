export function printHelp() {
  console.log(`Ship CLI — methodology API (docs, patterns) + manifest catalogs (tools, workflows, collections).

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
  --base-url URL   API root (default: SHIP_API_BASE or http://127.0.0.1:8100)
  --json           Machine-readable JSON to stdout

MANIFEST COMMANDS (tools / workflows / collections)
  Read tools/manifest.json, workflows/manifest.json, collections/manifest.json from disk.
  Run inside the Ship clone or set SHIP_REPO to the repo root. No API server required.

INIT FLAGS
  --yes            Skip confirmation prompts (non-interactive)
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
   - Optionally list/show patterns for curated instruction slices (CLI: ship patterns list | ship patterns show <id> — same as GET /patterns)
   - POST /feedback only for retro-style notes (no secrets in free text) (CLI: ship docs feedback)

3. Run  ship init  in the TARGET repository (your product repo, not necessarily Ship).
   It detects .cursor/, AGENTS.md, CLAUDE.md, .codex/, or Copilot instructions and, after
   your confirmation, drops a focused rule or appends a markdown section the agent can read.

4. List tools/workflows/collections from manifests without the API:  ship tools list ,  ship workflows list ,  ship collections list  (and  show <id> ).

5. From CI or headless agents, call the same HTTP API with curl or fetch; use  ship … --json
   for stable parsing.

For full request/response schemas see documentation/tools/backend-api.md in the Ship repo.
`);
}
