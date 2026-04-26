import "server-only";

import { cookies } from "next/headers";

import {
  parseWorkspaceIdParam,
  readWorkspaceIdFromCookieValue,
  resolveActiveWorkspaceId,
  SHIP_ACTIVE_WORKSPACE_COOKIE,
} from "@/lib/workspace-scope";

type Search = Record<string, string | string[] | undefined>;

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
