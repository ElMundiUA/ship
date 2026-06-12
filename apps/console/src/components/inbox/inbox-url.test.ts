import { describe, expect, it } from "vitest";

import { inboxItemUrl } from "./inbox-url";

describe("inboxItemUrl", () => {
  it("targets the /approve confirm page", () => {
    expect(inboxItemUrl("abc-123")).toBe("/approve/abc-123");
  });
  it("keeps ?ws= for multi-workspace operators", () => {
    expect(inboxItemUrl("abc-123", "ws-9")).toBe("/approve/abc-123?ws=ws-9");
  });
  it("URL-encodes hostile ids", () => {
    expect(inboxItemUrl("a/b?c")).toBe("/approve/a%2Fb%3Fc");
  });
});
