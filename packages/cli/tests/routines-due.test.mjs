// Drain-first scheduler — ``dueRoutines`` sorts the cron-due
// candidates by ``pipeline_priority`` DESC so the LATER SDLC stage
// runs first.
//
// The operator's bug, restated: when N stages are cron-due in the
// same window (the common case because every SDLC routine ships
// with the canonical half-hourly cron), the old code returned them
// in YAML iteration order, ``intake`` always won, the tail of the
// pipeline (code review, qa automation) starved, and tickets piled
// up at the planning gate. Drain-first flips that: empty the tail
// first, minimize WIP, prefer flushing tickets toward Done over
// starting new ones.
//
// Test surface is the local computation only — we don't exercise
// the ``/routine-runs/claim`` HTTP path here; that's covered by
// ``trigger.mjs``'s integration footprint elsewhere. The single
// invariant this file pins is "due array exits ``dueRoutines``
// already sorted by drain-first priority."

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  dueRoutines,
  routineToExecutable,
} from "../lib/runtime/routines.mjs";

/** Build a single schedule-trigger routine spec. */
function _scheduleRoutine(priority, extras = {}) {
  return {
    enabled: true,
    trigger: { type: "schedule", cron: "*/30 * * * *", window: "30m" },
    specialist: "developer",
    pipeline_priority: priority,
    ...extras,
  };
}

// A timestamp that lands on a half-hour boundary so every routine
// with the canonical SDLC cron is due.
const ON_THE_HALF = new Date("2026-05-11T15:00:00.000Z");

test("dueRoutines: SDLC chain returns drain-first order (code_review first)", () => {
  const config = {
    process: {
      routines: {
        intake: _scheduleRoutine(10),
        tech_arch_plan: _scheduleRoutine(20),
        qa_arch_plan: _scheduleRoutine(30),
        dev_implementation: _scheduleRoutine(40),
        qa_manual: _scheduleRoutine(50),
        qa_automation: _scheduleRoutine(60),
        code_review: _scheduleRoutine(70),
      },
    },
  };
  const { due } = dueRoutines(config, { event: "schedule", now: ON_THE_HALF });
  assert.deepEqual(
    due.map((r) => r.routine_id),
    [
      "code_review",
      "qa_automation",
      "qa_manual",
      "dev_implementation",
      "qa_arch_plan",
      "tech_arch_plan",
      "intake",
    ],
    "due list must come out highest-priority first (code_review > intake)",
  );
});

test("dueRoutines: missing pipeline_priority defaults to 0 (lowest)", () => {
  // Daily / retro / sweeps don't carry a priority. They must sort
  // BELOW any priority-bearing SDLC routine when they happen to
  // share a tick (rare but possible — e.g., 09:00 UTC where the
  // daily ``0 9 * * *`` cron AND the SDLC ``*/30 * * * *`` cron
  // both fire).
  const config = {
    process: {
      routines: {
        intake: _scheduleRoutine(10),
        // No ``pipeline_priority`` — supporting routine shape.
        daily: {
          enabled: true,
          trigger: { type: "schedule", cron: "*/30 * * * *", window: "30m" },
          specialist: "daily-retro",
        },
      },
    },
  };
  const { due } = dueRoutines(config, { event: "schedule", now: ON_THE_HALF });
  assert.deepEqual(
    due.map((r) => r.routine_id),
    ["intake", "daily"],
    "intake (priority 10) must rank above daily (default 0)",
  );
});

test("dueRoutines: equal priorities break ties on routine_id ASC", () => {
  // Determinism matters: two GitHub-Actions runners triggering at
  // the same window must claim routines in identical order so
  // window contention is reproducible. Same priority → lexicographic
  // routine_id, ascending.
  const config = {
    process: {
      routines: {
        zebra: _scheduleRoutine(50),
        alpha: _scheduleRoutine(50),
        mike: _scheduleRoutine(50),
      },
    },
  };
  const { due } = dueRoutines(config, { event: "schedule", now: ON_THE_HALF });
  assert.deepEqual(
    due.map((r) => r.routine_id),
    ["alpha", "mike", "zebra"],
    "ties must break ASC by routine_id so runners agree on order",
  );
});

test("dueRoutines: each due row carries its pipeline_priority", () => {
  // Trigger.mjs and downstream consumers (audit logs, future
  // server-side probe) read ``pipeline_priority`` off the due rows;
  // pin that it survives the projection.
  const config = {
    process: {
      routines: {
        code_review: _scheduleRoutine(70),
        intake: _scheduleRoutine(10),
      },
    },
  };
  const { due } = dueRoutines(config, { event: "schedule", now: ON_THE_HALF });
  const byId = Object.fromEntries(due.map((r) => [r.routine_id, r]));
  assert.equal(byId.code_review.pipeline_priority, 70);
  assert.equal(byId.intake.pipeline_priority, 10);
});

test("routineToExecutable: numeric pipeline_priority surfaces; missing → 0", () => {
  const ex = routineToExecutable("intake", { pipeline_priority: 10 });
  assert.equal(ex.pipeline_priority, 10);

  const ex0 = routineToExecutable("daily", {});
  assert.equal(
    ex0.pipeline_priority,
    0,
    "routines without an explicit priority must surface 0 (sort last)",
  );

  // Non-numeric garbage shouldn't make the picker blow up.
  const exJunk = routineToExecutable("junk", { pipeline_priority: "abc" });
  assert.equal(exJunk.pipeline_priority, 0);
});
