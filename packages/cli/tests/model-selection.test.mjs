// Per-stage model selection — the model chosen per stage in the process
// editor is persisted to .ship/config.yml under ``specialist.model`` and must
// surface on the runtime executable so ``shipctl run`` can pass it to the
// agent CLI as ``--model``. These pin the config → executable boundary.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  resolveExecutable,
  routineToExecutable,
  stageModelFromStates,
} from "../lib/runtime/routines.mjs";

// Mirrors a real .ship/config.yml: model lives on process.states, routines are
// empty, and two stages share the same specialist (devops_platform).
const STATES_CONFIG = {
  process: {
    routines: {},
    states: [
      { id: "planning", state: "planning", specialist: { id: "devops_platform", model: "claude-4.5-haiku" } },
      { id: "dev_implementation", state: "executing", specialist: { id: "developer" } },
      { id: "validation", state: "executing", specialist: { id: "devops_platform" } },
    ],
  },
};

test("stageModelFromStates: planning stage resolves its per-stage model", () => {
  assert.equal(
    stageModelFromStates(STATES_CONFIG, { fsmStage: "planning", specialist: "devops_platform" }),
    "claude-4.5-haiku",
  );
});

test("stageModelFromStates: validation (same specialist) does NOT inherit planning's model", () => {
  // Matched by state id, so the shared specialist must not leak the model.
  assert.equal(
    stageModelFromStates(STATES_CONFIG, { fsmStage: "validation", specialist: "devops_platform" }),
    null,
  );
});

test("stageModelFromStates: null when no states / no model", () => {
  assert.equal(stageModelFromStates({}, { fsmStage: "planning" }), null);
});

test("routineToExecutable: reads model from routine.model", () => {
  const ex = routineToExecutable("developer", {
    specialist: "developer",
    model: "claude-sonnet-4-6",
  });
  assert.equal(ex.model, "claude-sonnet-4-6");
});

test("routineToExecutable: reads model from the mirrored specialist record", () => {
  const ex = routineToExecutable("developer", {
    specialist: { id: "developer", name: "Developer", model: "gpt-5-codex" },
  });
  assert.equal(ex.model, "gpt-5-codex");
  assert.equal(ex.specialist, "developer");
});

test("routineToExecutable: model is null when unset (provider default)", () => {
  const ex = routineToExecutable("developer", { specialist: "developer" });
  assert.equal(ex.model, null);
});

test("resolveExecutable: surfaces the per-stage model from the config map", () => {
  const config = {
    process: {
      routines: {
        developer: { specialist: { id: "developer", model: "claude-opus-4-8" } },
      },
    },
  };
  const resolved = resolveExecutable(config, "developer");
  assert.equal(resolved.executable.model, "claude-opus-4-8");
});
