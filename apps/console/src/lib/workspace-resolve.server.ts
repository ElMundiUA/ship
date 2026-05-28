import "server-only";

import { cookies, headers } from "next/headers";

import {
  isLikelyWorkspaceId,
  parseWorkspaceIdParam,
  readWorkspaceIdFromCookieValue,
  resolveActiveWorkspaceId,
  SHIP_ACTIVE_WORKSPACE_COOKIE,
  SHIP_ACTIVE_WS_REQUEST_HEADER,
} from "@/lib/workspace-scope";

type Search = Record<string, string | string[] | undefined>;

/**
 * Search params for authed layout SSR. Layouts cannot receive
 * ``searchParams`` from Next.js; middleware forwards ``?ws=`` on a
 * request header so the shell resolves the same id as sibling pages.
 */
export async function getLayoutWorkspaceSearchParams(): Promise<Search> {
  const h = await headers();
  const ws = h.get(SHIP_ACTIVE_WS_REQUEST_HEADER)?.trim();
  if (ws && isLikelyWorkspaceId(ws)) {
    return { ws };
  }
  return {};
}

/**
 * Effective workspace for SSR: ``?ws=`` overrides; otherwise
 * {@link SHIP_ACTIVE_WORKSPACE_COOKIE} if it still matches
 * `workspaces` (set by middleware on any `?ws=` hit).
 */
export async function getResolvedWorkspaceId(
  searchParams: Search,
  workspaces: { id: string }[],
): Promise<string | undefined> {
  const urlWs = parseWorkspaceIdParam(searchParams.ws);
  const c = await cookies();
  const stored = readWorkspaceIdFromCookieValue(
    c.get(SHIP_ACTIVE_WORKSPACE_COOKIE)?.value,
  );
  return resolveActiveWorkspaceId(urlWs, stored, workspaces);
}
