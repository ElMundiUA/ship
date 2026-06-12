import "server-only";

import { headers } from "next/headers";

import { apiFetch } from "@/lib/api/client";
import {
  type ConsoleMode,
  SHIP_PATHNAME_REQUEST_HEADER,
  envDefaultMode,
  resolveMode,
} from "@/lib/console-mode";

/**
 * Server half of console-mode resolution (ELS-235).
 *
 * Reads the per-workspace `console.surface` config scope; failures fail
 * OPEN to the env default so a backend hiccup never locks the operator
 * out. The pathname arrives on a middleware-forwarded request header
 * (same pattern as the active-workspace header).
 */
export async function resolveConsoleMode(
  workspaceId: string,
  token: string | null,
): Promise<ConsoleMode> {
  const envMode = envDefaultMode();
  try {
    const detail = await apiFetch<{ current_value?: string | null }>(
      `/v1/workspaces/${workspaceId}/config/console.surface`,
      { token },
    );
    return resolveMode(detail?.current_value, envMode);
  } catch {
    return envMode;
  }
}

export async function getRequestPathname(): Promise<string> {
  const h = await headers();
  return h.get(SHIP_PATHNAME_REQUEST_HEADER)?.trim() || "/";
}
