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
