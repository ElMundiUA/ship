/**
 * W8.1 (ELS-256) — CLI-side workflow spec parser mirrors the
 * server loader's reject-before-run contract.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { loadSpec, WorkflowSpecError, HARD_FANOUT_CEILING } from "../lib/workflow/loadSpec.mjs";

const VALID = `
name: pr-review
steps:
  - id: fan
    kind: parallel
    steps:
      - {id: a, kind: pipeline, agent: {kind: reasoning}}
      - {id: b, kind: pipeline, agent: {kind: coding, provider: claude}}
  - id: join
    kind: barrier
    needs: [a, b]
  - id: synth
    kind: synthesize
    needs: [join]
    output_schema: {type: object}
`;

test("valid spec round-trips", () => {
  const spec = loadSpec(VALID);
  assert.equal(spec.name, "pr-review");
  assert.equal(spec.max_fanout, 4);
  assert.equal(spec.steps.length, 3);
});

test("unknown kind names the step", () => {
  assert.throws(
    () => loadSpec(VALID.replace("kind: barrier", "kind: warp")),
    (e) => e instanceof WorkflowSpecError && /warp/.test(e.message),
  );
});

test("unknown provider names the step", () => {
  assert.throws(
    () => loadSpec(VALID.replace("provider: claude", "provider: hal9000")),
    /hal9000/,
  );
});

test("fan-out ceiling enforced", () => {
  assert.throws(
    () => loadSpec(`name: x\nmax_fanout: ${HARD_FANOUT_CEILING + 1}\nsteps:\n  - {id: s, kind: pipeline, agent: {kind: reasoning}}`),
    /hard ceiling/,
  );
});

test("synthesize requires output_schema", () => {
  assert.throws(
    () => loadSpec("name: x\nsteps:\n  - {id: s, kind: synthesize}"),
    /output_schema/,
  );
});

test("unknown needs edge rejected", () => {
  assert.throws(
    () => loadSpec("name: x\nsteps:\n  - {id: s, kind: barrier, needs: [ghost]}"),
    /ghost/,
  );
});
