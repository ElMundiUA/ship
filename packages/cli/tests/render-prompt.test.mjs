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

test("renderPrompt mode=local strips ticket block + exit protocol (ELS-246)", () => {
  const out = renderPrompt({
    patternBody: "{{BASE}}\nDo work on {{ISSUE}}.\n{{DESCRIPTION}}",
    baseBody: "System base for {{ROLE}}.",
    role: "developer",
    routineSpec: {},
    task: null,
    fsmStage: null,
    finishCtx: null,
    mode: "local",
    localAsk: "Tweak the landing hero copy\n\nMake the subtitle shorter.",
  });
  // System base + role substitution still present (thesis 5).
  assert.ok(out.includes("System base for developer."));
  // No ticket block, no raw placeholder leakage.
  assert.ok(!out.includes("## Task"));
  assert.ok(!out.includes("{{ISSUE}}"));
  assert.ok(out.includes("(local scratch session — no ticket)"));
  // No sidecar/finish/PR protocol...
  assert.ok(!out.includes("## Required exit protocol"));
  assert.ok(!out.includes("agent-finish.json\\` in the repo workdir"));
  assert.ok(!out.includes("/agent-runs/finish"));
  // ...replaced by the local scratch contract.
  assert.ok(out.includes("## Local scratch run — required behavior"));
  assert.ok(out.includes("Do **not** open a pull request."));
  assert.ok(out.includes("they should escalate"));
  // Operator ask + lifecycle hooks survive.
  assert.ok(out.includes("## Operator ask"));
  assert.ok(out.includes("Make the subtitle shorter."));
  assert.ok(out.includes("## Lifecycle hooks (Phase 4)"));
  assert.ok(out.includes("Before your session ends,"));
});

test("renderPrompt default mode still renders the ticket exit protocol", () => {
  const out = renderPrompt({
    patternBody: "Do work on {{ISSUE}}.",
    baseBody: "",
    role: "developer",
    routineSpec: { prompt: "Routine body here." },
    task: { ticket_ref: "ELS-3", title: "Task", body: "Desc" },
    fsmStage: "dev_implementation",
    finishCtx: { process: "development", role: "developer", ticketRef: "ELS-3" },
  });
  assert.ok(out.includes("## Required exit protocol"));
  assert.ok(out.includes("## Task"));
  assert.ok(!out.includes("## Local scratch run"));
});
