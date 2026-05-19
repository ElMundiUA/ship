import { describe, expect, it } from "vitest";

import { filterInboxByLane } from "@/components/inbox/inbox-lane-filters";
import type { InboxItem } from "@/lib/inbox-types";

function stubItem(overrides: Partial<InboxItem>): InboxItem {
  return {
    id: "id-1",
    workspace_id: "ws-1",
    type: "clarification",
    category: "decision_needed",
    priority: 8,
    status: "new",
    title: "t",
    summary: "s",
    payload: {},
    lane: "today",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  } as InboxItem;
}

describe("filterInboxByLane", () => {
  const items = [
    stubItem({ id: "a", lane: "now" }),
    stubItem({ id: "b", lane: "today" }),
    stubItem({ id: "c", lane: "whenever" }),
  ];

  it("returns all rows for lane=all", () => {
    expect(filterInboxByLane(items, "all")).toHaveLength(3);
  });

  it("filters to a single lane without mutating source", () => {
    const filtered = filterInboxByLane(items, "now");
    expect(filtered.map((i) => i.id)).toEqual(["a"]);
    expect(items).toHaveLength(3);
  });
});
