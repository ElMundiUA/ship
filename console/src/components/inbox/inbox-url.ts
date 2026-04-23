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
 *   - Default values are omitted (so `/inbox` == "open queue, mine
 *     only"), and the clear-filters link target is just `/inbox`.
 *   - `types` and `statuses` are emitted as repeated query params
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
  type InboxFilterState,
} from "@/lib/inbox-types";

export type BuildInboxUrlExtras = {
  cursor?: string | null;
  repo?: string | null;
  play?: string | null;
};

function statusesEqualDefault(statuses: InboxFilterState["statuses"]): boolean {
  const defaults = DEFAULT_INBOX_FILTERS.statuses;
  if (statuses.length !== defaults.length) return false;
  return statuses.every((s) => defaults.includes(s));
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
  if (!statusesEqualDefault(filters.statuses)) {
    for (const s of filters.statuses) params.append("status", s);
  }
  if (extras.repo) params.set("repo", extras.repo);
  if (extras.play) params.set("play", extras.play);
  if (extras.cursor) params.set("cursor", extras.cursor);
  const qs = params.toString();
  return qs ? `/inbox?${qs}` : "/inbox";
}

/**
 * Count of non-default filter axes — drives the "Filters active"
 * stat tile + the clear-filters link visibility. Each axis (ownership,
 * types, statuses, repo, play) contributes at most 1.
 */
export function countActiveFilters(
  filters: InboxFilterState,
  extras: { repo?: string | null; play?: string | null } = {},
): number {
  let n = 0;
  if (filters.ownership !== DEFAULT_INBOX_FILTERS.ownership) n += 1;
  if (filters.types.length > 0) n += 1;
  if (!statusesEqualDefault(filters.statuses)) n += 1;
  if (extras.repo) n += 1;
  if (extras.play) n += 1;
  return n;
}
