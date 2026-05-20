/**
 * Frontend type contract for the Inbox v1 surface (RFC-0010).
 *
 * Mirrors the Pydantic shapes returned by the Phase-2 backend:
 *   - GET /v1/workspaces/{ws}/inbox             → InboxListResponse
 *   - GET /v1/workspaces/{ws}/inbox/{id}        → InboxItemDetail
 *
 * Kept dependency-free so it can be imported from server components,
 * route handlers, and pure helpers without dragging in React. The
 * backend tickets (P2-03..P2-09) ship the actual API; the existing
 * `/inbox` stub will swap from legacy clarifications/improvements
 * to these shapes once the Phase-2 routes land.
 */

export const INBOX_TYPES = [
  "clarification",
  "improvement",
  "failure",
  "approval",
  "exception",
  "stuck",
  "blocker",
  "report",
] as const;
export type InboxType = (typeof INBOX_TYPES)[number];

export const INBOX_STATUSES = [
  "new",
  "snoozed",
  "resolved",
  "dismissed",
] as const;
export type InboxStatus = (typeof INBOX_STATUSES)[number];

export const INBOX_CATEGORIES = [
  "decision_needed",
  "failure",
  "attention",
] as const;
export type InboxCategory = (typeof INBOX_CATEGORIES)[number];

export const INBOX_LANES = ["now", "today", "whenever"] as const;
export type InboxLane = (typeof INBOX_LANES)[number];

export const INBOX_LANE_META: Record<
  InboxLane,
  { label: string; tone: string }
> = {
  now: { label: "Now", tone: "text-coral" },
  today: { label: "Today", tone: "text-sun" },
  whenever: { label: "Whenever", tone: "text-white/55" },
};

export const INBOX_RESOLUTIONS = [
  "answered",
  "approved",
  "rejected",
  "accepted",
  "dismissed",
  "retried",
  "acknowledged",
] as const;
export type InboxResolution = (typeof INBOX_RESOLUTIONS)[number];

/**
 * Type-level metadata: label, short blurb, ordering.
 *
 * Shared between the navigation chips, the list filters, and the
 * detail page header so the wording stays consistent.
 */
export const INBOX_TYPE_META: Record<
  InboxType,
  { label: string; blurb: string; order: number }
> = {
  clarification: {
    label: "Clarifications",
    blurb: "Questions agents raised that need a human answer.",
    order: 1,
  },
  improvement: {
    label: "Improvements",
    blurb: "Proposed changes awaiting yes / no / later.",
    order: 2,
  },
  failure: {
    label: "Failures",
    blurb: "Run failures the system can't auto-recover from.",
    order: 3,
  },
  approval: {
    label: "Approvals",
    blurb: "Gated steps requiring an explicit human go-ahead.",
    order: 4,
  },
  exception: {
    label: "Exceptions",
    blurb: "Policy or routing edge cases that bypassed automation.",
    order: 5,
  },
  stuck: {
    label: "Stuck work",
    blurb: "Tracker or PR idle with no status movement for 24h+.",
    order: 6,
  },
  blocker: {
    label: "Blockers",
    blurb: "Self-heal could not recover — needs human follow-up.",
    order: 7,
  },
  report: {
    label: "Reports",
    blurb: "Daily / retro / process-review digests — read-only.",
    order: 8,
  },
};

/**
 * Footer kind drives the mailbox preview-pane footer:
 *
 *   - ``acknowledge`` — single "Acknowledge" button. Used for
 *     ``report`` (read-only digests) — operator marks read and moves
 *     on, no branching action.
 *   - ``reply`` — text-area + "Send" submit, plus a Dismiss escape.
 *     Used for ``clarification`` where the agent needs an answer.
 *   - ``decision`` — primary + secondary disposition buttons. Used for
 *     everything that's a yes/no/dismiss decision.
 *   - ``checklist`` — one row per ``payload.action_items`` entry with
 *     prompt + primary/secondary choices (daily digest letters).
 */
export type InboxFooterKind =
  | "acknowledge"
  | "reply"
  | "decision"
  | "report_actions"
  | "checklist";

export type InboxReportActionItem = {
  id: string;
  hint: string;
  label: string;
  secondary_label: string;
};

export function reportActionItems(
  payload: Record<string, unknown> | undefined,
): InboxReportActionItem[] {
  const raw = payload?.action_items;
  if (!Array.isArray(raw) || raw.length === 0) return [];
  const out: InboxReportActionItem[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const row = entry as Record<string, unknown>;
    if (row.kind !== "binary") continue;
    const id = typeof row.id === "string" ? row.id : "";
    const hint = typeof row.hint === "string" ? row.hint : "";
    const label = typeof row.label === "string" ? row.label : "";
    const secondary_label =
      typeof row.secondary_label === "string" ? row.secondary_label : "";
    if (!id || !hint || !label || !secondary_label) continue;
    out.push({ id, hint, label, secondary_label });
  }
  return out;
}

