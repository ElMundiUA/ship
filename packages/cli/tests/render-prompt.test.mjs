import { test } from "node:test";
import assert from "node:assert/strict";
import { renderPrompt } from "../lib/commands/run.mjs";

test("renderPrompt prepends file_coordination_warning before routine instructions", () => {
  const warning =
    "> **File-coordination warning**:\n>\n> PR #1 (ELS-2) touches migrations.";
  const out = renderPrompt({
    patternBody: "Do work on {{ISSUE}}.",
    baseBody: "",
    role: "developer",
    routineSpec: { prompt: "Routine body here." },
    task: {
      ticket_ref: "ELS-3",
      title: "Task",
      body: "Desc",
      file_coordination_warning: warning,
    },
    fsmStage: "dev_implementation",
    finishCtx: { process: "development" },
  });
  const idxWarn = out.indexOf("File-coordination warning");
  const idxRoutine = out.indexOf("## Routine instructions");
  assert.ok(idxWarn >= 0);
  assert.ok(idxRoutine >= 0);
  assert.ok(idxWarn < idxRoutine, "warning must precede routine instructions");
});

test("renderPrompt prefers FILE_OVERLAP_WARNINGS env over task field", () => {
  const envWarning = "## File overlap warnings (advisory)\n\nHard migration conflict on 0074.";
  const out = renderPrompt({
    patternBody: "Do work on {{ISSUE}}.",
    baseBody: "",
    role: "developer",
    routineSpec: { prompt: "Routine body here." },
    task: {
      ticket_ref: "ELS-3",
      title: "Task",
      body: "Desc",
      file_coordination_warning: "> stale task warning",
    },
    fsmStage: "dev_implementation",
    fileOverlapWarnings: envWarning,
    finishCtx: { process: "development" },
  });
  assert.ok(out.includes("0074"));
  assert.ok(!out.includes("stale task warning"));
});
