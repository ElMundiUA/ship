/**
 * Client-side inbox copy transforms — list rows and preview headers share
 * the same headline rules without a DB migration.
 */

import type { InboxItem } from "@/lib/inbox-types";

/** Human labels for raw ``intake_reason`` prefixes (tooltip only). */
export const INTAKE_REASON_LABELS: Record<string, string> = {
  "rule:user": "Routed to assignee",
  "rule:strategy:": "Rule strategy",
  "group:": "Group assignment",
  "builtin:": "Builtin handle",
  "fallback:": "Fallback routing",
  unresolved: "Unassigned",
  "unresolved:": "Unassigned (qualified)",
  agent_run_blocked: "Agent escalation",
  agent_run_clarification: "Agent clarification",
  agent_run_no_tracker: "No tracker bound",
  "manual:admin": "Manual assignment",
  knowledge_draft_review: "Knowledge draft review",
  knowledge_archive_review: "Knowledge archive review",
  "round_robin:": "Round-robin",
  tracker_outage: "Tracker outage",
  refire_capped: "Refire cap",
};

const AGENT_BLOCKED = /^agent blocked:\s*(.+)$/i;

type HeadlineInput = Pick<InboxItem, "title" | "type"> & {
  payload?: Record<string, unknown>;
};

/**
 * Map a raw inbox title to a one-line operator headline.
 * Unknown patterns pass through unchanged — no fabricated ticket refs.
 */
export function formatInboxHeadline(item: HeadlineInput): string {
  const title = item.title.trim();
  const payload = item.payload ?? {};
  const blocked = title.match(AGENT_BLOCKED);
  if (!blocked) return title;

  const stageFromTitle = blocked[1].trim();
  const stage =
    typeof payload.fsm_stage === "string" && payload.fsm_stage.trim()
      ? payload.fsm_stage.trim()
      : stageFromTitle;
  const ticketRef =
    typeof payload.ticket_ref === "string" ? payload.ticket_ref.trim() : "";

  if (ticketRef) {
    return `${ticketRef} ${stage} bounced — restart or skip?`;
  }
  if (typeof payload.fsm_stage === "string" && payload.fsm_stage.trim()) {
    return `${stage} bounced — restart or skip?`;
  }
  return title;
}

/** Tooltip copy for ``intake_reason``; unknown values show the raw string. */
export function formatIntakeReasonTooltip(intakeReason: string | null): string | undefined {
  if (!intakeReason) return undefined;
  for (const [prefix, label] of Object.entries(INTAKE_REASON_LABELS)) {
    if (intakeReason === prefix || intakeReason.startsWith(prefix)) {
      return label;
    }
  }
  return intakeReason;
}
