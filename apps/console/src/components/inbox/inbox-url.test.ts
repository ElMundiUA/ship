import { describe, expect, it } from "vitest";

import { buildInboxUrl, parseInboxSearchParams } from "./inbox-url";

describe("parseInboxSearchParams", () => {
  it("parses clarification deep link filters", () => {
    expect(parseInboxSearchParams({ type: "clarification" })).toEqual({
      filters: { ownership: "all", types: ["clarification"] },
      selectedId: null,
      errorCode: null,
    });
  });

  it("ignores unknown type values", () => {
    expect(parseInboxSearchParams({ type: "not-a-type" }).filters.types).toEqual(
      [],
    );
  });

  it("preserves selected row and error banner codes", () => {
    expect(
      parseInboxSearchParams({
        selected: "abc-123",
        error: "forbidden",
        ownership: "mine",
      }),
    ).toEqual({
      filters: { ownership: "mine", types: [] },
      selectedId: "abc-123",
      errorCode: "forbidden",
    });
  });
});

describe("buildInboxUrl", () => {
  it("round-trips type + selected for mailbox navigation", () => {
    const url = buildInboxUrl(
      { ownership: "all", types: ["clarification"] },
      { selected: "item-1", workspaceScope: "ws-9" },
    );
    expect(url).toBe("/inbox?type=clarification&ws=ws-9&selected=item-1");
    expect(parseInboxSearchParams(Object.fromEntries(new URL(url, "http://x").searchParams.entries()))).toEqual({
      filters: { ownership: "all", types: ["clarification"] },
      selectedId: "item-1",
      errorCode: null,
    });
  });
});
