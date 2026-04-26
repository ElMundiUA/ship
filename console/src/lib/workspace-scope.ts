/**
 * Picks the active workspace for server-rendered console pages. The URL may
 * carry ``?ws=<uuid>`` so multi-workspace users stay on the intended org after
 * switching from the shell.
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
