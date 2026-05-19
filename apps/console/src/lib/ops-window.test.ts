import { describe, expect, it } from "vitest";

import {
  opsReportWindowPhrase,
  opsReportWindowShortLabel,
  parseOpsReportWindow,
} from "@/lib/ops-window";

describe("parseOpsReportWindow", () => {
  it("defaults to 24h when param is missing", () => {
    expect(parseOpsReportWindow(undefined)).toBe("24h");
  });

  it("accepts each allowed window token", () => {
    expect(parseOpsReportWindow("7d")).toBe("7d");
    expect(parseOpsReportWindow("30d")).toBe("30d");
    expect(parseOpsReportWindow("all")).toBe("all");
  });

  it("uses the first element when searchParams is an array", () => {
    expect(parseOpsReportWindow(["7d", "30d"])).toBe("7d");
  });

  it("falls back to 24h for unknown tokens (UI; API returns 422)", () => {
    expect(parseOpsReportWindow("fortnight")).toBe("24h");
    expect(parseOpsReportWindow("")).toBe("24h");
  });
});

describe("opsReportWindowShortLabel", () => {
  it("maps windows to compact kickers", () => {
    expect(opsReportWindowShortLabel("24h")).toBe("24H");
    expect(opsReportWindowShortLabel("7d")).toBe("7D");
    expect(opsReportWindowShortLabel("30d")).toBe("30D");
    expect(opsReportWindowShortLabel("all")).toBe("ALL");
  });
});

describe("opsReportWindowPhrase", () => {
  it("describes the active UTC horizon in prose", () => {
    expect(opsReportWindowPhrase("7d")).toContain("7d");
    expect(opsReportWindowPhrase("all")).toContain("all history");
  });
});
