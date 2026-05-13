// Telemetry parsing for ``claude --output-format stream-json``.
//
// Operator-facing bug: before this change, ``runClaudeAgent`` ran
// claude with ``--output-format text`` and the workflow log carried
// only the agent's final reply. Token usage + cost were never
// captured, so when intake / dev runs burned tokens we had no
// visibility into the breakdown (input vs cache_read vs output).
//
// The new shape parses the trailing ``result`` event from the
// stream-json output and projects it into a stable shape the
// caller can ship to an audit row. These tests pin the projection
// contract — what we accept upstream, what we promise downstream —
// so a future Claude Code release renaming a field surfaces as a
// test failure instead of silent zeros on the dashboard.

import { test } from "node:test";
import assert from "node:assert/strict";

import { extractUsage, formatUsageLine } from "../lib/agents/claude.mjs";


// Hand-captured ``result`` event from a real ``claude --print
// --output-format stream-json --verbose`` invocation. Trimmed of
// the noisy ``modelUsage`` / ``iterations`` / ``rateLimit`` arms —
// the projection only touches the fields below, and pinning the
// minimum surface avoids hard-coding upstream churn.
const SAMPLE_RESULT_EVENT = {
  type: "result",
  subtype: "success",
  is_error: false,
  duration_ms: 3242,
  duration_api_ms: 3146,
  num_turns: 4,
  result: "Done.",
  stop_reason: "end_turn",
  total_cost_usd: 0.06647225,
  usage: {
    input_tokens: 6,
    cache_creation_input_tokens: 9155,
    cache_read_input_tokens: 17647,
    output_tokens: 16,
  },
};


test("extractUsage: projects the result event onto the stable shape", () => {
  const out = extractUsage(SAMPLE_RESULT_EVENT);
  assert.deepEqual(out, {
    input_tokens: 6,
    cache_read_input_tokens: 17647,
    cache_creation_input_tokens: 9155,
    output_tokens: 16,
    total_cost_usd: 0.06647225,
    num_turns: 4,
    duration_ms: 3242,
  });
});


test("extractUsage: missing fields default to 0 (not undefined)", () => {
  // A pathological event where the upstream payload is incomplete —
  // could happen if claude crashes mid-result or a future release
  // moves a field. We promise the audit row that every numeric slot
  // is a real number; ``undefined`` would 500 the JSON serializer.
  const out = extractUsage({ type: "result" });
  assert.equal(out.input_tokens, 0);
  assert.equal(out.cache_read_input_tokens, 0);
  assert.equal(out.cache_creation_input_tokens, 0);
  assert.equal(out.output_tokens, 0);
  assert.equal(out.total_cost_usd, 0);
  assert.equal(out.num_turns, 0);
  assert.equal(out.duration_ms, 0);
});


test("extractUsage: tolerates non-numeric garbage in payload", () => {
  // Defensive against an upstream schema change (e.g. a field that
  // becomes a string-formatted number). Don't crash; coerce to 0
  // and let the operator notice the zero on the dashboard.
  const out = extractUsage({
    type: "result",
    total_cost_usd: "0.42",
    num_turns: null,
    duration_ms: undefined,
    usage: {
      input_tokens: NaN,
      output_tokens: "many",
    },
  });
  assert.equal(out.total_cost_usd, 0);
  assert.equal(out.input_tokens, 0);
  assert.equal(out.output_tokens, 0);
});


test("formatUsageLine: produces the one-liner that lands in the CI log", () => {
  const line = formatUsageLine({
    input_tokens: 123,
    cache_read_input_tokens: 45678,
    cache_creation_input_tokens: 9876,
    output_tokens: 543,
    total_cost_usd: 0.7531,
    num_turns: 12,
    duration_ms: 187500,
  });
  // The exact format is operator-facing — humans read these grepped
  // out of workflow logs. Pin it tightly; a reflow should slip into
  // the test as a deliberate update rather than silent drift.
  assert.equal(
    line,
    "usage: input=123 cache_read=45678 cache_create=9876 " +
      "output=543 cost=$0.7531 turns=12 dur=187.5s",
  );
});


test("formatUsageLine: cost rounds to 4 decimals even when the upstream is noisy", () => {
  const line = formatUsageLine({
    input_tokens: 0,
    cache_read_input_tokens: 0,
    cache_creation_input_tokens: 0,
    output_tokens: 0,
    total_cost_usd: 0.06647225,
    num_turns: 0,
    duration_ms: 0,
  });
  assert.match(line, /cost=\$0\.0665/);
});
