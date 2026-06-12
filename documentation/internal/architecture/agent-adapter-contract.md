# Agent-adapter contract (thesis 5, ELS-245)

Ship is a **superstructure over coding agents**: it spawns, controls
and injects context. It never re-implements the agent — not the
file-edit loop, not git, not the tool loop, not even locally.

## The two-point coupling rule

An adapter under `packages/cli/lib/agents/` may couple to its tool
through **exactly two points**:

1. **The non-interactive invocation contract** — the tool's binary +
   the handful of headless flags, prompt in:
   - `cursor-agent` (Cursor CLI)
   - `claude --print --dangerously-skip-permissions --output-format stream-json`
   - `codex exec --full-auto --skip-git-repo-check <prompt>`
   - `shipctl run` (the thesis-6 self-spawn, which bottoms out in one
     of the above)
2. **"The agent commits to the branch Ship checked out"** — plus the
   exit code. The runner owns checkout, push, and the PR.

Everything Ship knows — agent-roles, policies, `.ship/knowledge`,
Lighthouse retrieval, the autonomy preamble — enters **through the
prompt/context**. The adapter is a dumb spawn.

## Why: the maintenance math

The headless CLI is the most stable surface each vendor has (their own
CI depends on it). Coupling only to it means: a vendor change is a
~30-LOC adapter fix, version-pinnable, isolated. Ship supports one
entrypoint per tool — not "everything they do" — and rides every
coding agent while betting on none. A new tool is a +30-LOC adapter.

## The two rejected patterns (do not reintroduce)

1. **Variant A — tool-native config.** Delivering Ship's value via
   `.cursorrules`, per-tool `CLAUDE.md`, codex config files, hooks or
   slash commands. That is N foreign roadmaps of maintenance, breaks
   on every vendor format change, and — decisive — config cannot hold
   the control plane (lease/cap/cascade live in Postgres, thesis 2).
   The moment an adapter writes a tool-native config artifact, we have
   slid into Variant A. The lint below fails the build for it.
2. **The inversion — Ship as an MCP tool the agents call.** That flips
   the control direction: the agent would drive Ship. Ship spawns the
   agent as a subprocess; the agent never drives Ship.

## Per-tool rationale snapshot (2026-06)

| Tool | Invocation | Tool-specific tail we deliberately forgo |
|---|---|---|
| Cursor | `cursor-agent` headless | repo indexing, cloud agents |
| Claude Code | `--print` + `stream-json` | MCP servers, subagents, hooks |
| Codex | `codex exec --full-auto` | profiles, sandbox modes |
| ship (self-spawn) | nested `shipctl run` | — (dogfood-gated, ELS-241/242) |

Forgoing the per-tool bells is the trade: agent-agnosticism **is** the
moat.

## Enforcement

- The CONTRACT docblock at the top of `packages/cli/lib/agents/index.mjs`
  states the rule at the point of change.
- `apps/backend/tests/test_agent_adapter_discipline.py` fails CI if any
  adapter file references a tool-native config artifact in a
  read/write context.
