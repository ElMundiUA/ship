import { describe, expect, it } from "vitest";

import { INBOX_TYPES, inboxFooterKind, ROW_KICKER } from "@/lib/inbox-types";

describe("ROW_KICKER", () => {
  it("maps clarification to ? CLARIFY", () => {
    expect(ROW_KICKER.clarification).toEqual({
      glyph: "?",
      label: "CLARIFY",
      tone: "bg-sun",
    });
  });

  it("maps blocker to ! BLOCKER", () => {
    expect(ROW_KICKER.blocker.glyph).toBe("!");
    expect(ROW_KICKER.blocker.label).toBe("BLOCKER");
  });

  it("maps report to ≡ REPORT", () => {
    expect(ROW_KICKER.report.glyph).toBe("≡");
    expect(ROW_KICKER.report.label).toBe("REPORT");
  });

  it("defines glyph + label for every inbox type", () => {
    const expectedGlyphs: Record<string, string> = {
      clarification: "?",
      blocker: "!",
      failure: "!",
      report: "≡",
      approval: "★",
      exception: "★",
      stuck: "★",
      improvement: "★",
    };
    for (const type of INBOX_TYPES) {
      const kicker = ROW_KICKER[type];
      expect(kicker.glyph).toBe(expectedGlyphs[type]);
      expect(kicker.label.length).toBeGreaterThan(0);
    }
  });
});

describe("inboxFooterKind", () => {
  it("returns acknowledge for report", () => {
    expect(inboxFooterKind("report")).toBe("acknowledge");
  });

  it("returns reply for clarification", () => {
    expect(inboxFooterKind("clarification")).toBe("reply");
  });

  it("returns decision for blocker", () => {
    expect(inboxFooterKind("blocker")).toBe("decision");
  });
});
