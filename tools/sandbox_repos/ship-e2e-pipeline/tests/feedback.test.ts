import { describe, expect, it } from "vitest";
import { FeedbackStore } from "../src/db/feedback_store.js";

describe("FeedbackStore", () => {
  it("assigns sequential ids and lists in insertion order", () => {
    const store = new FeedbackStore();
    const a = store.add({ author: "alice", message: "first" });
    const b = store.add({ author: "bob", message: "second" });
    expect(a.id).toBe("fb_1");
    expect(b.id).toBe("fb_2");
    expect(store.list().map((r) => r.id)).toEqual(["fb_1", "fb_2"]);
  });

  it("returns undefined for unknown ids", () => {
    const store = new FeedbackStore();
    expect(store.get("nope")).toBeUndefined();
  });

  it("removes existing rows and reports false for unknown", () => {
    const store = new FeedbackStore();
    const row = store.add({ author: "alice", message: "ping" });
    expect(store.remove(row.id)).toBe(true);
    expect(store.remove(row.id)).toBe(false);
    expect(store.list()).toEqual([]);
  });
});
