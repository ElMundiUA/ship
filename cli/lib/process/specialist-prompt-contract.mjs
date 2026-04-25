export const SPECIALIST_PROMPT_GUARDRAILS = `# Ship specialist execution guardrails

These rules apply to specialist prompts assembled from process configuration,
ticket context, workspace policies, and knowledge buckets.

## Knowledge First

Before inventing a solution or procedure, search Ship knowledge for relevant
recipes, patterns, policies, and technical context. Use repository-specific
knowledge first when a repo is known, then workspace knowledge. If no relevant
article is found, say that explicitly before proposing a new approach.

Knowledge articles can also answer clarifying technical questions when the
ticket lacks implementation detail.

## Allowed Exits

A specialist cycle may end only through a Ship-controlled outcome:

- Ask for clarification: return a clarification intent for Ship to post as a
  tracker comment and mirror into Inbox.
- Handoff: request one of the transitions explicitly configured in the process
  FSM. Ship validates the transition before any side effect.
- Complete with result: produce the final result or PR reference for Ship to
  record. Repository changes must be delivered through pull requests only.

## Boundaries

Do not perform direct ticket-system mutations. Do not execute transitions that
are not declared in the process configuration. Do not push directly to protected
branches or bypass review. All material actions must be represented in Ship
audit data. Workspace policies are mandatory and override recipe guidance.`;

export function renderSpecialistPromptGuardrails() {
  return `${SPECIALIST_PROMPT_GUARDRAILS.trim()}\n`;
}

export function buildSpecialistPromptBundle({
  process,
  state,
  allowedTransitions = [],
  ticket = null,
  policies = null,
}) {
  const specialist = normalizeSpecialist(state.specialist);
  const agentProfile =
    state.agent_profile || specialist.agent_profile || "auto";
  return {
    process: {
      id: process.id,
      name: process.name || process.id,
      primary: process.primary === true,
    },
    state: {
      id: state.id,
      name: state.name || state.id,
      instructions: typeof state.instructions === "string" ? state.instructions : "",
      triggers: Array.isArray(state.triggers) ? state.triggers : [],
      exit_conditions: Array.isArray(state.exit_conditions) ? state.exit_conditions : [],
      block_conditions: Array.isArray(state.block_conditions) ? state.block_conditions : [],
    },
    specialist,
    agent_profile: agentProfile,
    ticket,
    policies,
    allowed_transitions: allowedTransitions.map((transition) => ({
      from: transition.from,
      to: transition.to,
      condition: transition.condition || null,
    })),
    guardrails: renderSpecialistPromptGuardrails(),
  };
}

export function renderSpecialistPromptBundleMarkdown(bundle) {
  const lines = [
    "# Ship Specialist Prompt Bundle",
    "",
    "## Process",
    "",
    `- Process: ${bundle.process.name} (\`${bundle.process.id}\`)`,
    `- Primary: ${bundle.process.primary ? "yes" : "no"}`,
    `- State: ${bundle.state.name} (\`${bundle.state.id}\`)`,
    "",
    "## Specialist",
    "",
    `- Specialist: ${bundle.specialist.name} (\`${bundle.specialist.id}\`)`,
    `- Agent profile: \`${bundle.agent_profile}\``,
    "",
    bundle.specialist.role || "No specialist role description configured.",
    "",
  ];

  if (bundle.state.instructions) {
    lines.push("## State Instructions", "", bundle.state.instructions, "");
  }

  lines.push("## Ticket Context", "");
  if (bundle.ticket) {
    lines.push(...renderTicketLines(bundle.ticket), "");
  } else {
    lines.push("No ticket context was supplied. Use the Ship tracker picker before starting this specialist cycle.", "");
  }

  lines.push("## Workspace Policies", "");
  if (bundle.policies && bundle.policies.trim()) {
    lines.push(bundle.policies.trim(), "");
  } else {
    lines.push("Workspace policies were not supplied locally. In managed runs, Ship must inject enabled workspace policies before the agent starts.", "");
  }

  lines.push("## Allowed Transitions", "");
  if (bundle.allowed_transitions.length) {
    for (const transition of bundle.allowed_transitions) {
      const condition = transition.condition ? ` when ${transition.condition}` : "";
      lines.push(`- \`${transition.from}\` -> \`${transition.to}\`${condition}`);
    }
  } else {
    lines.push("- No outgoing transitions are configured for this state. The agent may only ask for clarification or complete with a result.");
  }

  lines.push(
    "",
    "## Required Guardrails",
    "",
    bundle.guardrails.trim(),
    "",
  );
  return `${lines.join("\n").trim()}\n`;
}

function normalizeSpecialist(value) {
  if (typeof value === "string") {
    return { id: value, name: value, role: "", agent_profile: null };
  }
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return {
      id: String(value.id || value.name || "specialist"),
      name: String(value.name || value.id || "Specialist"),
      role: typeof value.role === "string" ? value.role : "",
      agent_profile: typeof value.agent_profile === "string" ? value.agent_profile : null,
    };
  }
  return { id: "specialist", name: "Specialist", role: "", agent_profile: null };
}

function renderTicketLines(ticket) {
  const lines = [];
  for (const key of ["id", "key", "title", "url", "status", "description"]) {
    const value = ticket[key];
    if (typeof value === "string" && value.trim()) {
      lines.push(`- ${titleCase(key)}: ${value.trim()}`);
    }
  }
  const extra = Object.entries(ticket).filter(
    ([key, value]) =>
      !["id", "key", "title", "url", "status", "description"].includes(key) &&
      value != null &&
      typeof value !== "object",
  );
  for (const [key, value] of extra) {
    lines.push(`- ${titleCase(key)}: ${String(value)}`);
  }
  return lines.length ? lines : ["- Ticket context object was empty."];
}

function titleCase(value) {
  return value
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}
