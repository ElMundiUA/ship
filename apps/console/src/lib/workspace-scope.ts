/**
 * Picks the active workspace for server-rendered console pages. The effective
 * id comes from the URL (``?ws=``) when present, else the ``ship.ws`` httpOnly
 * cookie (set by middleware when any navigation includes ``?ws=``), else the
 * first list entry. Use {@link getResolvedWorkspaceId} in server code.
 */

import type { ApiWorkspace } from "@/lib/api/types";

export function parseWorkspaceIdParam(
  raw: string | string[] | undefined,
): string | undefined {
  if (Array.isArray(raw)) {
    return typeof raw[0] === "string" && raw[0].length > 0 ? raw[0] : undefined;
  }
  return typeof raw === "string" && raw.length > 0 ? raw : undefined;
}

/** Cookie set by middleware when a request carries ``?ws=<uuid>`` (multi-tenant). */
export const SHIP_ACTIVE_WORKSPACE_COOKIE = "ship.ws" as const;

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function isLikelyWorkspaceId(id: string): boolean {
  return UUID_RE.test(id);
}

/**
 * If the raw cookie value looks like a workspace UUID, return it. Used
 * with ``getResolvedWorkspaceId`` (server) and middleware.
 */
export function readWorkspaceIdFromCookieValue(
  value: string | undefined,
): string | undefined {
  if (value && isLikelyWorkspaceId(value)) return value;
  return undefined;
}

/**
 * Resolves the active workspace id: ``?ws=`` wins; otherwise a persisted
 * cookie id if it is still in the user's workspace list.
 */
export function resolveActiveWorkspaceId(
  urlWs: string | undefined,
  storedWs: string | undefined,
  workspaces: { id: string }[],
): string | undefined {
  const ids = new Set(workspaces.map((w) => w.id));
  if (urlWs && ids.has(urlWs)) return urlWs;
  if (storedWs && ids.has(storedWs)) return storedWs;
  return undefined;
}

export function pickWorkspace(
  workspaces: ApiWorkspace[],
  wsId: string | undefined,
): ApiWorkspace {
  if (workspaces.length === 0) {
    throw new Error("pickWorkspace: empty workspace list");
  }
  if (!wsId) {
    return workspaces[0];
  }
  const found = workspaces.find((w) => w.id === wsId);
  return found ?? workspaces[0];
}

export function toAppShellWorkspaces(
  list: ApiWorkspace[],
): { id: string; name: string; slug: string }[] {
  return list.map((w) => ({ id: w.id, name: w.name, slug: w.slug }));
}

/** For server `Link` hrefs: preserve workspace when the account has &gt;1. */
export function withWorkspaceQuery(
  path: string,
  workspaceId: string,
  multiWorkspace: boolean,
): string {
  if (!multiWorkspace) {
    return path;
  }
  const joiner = path.includes("?") ? "&" : "?";
  return `${path}${joiner}ws=${encodeURIComponent(workspaceId)}`;
}

/** Heuristic for the JIT personal workspace name from the backend. */
export function looksLikeJitPersonalWorkspace(w: { name: string }): boolean {
  return /'s workspace$/i.test(w.name.trim());
}

/** Home after picking a row on the multi-workspace chooser. */
export function homeEntryHref(workspaceId: string, skipWizard: boolean): string {
  const p = new URLSearchParams();
  p.set("ws", workspaceId);
  if (skipWizard) p.set("skipWizard", "1");
  return `/?${p.toString()}`;
}
