import "server-only";

import { cache } from "react";

import { getMe, listWorkspaces } from "@/lib/api/client";
import type { ApiUser, ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";

/**
 * React.cache()-wrapped session helpers. Within a single server render
 * (one navigation, one RSC payload), repeat calls return the same
 * promise instead of hitting the API again.
 *
 * Use these from layouts AND pages — when a shared layout fetches
 * workspaces and the page needs them too, only one network round-trip
 * happens. Note: cache lifetime is per-request only; cross-navigation
 * deduplication needs Next data cache (revalidate) or layout-level
 * partial rendering.
 */

export const getCachedSessionToken = cache(
  async (): Promise<string | null> => getSessionToken(),
);

export const getCachedWorkspaces = cache(
  async (): Promise<ApiWorkspace[]> => {
    const token = await getCachedSessionToken();
    if (!token) return [];
    return listWorkspaces(token);
  },
);

export const getCachedMe = cache(async (): Promise<ApiUser | null> => {
  const token = await getCachedSessionToken();
  if (!token) return null;
  try {
    return await getMe(token);
  } catch {
    return null;
  }
});
