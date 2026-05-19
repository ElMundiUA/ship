import { describe, expect, it } from "vitest";

import {
  INBOX_TYPES,
  inboxActionItems,
  inboxFooterKind,
  ROW_KICKER,
} from "@/lib/inbox-types";

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
  it("returns checklist when open item has action_items", () => {
    expect(
      inboxFooterKind("report", {
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

  it("returns acknowledge for report without action_items", () => {
    expect(inboxFooterKind("report", { status: "new", payload: {} })).toBe(
      "acknowledge",
    );
  });

  it("falls back to type default when resolved even with action_items", () => {
    expect(
      inboxFooterKind("report", {
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

describe("inboxActionItems", () => {
  it("filters invalid rows", () => {
    expect(
      inboxActionItems({
        action_items: [
          {
            id: "ok",
            prompt: "Q?",
            primary: { label: "A", choice: "a" },
            secondary: { label: "B", choice: "b" },
          },
          { id: "bad" },
        ],
      }),
    ).toHaveLength(1);
  });
});
