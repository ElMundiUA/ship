import { describe, expect, it } from "vitest";

import {
  envDefaultMode,
  isPathAllowed,
  parseConsoleMode,
  resolveMode,
} from "./console-mode";

describe("parseConsoleMode", () => {
  it("accepts the three modes and rejects garbage", () => {
    expect(parseConsoleMode("full")).toBe("full");
    expect(parseConsoleMode("residual")).toBe("residual");
    expect(parseConsoleMode("off")).toBe("off");
    expect(parseConsoleMode("hidden")).toBeNull();
    expect(parseConsoleMode(null)).toBeNull();
    expect(parseConsoleMode("")).toBeNull();
  });
});

describe("resolveMode precedence", () => {
  it("workspace override beats env default", () => {
    expect(resolveMode("residual", "full")).toBe("residual");
    expect(resolveMode("off", "full")).toBe("off");
  });
  it("falls back to env default on null/garbage override", () => {
    expect(resolveMode(null, "residual")).toBe("residual");
    expect(resolveMode("wat", "full")).toBe("full");
  });
});

describe("envDefaultMode", () => {
  it("defaults to full when unset/garbage", () => {
    const prev = process.env.SHIP_CONSOLE_MODE;
    delete process.env.SHIP_CONSOLE_MODE;
    expect(envDefaultMode()).toBe("full");
    process.env.SHIP_CONSOLE_MODE = "nonsense";
    expect(envDefaultMode()).toBe("full");
    process.env.SHIP_CONSOLE_MODE = "residual";
    expect(envDefaultMode()).toBe("residual");
    if (prev === undefined) delete process.env.SHIP_CONSOLE_MODE;
    else process.env.SHIP_CONSOLE_MODE = prev;
  });
});

describe("isPathAllowed", () => {
  it("full allows everything", () => {
    for (const p of ["/", "/inbox", "/analytics", "/process/x", "/settings/general"]) {
      expect(isPathAllowed("full", p)).toBe(true);
    }
  });
  it("residual allows status + Inbox + Settings only", () => {
    expect(isPathAllowed("residual", "/")).toBe(true);
    expect(isPathAllowed("residual", "/inbox")).toBe(true);
    expect(isPathAllowed("residual", "/inbox/123")).toBe(true);
    expect(isPathAllowed("residual", "/settings/general")).toBe(true);
    expect(isPathAllowed("residual", "/analytics")).toBe(false);
    expect(isPathAllowed("residual", "/process")).toBe(false);
    expect(isPathAllowed("residual", "/memory")).toBe(false);
  });
  it("off keeps the Inbox approval surface reachable (must-fix)", () => {
    expect(isPathAllowed("off", "/inbox")).toBe(true);
    expect(isPathAllowed("off", "/inbox/abc/decide")).toBe(true);
    expect(isPathAllowed("off", "/")).toBe(true);
    expect(isPathAllowed("off", "/settings/general")).toBe(false);
    expect(isPathAllowed("off", "/analytics")).toBe(false);
  });
  it("prefix matching does not leak (/inboxx is not /inbox)", () => {
    expect(isPathAllowed("off", "/inboxx")).toBe(false);
  });
});
