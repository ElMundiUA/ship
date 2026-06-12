/**
 * Console-mode resolution (ELS-235 — strangler step 0).
 *
 * The whole authed surface can go dark per-workspace without deleting
 * code: `full` (today, default), `residual` (status + Inbox + Settings
 * only), `off` (status + Inbox only — the Inbox approval surface stays
 * reachable in EVERY mode so pending operator approvals are never
 * orphaned; thesis 4).
 *
 * Precedence: per-workspace `console.surface` config scope beats the
 * `SHIP_CONSOLE_MODE` env default beats `full`. Resolution failures
 * fail OPEN to the env default — a backend hiccup must not lock the
 * operator out of their console.
 *
 * Pure helpers live here (vitest-covered); the server-side fetch wrapper
 * is in console-mode.server.ts.
 */

export type ConsoleMode = "full" | "residual" | "off";

export const CONSOLE_MODES: readonly ConsoleMode[] = ["full", "residual", "off"];

export const SHIP_PATHNAME_REQUEST_HEADER = "x-ship-pathname" as const;

export function parseConsoleMode(raw: string | null | undefined): ConsoleMode | null {
  if (raw && (CONSOLE_MODES as readonly string[]).includes(raw)) {
    return raw as ConsoleMode;
  }
  return null;
}

export function envDefaultMode(): ConsoleMode {
  return parseConsoleMode(process.env.SHIP_CONSOLE_MODE) ?? "full";
}

/** Path prefixes that stay reachable per mode. `/` is the residual
 * status landing in every mode; the Inbox approval surface is
 * reachable in EVERY mode (must-fix: never orphan pending operator
 * approvals). */
const RESIDUAL_PREFIXES = ["/", "/inbox", "/settings"] as const;
const OFF_PREFIXES = ["/", "/inbox"] as const;

function matchesPrefix(pathname: string, prefix: string): boolean {
  if (prefix === "/") return pathname === "/";
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

export function isPathAllowed(mode: ConsoleMode, pathname: string): boolean {
  if (mode === "full") return true;
  const prefixes = mode === "residual" ? RESIDUAL_PREFIXES : OFF_PREFIXES;
  return prefixes.some((p) => matchesPrefix(pathname, p));
}

/** Per-workspace override beats env default; null override falls back. */
export function resolveMode(
  workspaceOverride: string | null | undefined,
  envMode: ConsoleMode,
): ConsoleMode {
  return parseConsoleMode(workspaceOverride) ?? envMode;
}
