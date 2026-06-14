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
    for (const p of ["/", "/approve/x", "/analytics", "/process/x", "/settings/general"]) {
      expect(isPathAllowed("full", p)).toBe(true);
    }
  });
  it("residual allows hub + approve + oauth + Chat + Settings only", () => {
    expect(isPathAllowed("residual", "/")).toBe(true);
    expect(isPathAllowed("residual", "/approve/123")).toBe(true);
    expect(isPathAllowed("residual", "/oauth/authorize")).toBe(true);
    expect(isPathAllowed("residual", "/chat")).toBe(true);
    expect(isPathAllowed("residual", "/settings/general")).toBe(true);
    expect(isPathAllowed("residual", "/inbox")).toBe(false);
    expect(isPathAllowed("residual", "/analytics")).toBe(false);
    expect(isPathAllowed("residual", "/process")).toBe(false);
    expect(isPathAllowed("residual", "/memory")).toBe(false);
  });
  it("off keeps the /approve + /oauth surfaces reachable (must-fix)", () => {
    expect(isPathAllowed("off", "/approve/abc")).toBe(true);
    expect(isPathAllowed("off", "/oauth/authorize")).toBe(true);
    expect(isPathAllowed("off", "/")).toBe(true);
    expect(isPathAllowed("off", "/inbox")).toBe(false);
    expect(isPathAllowed("off", "/chat")).toBe(false);
    expect(isPathAllowed("off", "/settings/general")).toBe(false);
    expect(isPathAllowed("off", "/analytics")).toBe(false);
  });
  it("prefix matching does not leak (/approvex is not /approve)", () => {
    expect(isPathAllowed("off", "/approvex")).toBe(false);
  });
});
