/**
 * Deep-link helper for a single inbox item.
 *
 * The mailbox `/inbox` page was removed in the MCP-first rework
 * (ELS-289/294): inbox operation lives in the operator's agent over
 * MCP and in Telegram. What remains web-side is the per-item
 * `/approve/{id}` confirm page — ordinary approve/reject plus the
 * typed-slug flow for destructive items — and every surface that used
 * to link "into the Inbox" (chat tool renderers, decide/discuss route
 * redirects, Telegram buttons, MCP `web_url` refusals) now lands there.
 */

/** Deeplink to one item's confirm page. Pass the workspace id whenever
 * the operator may belong to several workspaces — the approve page
 * resolves ``?ws=`` exactly like every other console route. */
export function inboxItemUrl(itemId: string, workspaceId?: string | null): string {
  const base = `/approve/${encodeURIComponent(itemId)}`;
  return workspaceId ? `${base}?ws=${encodeURIComponent(workspaceId)}` : base;
}