export function reportActionItemDecisions(
  payload: Record<string, unknown> | undefined,
): Record<string, "primary" | "secondary"> {
  const raw = payload?.action_item_decisions;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, "primary" | "secondary"> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (value === "primary" || value === "secondary") out[key] = value;
  }
  return out;
}

/** Daily-digest / multi-prompt letters — checklist footer contract. */
export type InboxChecklistActionItem = {
  id: string;
  prompt: string;
  primary: { label: string; choice: string };
  secondary: { label: string; choice: string };
};

function readChoiceButton(
  raw: unknown,
): { label: string; choice: string } | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const label = typeof o.label === "string" ? o.label.trim() : "";
  const choice = typeof o.choice === "string" ? o.choice.trim() : "";
  if (!label || !choice) return null;
  return { label, choice };
}

/** Parse checklist-shaped ``action_items``; skips invalid rows. */
export function parseChecklistActionItems(
  payload: Record<string, unknown> | undefined,
): InboxChecklistActionItem[] {
  if (!payload || typeof payload !== "object") return [];
  const raw = payload["action_items"];
  if (!Array.isArray(raw)) return [];
  const out: InboxChecklistActionItem[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const row = entry as Record<string, unknown>;
    const id = typeof row.id === "string" ? row.id.trim() : "";
    const prompt = typeof row.prompt === "string" ? row.prompt.trim() : "";
    const primary = readChoiceButton(row.primary);
    const secondary = readChoiceButton(row.secondary);
    if (!id || !prompt || !primary || !secondary) continue;
    out.push({ id, prompt, primary, secondary });
  }
  return out;
}

type FooterKindInput =
  | InboxType
  | Pick<InboxItemDetail, "type" | "payload" | "status">;

export function inboxFooterKind(input: FooterKindInput): InboxFooterKind {
  if (typeof input === "object") {
    if (input.status !== "resolved" && input.status !== "dismissed") {
      if (parseChecklistActionItems(input.payload).length > 0) {
        return "checklist";
      }
      if (
        input.type === "report" &&
        reportActionItems(input.payload).length > 0
      ) {
        return "report_actions";
      }
    }
    return inboxFooterKind(input.type);
  }
  if (input === "report") return "acknowledge";
  if (input === "clarification") return "reply";
  return "decision";
}

/** Primary list-row text: headline, else truncated summary, else title. */
export function inboxListLine(item: {
  headline?: string | null;
  summary?: string | null;
  title: string;
}): string {
  const headline = item.headline?.trim();
  if (headline) return headline;
  const summary = item.summary?.trim();
  if (summary) {
    const first = summary.split(/\r?\n/).find((line) => line.trim());
    const line = (first ?? summary).trim();
    return line.length <= 80 ? line : line.slice(0, 80);
  }
  return item.title;
}

/** Type kicker: glyph + label + dot tone for mailbox list rows. */
export const ROW_KICKER: Record<
  InboxType,
  { glyph: string; label: string; tone: string }
> = {
  clarification: { glyph: "?", label: "CLARIFY", tone: "bg-sun" },
  approval: { glyph: "★", label: "ATTENTION", tone: "bg-aqua" },
  improvement: { glyph: "★", label: "ATTENTION", tone: "bg-lilac" },
  failure: { glyph: "!", label: "FAILURE", tone: "bg-coral" },
  blocker: { glyph: "!", label: "BLOCKER", tone: "bg-coral" },
  exception: { glyph: "★", label: "ATTENTION", tone: "bg-coral/60" },
  stuck: { glyph: "★", label: "ATTENTION", tone: "bg-white/30" },
  report: { glyph: "≡", label: "REPORT", tone: "bg-white/15" },
};
/**
 * One row as it appears in the inbox list. Compact: the detail view
 * fetches the full payload + audit trail separately.
 */
export type InboxItem = {
  id: string;
  workspace_id: string;
  repo_id: string | null;
  type: InboxType;
  status: InboxStatus;
  title: string;
  headline: string;
  summary: string | null;
  /** Symbolic handle that resolved (e.g. "secops"). */
  intake_handle: string | null;
  /** Human-readable explanation of routing (e.g. "fallback:workspace_admin"). */
  intake_reason: string | null;
  /** Resolved owner. `null` = unassigned (admin attention required). */
  owner: { user_id: string; email: string; display_name: string | null } | null;
  play_key: string | null;
  run_id: string | null;
  created_at: string;
  due_at: string | null;
  snoozed_until: string | null;
  resolved_at: string | null;
  resolution: InboxResolution | null;
  category: InboxCategory;
  priority: number;
  lane: InboxLane;
  resolution_mode: InboxResolutionMode;
  /** Count of ``payload.action_items`` for list-row decision chip. */
  action_item_count?: number;
  /** From payload — powers ``formatInboxHeadline`` on list rows. */
  ticket_ref?: string | null;
  fsm_stage?: string | null;
};

