/**
 * Static inbox rows for Playwright visual regression (ELS-146).
 * Only served from `/e2e/inbox-mailbox` when `SHIP_E2E_INBOX_VISUAL=1`.
 */

import { INBOX_TYPES, type InboxItem } from "@/lib/inbox-types";

const FIXTURE_CREATED = "2026-05-01T12:00:00.000Z";

function baseRow(type: (typeof INBOX_TYPES)[number], index: number): InboxItem {
  return {
    id: `els146-fixture-${type}`,
    workspace_id: "e2e-fixture-ws",
    repo_id: null,
    type,
    status: "new",
    title:
      type === "blocker"
        ? "agent blocked: validation"
        : `ELS-146 visual fixture ${type}`,
    summary: null,
    intake_handle: null,
    intake_reason: type === "clarification" ? "agent_run_clarification" : null,
    owner: null,
    play_key: null,
    run_id: null,
    created_at: FIXTURE_CREATED,
    due_at: null,
    snoozed_until: null,
    resolved_at: null,
    resolution: null,
    category:
      type === "failure" || type === "blocker"
        ? "failure"
        : type === "report" ||
            type === "stuck" ||
            type === "approval" ||
            type === "exception" ||
            type === "improvement"
          ? "attention"
          : "decision_needed",
    priority: index,
    lane: "now",
    resolution_mode: "single_choice",
    action_item_count: type === "report" ? 3 : 0,
    ticket_ref: type === "blocker" ? "ELS-99" : null,
    fsm_stage: type === "blocker" ? "validation" : null,
  };
}

export const INBOX_VISUAL_MIXED_ITEMS: InboxItem[] = INBOX_TYPES.map((type, i) =>
  baseRow(type, i),
);
