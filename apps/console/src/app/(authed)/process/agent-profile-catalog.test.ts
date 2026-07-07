import { describe, expect, it } from "vitest";

import { AGENT_PROFILE_OPTIONS } from "./agent-profile-catalog";

describe("AGENT_PROFILE_OPTIONS", () => {
  const ids = AGENT_PROFILE_OPTIONS.map((o) => o.id);

  it("offers only the runtime-honoured backends", () => {
    expect(ids).toEqual(["main", "cursor_agent", "codex_cli", "claude_code"]);
  });

  it("does not surface the legacy no-op profiles", () => {
    for (const legacy of ["auto", "cheaper", "ship_cloud_agent", "local_cli"]) {
      expect(ids).not.toContain(legacy);
    }
  });

  it("keeps the workspace-default option first (used as the fallback)", () => {
    expect(AGENT_PROFILE_OPTIONS[0]?.id).toBe("main");
  });
});
