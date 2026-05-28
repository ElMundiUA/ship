import { describe, expect, it } from "vitest";

import {
  parseWorkspaceIdParam,
  readWorkspaceIdFromCookieValue,
  resolveActiveWorkspaceId,
} from "@/lib/workspace-scope";

const WS_A = "11111111-1111-4111-8111-111111111111";
const WS_B = "22222222-2222-4222-8222-222222222222";
const WS_UNKNOWN = "33333333-3333-4333-8333-333333333333";
const workspaces = [{ id: WS_A }, { id: WS_B }];

describe("parseWorkspaceIdParam", () => {
  it("returns the first entry when ws is an array", () => {
    expect(parseWorkspaceIdParam([WS_A, WS_B])).toBe(WS_A);
  });

  it("returns undefined for empty strings", () => {
    expect(parseWorkspaceIdParam("")).toBeUndefined();
    expect(parseWorkspaceIdParam([])).toBeUndefined();
  });
});

describe("readWorkspaceIdFromCookieValue", () => {
  it("accepts valid uuids and rejects junk", () => {
    expect(readWorkspaceIdFromCookieValue(WS_A)).toBe(WS_A);
    expect(readWorkspaceIdFromCookieValue("not-a-uuid")).toBeUndefined();
    expect(readWorkspaceIdFromCookieValue(undefined)).toBeUndefined();
  });
});

describe("resolveActiveWorkspaceId", () => {
  it("prefers a valid url ws over the cookie", () => {
    expect(resolveActiveWorkspaceId(WS_B, WS_A, workspaces)).toBe(WS_B);
  });

  it("falls back to cookie when url ws is absent", () => {
    expect(resolveActiveWorkspaceId(undefined, WS_A, workspaces)).toBe(WS_A);
  });

  it("ignores ids that are not in the membership list", () => {
    expect(resolveActiveWorkspaceId(WS_UNKNOWN, WS_A, workspaces)).toBe(WS_A);
    expect(resolveActiveWorkspaceId(undefined, WS_UNKNOWN, workspaces)).toBe(
      undefined,
    );
  });

  it("returns undefined when nothing matches", () => {
    expect(resolveActiveWorkspaceId(undefined, undefined, workspaces)).toBe(
      undefined,
    );
  });
});
