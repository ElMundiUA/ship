export type AgentProfileOption = {
  id: string;
  name: string;
  description: string;
};

// The per-stage execution backend. Only options that the runtime
// actually honours are offered:
//   - "main" defers to the workspace-bound provider (the default).
//   - cursor_agent / codex_cli / claude_code pin a concrete CLI for THIS
//     stage, overriding the workspace provider at runtime.
// The legacy profiles (auto / cheaper / ship_cloud_agent / local_cli) are
// still accepted by the config schema for backwards-compat with existing
// .ship/config.yml files, but are intentionally not surfaced here: they
// never changed runtime routing and only confused operators. Configs that
// still carry them resolve to the workspace provider at run time.
export const AGENT_PROFILE_OPTIONS: AgentProfileOption[] = [
  {
    id: "main",
    name: "Workspace default",
    description: "Use the provider chosen for this workspace (Claude / Cursor / Codex).",
  },
  {
    id: "cursor_agent",
    name: "Cursor agent",
    description: "Run this stage on the Cursor CLI, regardless of the workspace default.",
  },
  {
    id: "codex_cli",
    name: "Codex CLI",
    description: "Run this stage on the OpenAI Codex CLI, regardless of the workspace default.",
  },
  {
    id: "claude_code",
    name: "Claude Code",
    description: "Run this stage on the Claude Code CLI, regardless of the workspace default.",
  },
];
