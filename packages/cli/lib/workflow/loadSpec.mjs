/**
 * CLI-side workflow spec parser (W8.1, ELS-256).
 *
 * Mirrors apps/backend/app/services/workflow/spec.py — same step
 * kinds, same hard ceilings, same reject-before-anything-runs
 * contract. The CLI uses this for `.ship/workflows/*.yaml` linting
 * (init/doctor surface); the server-side loader stays the
 * authoritative validator at dispatch time.
 */

import { parse as parseYaml } from "yaml";

export const HARD_FANOUT_CEILING = 8;
export const DEFAULT_MAX_FANOUT = 4;
export const DEFAULT_MAX_DEPTH = 2;

export const STEP_KINDS = [
  "parallel",
  "pipeline",
  "loop",
  "barrier",
  "synthesize",
  "judge",
  "verify",
];
const STRUCTURED_KINDS = ["synthesize", "judge", "verify"];
const CODING_PROVIDERS = ["claude", "codex", "cursor", "ship"];

export class WorkflowSpecError extends Error {}

function fail(msg) {
  throw new WorkflowSpecError(msg);
}

function validateStep(step, path) {
  if (!step || typeof step !== "object") fail(`${path}: step must be a mapping`);
  const id = step.id;
  if (!id || typeof id !== "string") fail(`${path}: step requires an id`);
  if (!STEP_KINDS.includes(step.kind)) {
    fail(`step '${id}': unknown step kind '${step.kind}' (known: ${STEP_KINDS.join(", ")})`);
  }
  if (step.agent) {
    const { kind, provider = "reasoning" } = step.agent;
    if (kind === "coding") {
      if (!CODING_PROVIDERS.includes(provider)) {
        fail(`step '${id}': unknown coding provider '${provider}'`);
      }
    } else if (kind === "reasoning") {
      if (provider !== "reasoning") {
        fail(`step '${id}': reasoning leaves use provider 'reasoning'`);
      }
    } else {
      fail(`step '${id}': agent.kind must be coding|reasoning`);
    }
  }
  if (STRUCTURED_KINDS.includes(step.kind)) {
    if (!step.output_schema || typeof step.output_schema !== "object") {
      fail(`step '${id}': ${step.kind} requires output_schema`);
    }
    if (!("type" in step.output_schema)) {
      fail(`step '${id}': output_schema must carry 'type'`);
    }
  }
  if (step.kind === "parallel") {
    if (!Array.isArray(step.steps) || step.steps.length === 0) {
      fail(`step '${id}': parallel requires nested steps`);
    }
  } else if (Array.isArray(step.steps) && step.steps.length > 0) {
    fail(`step '${id}': only parallel steps nest children`);
  }
  if (step.kind === "barrier" && (!Array.isArray(step.needs) || !step.needs.length)) {
    fail(`step '${id}': barrier requires needs[]`);
  }
  if ((step.kind === "pipeline" || step.kind === "loop") && !step.agent) {
    fail(`step '${id}': ${step.kind} requires agent`);
  }
  (step.steps || []).forEach((s, i) => validateStep(s, `${path}.steps[${i}]`));
}

function staticDepth(steps, level) {
  let deepest = level;
  for (const s of steps) {
    if (Array.isArray(s.steps) && s.steps.length) {
      deepest = Math.max(deepest, staticDepth(s.steps, level + 1));
    }
  }
  return deepest;
}

function collectIds(steps, ids) {
  for (const s of steps) {
    if (ids.has(s.id)) fail(`duplicate step id '${s.id}'`);
    ids.add(s.id);
    collectIds(s.steps || [], ids);
  }
}

function checkNeeds(steps, ids) {
  for (const s of steps) {
    for (const need of s.needs || []) {
      if (!ids.has(need)) fail(`step '${s.id}': needs unknown step '${need}'`);
    }
    checkNeeds(s.steps || [], ids);
  }
}

function checkFanout(steps, maxFanout) {
  for (const s of steps) {
    if (s.kind === "parallel" && s.steps.length > maxFanout) {
      fail(`step '${s.id}': fan-out ${s.steps.length} exceeds max_fanout=${maxFanout}`);
    }
    checkFanout(s.steps || [], maxFanout);
  }
}

/**
 * Parse + validate one workflow YAML document. Throws
 * WorkflowSpecError naming the offending step on any rejection.
 */
export function loadSpec(text) {
  let raw;
  try {
    raw = parseYaml(text);
  } catch (err) {
    fail(`invalid YAML: ${err.message}`);
  }
  if (!raw || typeof raw !== "object") fail("workflow spec must be a YAML mapping");
  if (!raw.name || typeof raw.name !== "string") fail("workflow requires a name");
  if (!Array.isArray(raw.steps) || raw.steps.length === 0) {
    fail("workflow requires at least one step");
  }
  const maxFanout = raw.max_fanout ?? DEFAULT_MAX_FANOUT;
  const maxDepth = raw.max_depth ?? DEFAULT_MAX_DEPTH;
  if (maxFanout > HARD_FANOUT_CEILING) {
    fail(`max_fanout=${maxFanout} exceeds the hard ceiling ${HARD_FANOUT_CEILING}`);
  }
  raw.steps.forEach((s, i) => validateStep(s, `steps[${i}]`));
  const depth = staticDepth(raw.steps, 1);
  if (depth > maxDepth) {
    fail(`static step graph depth ${depth} exceeds max_depth=${maxDepth}`);
  }
  const ids = new Set();
  collectIds(raw.steps, ids);
  checkNeeds(raw.steps, ids);
  checkFanout(raw.steps, maxFanout);
  return {
    name: raw.name,
    version: String(raw.version ?? "1"),
    inputs: raw.inputs ?? {},
    max_fanout: maxFanout,
    max_depth: maxDepth,
    steps: raw.steps,
  };
}
