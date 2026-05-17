export const OPS_REPORT_WINDOWS = ["24h", "7d", "30d", "all"] as const;

export type OpsReportWindow = (typeof OPS_REPORT_WINDOWS)[number];

/** Normalise URL / search-param input; invalid tokens fall back to **24h**. */
export function parseOpsReportWindow(
  raw: string | string[] | undefined,
): OpsReportWindow {
  const v = Array.isArray(raw) ? raw[0] : raw;
  if (v === "24h" || v === "7d" || v === "30d" || v === "all") return v;
  return "24h";
}

/** Compact kicker, e.g. `(7D)` / `(ALL)`. */
export function opsReportWindowShortLabel(w: OpsReportWindow): string {
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

/** Phrase for prose labels (ops / Now period). */
export function opsReportWindowPhrase(w: OpsReportWindow): string {
  switch (w) {
    case "24h":
      return "last 24h (UTC)";
    case "7d":
      return "last 7d (UTC)";
    case "30d":
      return "last 30d (UTC)";
    case "all":
      return "all history (UTC, capped)";
  }
}
