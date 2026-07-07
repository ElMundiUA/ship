// Per-stage execution backend — the "Execution Backend" chosen per stage
// in the process editor is persisted to .ship/config.yml under
// ``specialist.agent_profile`` and, when it names a concrete CLI, must
// override the workspace-bound provider for that stage at runtime. These
// pin the config → provider-override boundary that ``shipctl run`` uses.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  stageAgentProfileFromStates,
  providerForAgentProfile,
} from "../lib/runtime/routines.mjs";

// Mirrors a real .ship/config.yml: agent_profile lives on process.states,
// routines are empty, and two stages share the same specialist.
const STATES_CONFIG = {
  process: {
    routines: {},
    states: [
      { id: "planning", state: "planning", specialist: { id: "devops_platform", agent_profile: "cursor_agent" } },
      { id: "dev_implementation", state: "executing", specialist: { id: "developer", agent_profile: "codex_cli" } },
      { id: "validation", state: "executing", specialist: { id: "devops_platform" } },
    ],
  },
};

test("stageAgentProfileFromStates: planning resolves its per-stage backend", () => {
  assert.equal(
    stageAgentProfileFromStates(STATES_CONFIG, { fsmStage: "planning", specialist: "devops_platform" }),
    "cursor_agent",
  );
});

test("stageAgentProfileFromStates: validation (same specialist) does NOT inherit planning's backend", () => {
  assert.equal(
    stageAgentProfileFromStates(STATES_CONFIG, { fsmStage: "validation", specialist: "devops_platform" }),
    null,
  );
});

test("stageAgentProfileFromStates: null when no states", () => {
  assert.equal(stageAgentProfileFromStates({}, { fsmStage: "planning" }), null);
});

test("providerForAgentProfile: concrete CLIs pin a provider", () => {
  assert.equal(providerForAgentProfile("cursor_agent"), "cursor");
  assert.equal(providerForAgentProfile("codex_cli"), "codex");
  assert.equal(providerForAgentProfile("claude_code"), "claude");
});

test("providerForAgentProfile: abstract profiles defer to the workspace (null)", () => {
  for (const p of ["main", "auto", "cheaper", "ship_cloud_agent", "local_cli"]) {
    assert.equal(providerForAgentProfile(p), null, `${p} must not override`);
  }
  assert.equal(providerForAgentProfile(null), null);
  assert.equal(providerForAgentProfile(undefined), null);
});

test("integration: planning overrides to cursor, validation keeps the workspace provider", () => {
  const workspaceProvider = "claude";
  // planning → cursor_agent → cursor (override wins)
  const planning = stageAgentProfileFromStates(STATES_CONFIG, { fsmStage: "planning", specialist: "devops_platform" });
  assert.equal(providerForAgentProfile(planning) || workspaceProvider, "cursor");
  // validation → no profile → falls back to the workspace provider
  const validation = stageAgentProfileFromStates(STATES_CONFIG, { fsmStage: "validation", specialist: "devops_platform" });
  assert.equal(providerForAgentProfile(validation) || workspaceProvider, "claude");
});
