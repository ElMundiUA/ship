/**
 * URL helpers for the unified Inbox surface (RFC-0010 P2-12).
 *
 * The list page is URL-driven so admins can deep-link / share /
 * refresh without losing context. Both the server component
 * (`/app/inbox/page.tsx`) and the client filter wrapper
 * (`./inbox-filters-controlled.tsx`) need the same encoding so we
 * keep it in one pure helper module — no React, no `'use client'`.
 *
 * Encoding rules:
 *   - Default values are omitted (so `/inbox` == "all items, all types
 *     in the default status slice"), and the clear-filters link
 *     target is just `/inbox`.
 *   - `types` are emitted as repeated query params
 *     (e.g. `?type=clarification&type=approval`) — the API client
 *     in `lib/api/client.ts` uses the same convention via
 *     `URLSearchParams.append`.
 *   - `repo` / `play` survive across pagination + filter changes
 *     so the page stays scoped while the operator drills.
 *   - `cursor` is opaque-forwarding only; resetting filters drops
 *     it (the cursor only makes sense for one filter snapshot).
 */

import {
  DEFAULT_INBOX_FILTERS,
  isInboxType,
  type InboxFilterState,
  type InboxType,
} from "@/lib/inbox-types";

export type BuildInboxUrlExtras = {
  cursor?: string | null;
  repo?: string | null;
  play?: string | null;
  /** Multi-workspace accounts: keep ``?ws=`` on every inbox URL. */
  workspaceScope?: string | null;
  /** Mailbox preview selection — preserved across filter / tab changes. */
  selected?: string | null;
};

export type ParsedInboxSearchParams = {
  filters: InboxFilterState;
  selectedId: string | null;
  errorCode: string | null;
};

function parseInboxTypes(
  raw: string | string[] | undefined,
): InboxType[] {
  const types: InboxType[] = [];
  const add = (value: string) => {
    if (isInboxType(value) && !types.includes(value)) types.push(value);
  };
  if (typeof raw === "string") add(raw);
  else if (Array.isArray(raw)) {
    for (const entry of raw) {
      if (typeof entry === "string") add(entry);
    }
  }
  return types;
}

/** Parse inbox list URL query params (server + client share this). */
export function parseInboxSearchParams(
  raw: Record<string, string | string[] | undefined>,
): ParsedInboxSearchParams {
  const ownershipRaw = typeof raw.ownership === "string" ? raw.ownership : null;
  const ownership: InboxFilterState["ownership"] =
    ownershipRaw === "mine" ||
    ownershipRaw === "unassigned" ||
    ownershipRaw === "all"
      ? ownershipRaw
      : DEFAULT_INBOX_FILTERS.ownership;

  const selectedRaw = typeof raw.selected === "string" ? raw.selected : null;
  const selectedId =
    selectedRaw && selectedRaw.length > 0 ? selectedRaw : null;
  const errorCode = typeof raw.error === "string" ? raw.error : null;

  return {
    filters: {
      ownership,
      types: parseInboxTypes(raw.type),
    },
    selectedId,
    errorCode,
  };
}

export function buildInboxUrl(
  filters: InboxFilterState,
  extras: BuildInboxUrlExtras = {},
): string {
  const params = new URLSearchParams();
  if (filters.ownership !== DEFAULT_INBOX_FILTERS.ownership) {
    params.set("ownership", filters.ownership);
  }
  for (const t of filters.types) params.append("type", t);
  if (extras.repo) params.set("repo", extras.repo);
  if (extras.play) params.set("play", extras.play);
  if (extras.cursor) params.set("cursor", extras.cursor);
  if (extras.workspaceScope) params.set("ws", extras.workspaceScope);
  if (extras.selected) params.set("selected", extras.selected);
  const qs = params.toString();
  return qs ? `/inbox?${qs}` : "/inbox";
}

/**
 * Count of non-default filter axes. Each axis (ownership, types, repo, play)
 * contributes at most 1.
 */
export function countActiveFilters(
  filters: InboxFilterState,
  extras: { repo?: string | null; play?: string | null } = {},
): number {
  let n = 0;
  if (filters.ownership !== DEFAULT_INBOX_FILTERS.ownership) n += 1;
  if (filters.types.length > 0) n += 1;
  if (extras.repo) n += 1;
  if (extras.play) n += 1;
  return n;
}
