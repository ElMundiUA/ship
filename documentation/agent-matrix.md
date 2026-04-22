# Agent matrix

Reference table for the 13 agent runtimes Ship supports out of the box. For each: the on-disk marker `shipctl doctor` looks for, the install target the rule body lands at, and the adapter artifact that owns the contract.

For *how* installation works (commands, flags, ordering), see [/cli](/cli). For *why* this contract looks the way it does, see [Discovery](/docs/discovery) (the agent-side interview) and [Protocol → RFC-0001](/docs/protocol/rfc-0001-artifacts-protocol).

## Canonical sources

| Source | Purpose |
|--------|---------|
| `collection/agent-rules-<agent>` | Per-agent rule artifact (RFC-0004); `shipctl init --copy-rules` installs it. |
| `collection/preset-<preset>` | Per-preset bootstrap content (CI workflow, labels, secrets). |
| [`pattern/adopt-ship-generic`](/patterns/adopt-ship-generic) | Universal adoption prompt for the discovery interview. |
| [Discovery contract](/docs/discovery) | Interactive Phase 0–4 contract the agent runs before its first PR. |

## Detection + injection matrix

| Agent id | Marker file(s) / dir(s) | Install target | Adapter artifact (`collection`) | Install / invoke | Example `shipctl init` |
|----------|-------------------------|----------------|---------------------------------|------------------|------------------------|
| `cursor` | `.cursor/`, `.cursor/rules/` | `.cursor/rules/ship-artifacts-protocol.mdc` | `collection/agent-rules-cursor` | Open repo in Cursor IDE. | `npx @elmundi/ship-cli init --agents cursor --copy-rules` |
| `agents-md` | `AGENTS.md` | `AGENTS.md` | `collection/agent-rules-agents-md` | Generic / Codex convention; file lives at repo root. | `npx @elmundi/ship-cli init --agents agents-md --copy-rules` |
| `claude-md` | `CLAUDE.md` | `CLAUDE.md` | `collection/agent-rules-claude-md` | Install Claude Code CLI, `claude` in repo. | `npx @elmundi/ship-cli init --agents claude-md --copy-rules` |
| `claude` | `CLAUDE.md`, `.claude/` | `CLAUDE.md` | `collection/agent-rules-claude` | Claude Code, local + SaaS. | `npx @elmundi/ship-cli init --agents claude --copy-rules` |
| `codex` | `.codex/` | `.codex/SHIP_API.md` | `collection/agent-rules-codex` | `npm i -g @openai/codex`, then `codex` in repo. | `npx @elmundi/ship-cli init --agents codex --copy-rules` |
| `copilot` | `.github/copilot-instructions.md` | `.github/copilot-instructions.md` | `collection/agent-rules-copilot` | GitHub Copilot in IDE. | `npx @elmundi/ship-cli init --agents copilot --copy-rules` |
| `aider` | `.aider.conf.yml`, `.aider/`, `AIDER.md` | `AIDER.md` | `collection/agent-rules-aider` | `pipx install aider-chat`, then `aider` in repo. | `npx @elmundi/ship-cli init --agents aider --copy-rules` |
| `cline` | `.clinerules`, `.rooignore` | `.clinerules` | `collection/agent-rules-cline` | Cline / Roo Cline extension in VS Code. | `npx @elmundi/ship-cli init --agents cline --copy-rules` |
| `continue` | `.continue/config.json`, `.continue/config.yaml` | `.continue/ship.md` | `collection/agent-rules-continue` | Continue.dev extension in VS Code / JetBrains. | `npx @elmundi/ship-cli init --agents continue --copy-rules` |
| `windsurf` | `.windsurfrules` | `.windsurfrules` | `collection/agent-rules-windsurf` | Windsurf IDE (Codeium). | `npx @elmundi/ship-cli init --agents windsurf --copy-rules` |
| `zed` | `.zed/`, `.zed/settings.json` | `.zed/ship.md` | `collection/agent-rules-zed` | Zed editor with AI enabled. | `npx @elmundi/ship-cli init --agents zed --copy-rules` |
| `gemini` | `GEMINI.md`, `.gemini/` | `GEMINI.md` | `collection/agent-rules-gemini` | `npm i -g @google/gemini-cli`, then `gemini` in repo. | `npx @elmundi/ship-cli init --agents gemini --copy-rules` |
| `opencode` | `.opencode/` | `.opencode/ship.md` | `collection/agent-rules-opencode` | OpenCode CLI. | `npx @elmundi/ship-cli init --agents opencode --copy-rules` |
| `cursor-cloud` | `.cursor/environments.json` | `.cursor/environments.json` (marker-guarded; never overwrites existing file) | `collection/agent-rules-cursor-cloud` | Cursor Cloud Agents. | `npx @elmundi/ship-cli init --agents cursor-cloud --copy-rules` |

Multiple agents at once:

```bash
npx @elmundi/ship-cli init --agents cursor,codex,claude-md,aider --copy-rules
```

The cached `collection/agent-rules-<agent>` artifact is the single source of
truth for what gets written to `install_target`. Bumping the artifact bumps
the `<!-- ship-cli: installed-from collection/agent-rules-<id>@<v> -->`
footer; `shipctl verify --check rules-markers` reports drift.

## Delivery modes

Ship is agent-runtime agnostic; the same `<kind>:<id>@<version>` evidence
trail works wherever the agent is hosted. Three modes cover the supported
matrix:

### Interactive IDE

For Cursor, Claude Code, Continue, Windsurf, Zed, Cline, Aider, Gemini.
The agent runs next to the human in the editor; `shipctl init --copy-rules`
installs the rule body at the per-agent `install_target` and the IDE picks
it up on the next prompt. Discovery interview happens conversationally;
the human applies edits with confirmation.

### Headless CI

For Codex CLI, Aider in batch mode, OpenCode, Claude Code in non-interactive
runs. The agent executes inside a CI runner with `SHIP_TELEMETRY=false` and
`shipctl --yes`. Cached artifacts come from the previous successful sync;
`artifacts.pins` keeps the run reproducible. `shipctl verify --no-network`
gates the PR before merge.

### Cloud agents

For Cursor Cloud, GitHub Copilot tasks, Claude in SaaS mode, Codex Cloud.
The agent runs server-side without local repo access; `shipctl init
--agents cursor-cloud` writes a marker-guarded `.cursor/environments.json`
(or equivalent) so the cloud platform can re-create the environment with
the right secrets. `shipctl doctor --json` is the contract surface — the
cloud agent reads it instead of running its own detection.

## One-liner helper

Thin wrapper around the command above; good for `curl | bash` onboarding:

```bash
curl -fsSL https://raw.githubusercontent.com/ElMundiUA/ship/main/adopt-ship.sh | bash
```

Env overrides:

- `SHIP_AGENTS=cursor,codex` → forwarded as `--agents`.
- `SHIP_NONINTERACTIVE=1` → forwarded as `--yes`.

Treat the launcher as convenience; `shipctl init` is the canonical contract.

## Required behavior across all agent surfaces

- Resolve artifacts via `shipctl <kind> show <id>` before applying them.
- Record `<kind>:<id>@<version>` for every consumed artifact in the PR description.
- Never copy artifact bodies into the repo; reference the id + version.
- Feedback via `shipctl feedback draft` — opt-in, never auto-submitted.
- Ask discovery questions before making assumptions; never commit secrets.
- Run `shipctl verify` (`--no-network` is acceptable in CI) before requesting review.