export type InboxItemDetail = InboxItem & {
  payload: Record<string, unknown>;
  events: InboxItemEvent[];
  source_table: string | null;
  source_id: string | null;
};

export type InboxListResponse = {
  items: InboxItem[];
  total: number;
  counts_by_type: Record<string, number>;
  counts_by_status: Record<string, number>;
  next_cursor: string | null;
};

export type InboxCountsResponse = {
  mine: number;
  unassigned: number;
  all_open: number;
  actionable_new: number;
  reports_new: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
};

/** Categories shown in the `/inbox` list.
 *
 * Pre-2026-05-18 this was decision_needed + failure only — reports
 * and stuck-rows lived on the separate `/reports` page. Operator
 * feedback: split surface drained the main inbox of context and the
 * report digests are the same kind of "scan-once, click-acknowledge"
 * letters every other type is. `attention` is back; only
 * `dismiss_silently` (env-warning info notices, etc.) stays gated to
 * the noise page.
 */
export const INBOX_ACTIONABLE_CATEGORIES: InboxCategory[] = [
  "decision_needed",
  "failure",
  "attention",
];

/** Inbox Decision UI (ELS-158) — structured action_items contract. */
export type InboxActionItemKind = "choice" | "checkbox" | "ack" | "binary";

export type InboxActionItem = {
  id: string;
  kind: InboxActionItemKind;
  label: string;
  hint?: string | null;
  secondary_label?: string | null;
  default?: boolean;
  target_project_id?: string | null;
};

export type InboxResolutionMode =
  | "single_choice"
  | "multi_select"
  | "ack_only"
  | "per_item_binary"
  | "freeform_only";

export type InboxItemEvent = {
  id: string;
  actor_kind: "user" | "system" | "agent";
  actor_user_id: string | null;
  action:
    | "created"
    | "assigned"
    | "reassigned"
    | "snoozed"
    | "unsnoozed"
    | "resolved"
    | "dismissed"
    | "commented"
    | "action_item_decided";
  payload: Record<string, unknown>;
  created_at: string;
};

/**
 * Filter state the list page tracks in the URL. Kept here so the
 * page, the filter component, and the API client share one shape.
 *
 * `ownership='mine'` → owner_user_id == current user
 * `ownership='unassigned'` → owner_user_id IS NULL
 * `ownership='all'` → no filter (admin firehose)
 */
export type InboxFilterState = {
  ownership: "mine" | "unassigned" | "all";
  /** Subset to show; empty = all types. */
  types: InboxType[];
};

export const DEFAULT_INBOX_FILTERS: InboxFilterState = {
  ownership: "all",
  types: [],
};

/** Shown in the list without explicit status filters in the URL. */
export const INBOX_LIST_DEFAULT_STATUSES: InboxStatus[] = ["new", "snoozed"];

/**
 * Primary type chips on the inbox list (stuck / blockers are list-only, not
 * in this row).
 */
export const INBOX_FILTER_TYPES = [
  "clarification",
  "improvement",
  "failure",
  "approval",
  "exception",
] as const satisfies readonly InboxType[];

/**
 * Type guard for a string that should be an InboxType. Use when
 * parsing query-string filters server-side.
 */
export function isInboxType(value: string): value is InboxType {
  return (INBOX_TYPES as readonly string[]).includes(value);
}

export function isInboxStatus(value: string): value is InboxStatus {
  return (INBOX_STATUSES as readonly string[]).includes(value);
}

/**
 * Triage tier — the visual ladder operators scan top-down on the list
 * page. Assignment is fixed by item kind; orderings inside a tier come
 * from the existing ``created_at`` sort.
 *
 *   Tier 1 (needs you)         clarification, approval
 *   Tier 2 (autonomy escapes)  failure, blocker, exception
 *   Tier 3 (later)             improvement, stuck
 */
export type InboxTier = 1 | 2 | 3;

export function inboxTier(type: InboxType): InboxTier {
  switch (type) {
    case "clarification":
    case "approval":
      return 1;
    case "failure":
    case "blocker":
    case "exception":
      return 2;
    case "improvement":
    case "stuck":
    case "report":
      return 3;
  }
}

export const INBOX_TIER_LABEL: Record<InboxTier, string> = {
  1: "Needs you",
  2: "Autonomy escapes",
  3: "Later",
};
