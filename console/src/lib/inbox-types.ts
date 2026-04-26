/**
 * Frontend type contract for the Inbox v1 surface (RFC-0010).
 *
 * Mirrors the Pydantic shapes returned by the Phase-2 backend:
 *   - GET /v1/workspaces/{ws}/inbox             → InboxListResponse
 *   - GET /v1/workspaces/{ws}/inbox/{id}        → InboxItemDetail
 *   - GET /v1/workspaces/{ws}/inbox/groups      → InboxGroup[]
 *   - GET /v1/workspaces/{ws}/inbox/routing     → InboxRoutingRule[]
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
] as const;
export type InboxType = (typeof INBOX_TYPES)[number];

export const INBOX_STATUSES = [
  "new",
  "snoozed",
  "resolved",
  "dismissed",
] as const;
export type InboxStatus = (typeof INBOX_STATUSES)[number];

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
  by_type: Record<string, number>;
  by_status: Record<string, number>;
};

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
    | "commented";
  payload: Record<string, unknown>;
  created_at: string;
};

/**
 * Operational group — distinct from `WorkspaceMember.role`. Routing
 * rules dereference symbolic handles (e.g. `secops`) into one of
 * these groups; group strategy then picks the concrete owner.
 */
export type InboxGroup = {
  id: string;
  workspace_id: string;
  key: string;
  name: string;
  description: string | null;
  assignment_strategy: "round_robin" | "oncall" | "first";
  member_count: number;
  created_at: string;
};

export type InboxRoutingTargetType = "user" | "group" | "strategy";
export type InboxAssignmentStrategy = "round_robin" | "oncall" | "first";

export type InboxRoutingRule = {
  id: string;
  workspace_id: string;
  handle: string;
  target_type: InboxRoutingTargetType;
  target_user_id: string | null;
  target_group_id: string | null;
  target_strategy: string | null;
  assignment_strategy: InboxAssignmentStrategy | null;
  strategy_config: Record<string, unknown>;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type InboxRoutingRuleDetail = InboxRoutingRule & {
  target_user_email: string | null;
  target_group_key: string | null;
  target_group_name: string | null;
};

export type InboxRoutingHandlesOut = {
  bound_handles: string[];
  used_handles: string[];
  orphaned_handles: string[];
  unbound_handles: string[];
};

export type InboxRoutingPreviewOut = {
  handle: string;
  resolved_user_id: string | null;
  resolved_user_email: string | null;
  intake_handle: string;
  intake_reason: string;
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
