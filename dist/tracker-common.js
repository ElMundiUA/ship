/**
 * Shared helpers for REST-based tracker adapters (canonical issue shape matches Linear/Jira).
 */
import { READY_LABELS, FLOW_LABELS } from "./agent-contracts.js";

export function hasLabel(issue, labelName) {
  return (issue.labels?.nodes ?? []).some((l) => l.name === labelName);
}

export function matchesFilters(issue, filters) {
  if (filters.role && !hasLabel(issue, READY_LABELS[filters.role])) return false;
  if (filters.withoutRole === "ba" && !hasLabel(issue, FLOW_LABELS.noBa)) return false;
  if (filters.status && issue.state?.name !== filters.status) return false;
  if (filters.labels?.length) {
    for (const lbl of filters.labels) {
      if (!hasLabel(issue, lbl)) return false;
    }
  }
  return true;
}

/** Workflow state labels on GitHub: exclusive prefix so we do not collide with ship role labels. */
export const GITHUB_STATUS_PREFIX = "ship-status:";

export function githubStatusLabel(stateName) {
  return `${GITHUB_STATUS_PREFIX}${stateName}`;
}

export function parseGithubWorkflowState(labels, closed) {
  if (closed) return "Done";
  const nodes = labels ?? [];
  for (const l of nodes) {
    const n = typeof l === "string" ? l : l.name;
    if (n?.startsWith(GITHUB_STATUS_PREFIX)) {
      return n.slice(GITHUB_STATUS_PREFIX.length) || "Open";
    }
  }
  return "Open";
}
