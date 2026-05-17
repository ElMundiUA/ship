/** Rolling UTC horizons for `/dashboard/ops` and repo-home Now tab. Mirrors backend enum. */

export const DASHBOARD_OPS_WINDOWS = ["24h", "7d", "30d", "all"] as const;

export type DashboardOpsWindow = (typeof DASHBOARD_OPS_WINDOWS)[number];

export function parseDashboardOpsWindow(
  raw: string | string[] | undefined,
): DashboardOpsWindow {
  const v = Array.isArray(raw) ? raw[0] : raw;
  return DASHBOARD_OPS_WINDOWS.includes(v as DashboardOpsWindow)
    ? (v as DashboardOpsWindow)
    : "24h";
}

/** Short uppercase label for headings and badges (UTC rollup). */
export function formatOpsWindowBadge(w: DashboardOpsWindow): string {
  switch (w) {
    case "24h":
      return "24H";
    case "7d":
      return "7D";
    case "30d":
      return "30D";
    case "all":
      return "ALL";
  }
}
