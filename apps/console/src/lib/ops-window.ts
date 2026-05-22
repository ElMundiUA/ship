export const OPS_REPORT_WINDOWS = ["24h", "7d", "30d", "all"] as const;

export type OpsReportWindow = (typeof OPS_REPORT_WINDOWS)[number];

/** Normalise URL / search-param input; invalid tokens fall back to a
 *  caller-supplied default (or **24h**). The workspace home passes
 *  ``"7d"`` because agentic-SDLC throughput is bursty — a quiet 24h
 *  window routinely reads all-zero and makes the pipeline look dead. */
export function parseOpsReportWindow(
  raw: string | string[] | undefined,
  fallback: OpsReportWindow = "24h",
): OpsReportWindow {
  const v = Array.isArray(raw) ? raw[0] : raw;
  if (v === "24h" || v === "7d" || v === "30d" || v === "all") return v;
  return fallback;
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
