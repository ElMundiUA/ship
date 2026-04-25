export type AgentProfileOption = {
  id: string;
  name: string;
  description: string;
};

export const AGENT_PROFILE_OPTIONS: AgentProfileOption[] = [
  {
    id: "auto",
    name: "Auto",
    description: "Let Ship choose the backend from workspace policy and task needs.",
  },
  {
    id: "main",
    name: "Main workspace agent",
    description: "Use the default agent profile configured for this workspace.",
  },
  {
    id: "cheaper",
    name: "Cheaper profile",
    description: "Prefer lower-cost execution for low-risk work.",
  },
  {
    id: "cursor_agent",
    name: "Cursor agent",
    description: "Prefer repo-local coding work in Cursor.",
  },
  {
    id: "codex_cli",
    name: "Codex CLI",
    description: "Prefer local CLI execution for code and docs tasks.",
  },
  {
    id: "ship_cloud_agent",
    name: "Ship cloud agent",
    description: "Prefer managed cloud execution for background or scheduled work.",
  },
  {
    id: "local_cli",
    name: "Local CLI only",
    description: "Keep execution on a local runner for sensitive repositories.",
  },
];
