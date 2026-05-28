import { describe, expect, it } from "vitest";

import {
  INBOX_TYPES,
  inboxFooterKind,
  inboxRowKicker,
  parseChecklistActionItems,
  ROW_KICKER,
  ROW_KICKER_FALLBACK,
} from "@/lib/inbox-types";

describe("inboxRowKicker", () => {
  it("falls back for unknown API types instead of throwing", () => {
    expect(inboxRowKicker("clarification")).toEqual(ROW_KICKER.clarification);
    expect(inboxRowKicker("legacy-unknown")).toEqual(ROW_KICKER_FALLBACK);
  });
});

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

  it("returns checklist when payload has checklist action_items", () => {
    expect(
      inboxFooterKind({
        type: "report",
        status: "new",
        payload: {
          action_items: [
            {
              id: "q1",
              prompt: "Ship it?",
              primary: { label: "Yes", choice: "yes" },
              secondary: { label: "No", choice: "no" },
            },
          ],
        },
      }),
    ).toBe("checklist");
  });

  it("does not return checklist for resolved items", () => {
    expect(
      inboxFooterKind({
        type: "report",
        status: "resolved",
        payload: {
          action_items: [
            {
              id: "q1",
              prompt: "Ship it?",
              primary: { label: "Yes", choice: "yes" },
              secondary: { label: "No", choice: "no" },
            },
          ],
        },
      }),
    ).toBe("acknowledge");
  });
});

describe("parseChecklistActionItems", () => {
  it("parses valid rows and skips incomplete entries", () => {
    expect(
      parseChecklistActionItems({
        action_items: [
          {
            id: "a",
            prompt: "Q1",
            primary: { label: "Go", choice: "go" },
            secondary: { label: "Wait", choice: "wait" },
          },
          { id: "b", prompt: "missing buttons" },
        ],
      }),
    ).toEqual([
      {
        id: "a",
        prompt: "Q1",
        primary: { label: "Go", choice: "go" },
        secondary: { label: "Wait", choice: "wait" },
      },
    ]);
  });
});
