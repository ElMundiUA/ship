import { laneFanout, lanePatterns, LANE_FANOUT_DEFAULT } from "../config/schema.mjs";

const DEFAULT_SCHEDULE_WINDOW_MINUTES = 30;

export function routineEntries(config) {
  const process = config?.process;
  const raw = process && typeof process === "object" ? process.routines : null;
  const out = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      if (!item || typeof item !== "object") continue;
      const id = typeof item.id === "string" ? item.id.trim() : "";
      if (!id) continue;
      out.push([id, item]);
    }
  } else if (raw && typeof raw === "object") {
    for (const [id, item] of Object.entries(raw)) {
      if (!item || typeof item !== "object") continue;
      out.push([id, { id, ...item }]);
    }
  }
  return out;
}

export function routineMap(config) {
  return Object.fromEntries(routineEntries(config));
}

export function resolveExecutable(config, id) {
  const routines = routineMap(config);
  if (routines[id]) {
    return { kind: "routine", id, source: routines[id], executable: routineToExecutable(id, routines[id]) };
  }
  const lane = config?.lanes?.[id];
  if (lane) {
    return { kind: "lane", id, source: lane, executable: laneToExecutable(id, lane) };
  }
  return null;
}

export function executableIds(config) {
  return {
    routines: Object.keys(routineMap(config)).sort(),
    lanes: Object.keys(config?.lanes || {}).sort(),
  };
}

export function routineToExecutable(id, routine) {
  const trigger = normalizeRoutineTrigger(routine);
  // Phase 2.4: ``specialist:`` is the canonical agent-role slug;
  // ``pattern:`` is kept as a back-compat alias and translated to a
  // slug below (drop the ``role-`` prefix the legacy catalog used).
  const specialist = pickSpecialistSlug(routine);
  return {
    id,
    type: "routine",
    kind: trigger.kind,
    trigger,
    specialist,
    pattern: stringOrNull(routine.pattern),
    patterns: Array.isArray(routine.patterns) ? routine.patterns : undefined,
    pattern_version: stringOrNull(routine.pattern_version),
    fanout: routine.fanout || LANE_FANOUT_DEFAULT,
    idempotency: routine.idempotency || null,
    prompt: stringOrNull(routine.prompt) || stringOrNull(routine.instructions),
    agent_profile: stringOrNull(routine.agent_profile) || stringOrNull(routine.specialist?.agent_profile),
    // Per-stage concrete model id (e.g. "claude-sonnet-4-6"). Read from the
    // routine or its mirrored specialist record (the process-stage shape
    // carries it under ``specialist.model``). null → provider default.
    model: stringOrNull(routine.model) || stringOrNull(routine.specialist?.model),
    // Per-routine FSM stage override. When set, ``shipctl run`` uses
    // it instead of the role's default ``fsm_stage`` — that's how a
    // single role (e.g. ``ba``) serves both SDLC (``ba_requirements``)
    // and decomposition (``wbs``) without per-process role clones.
    fsm_stage: stringOrNull(routine.fsm_stage),
    // Drain-first scheduling: when multiple routines are cron-due in
    // the same tick (the common case for SDLC + decomposition because
    // every stage shares ``*/30 * * * *``), ``dueRoutines`` sorts by
    // this DESC so the LATER pipeline stage runs first. Reasoning is
    // Lean-WIP: a code-review-eligible ticket is closer to delivering
    // value than an intake one, so flushing the tail of the pipeline
    // outranks feeding the head. Routines without a priority — daily
    // / retro / sweeps — fall through to ``0`` (effectively last).
    pipeline_priority: numberOrZero(routine.pipeline_priority),
  };
}

/**
 * Resolve the per-stage model when it lives on ``process.states`` (the
 * process editor writes the operator's pick there under
 * ``specialist.model``) rather than on a routine. ``shipctl`` executes by
 * routine/stage and configs commonly ship ``routines: {}``, so match the
 * running stage to its state — by state ``id`` (== the canonical fsm_stage),
 * then canonical ``state``, then specialist slug — and read the model.
 * Returns null when unset (provider default).
 */
export function stageModelFromStates(config, { fsmStage, specialist } = {}) {
  const states = config?.process?.states;
  if (!Array.isArray(states)) return null;
  const modelOf = (s) => {
    const m = s?.specialist?.model;
    return typeof m === "string" && m.trim() ? m.trim() : null;
  };
  // Identify the running stage precisely — by state id (== the canonical
  // fsm_stage), else by canonical ``state``. When found, return ITS model
  // (even if null): do NOT fall back to specialist, or a stage that merely
  // shares a specialist (e.g. planning & validation both devops_platform)
  // would leak another stage's model.
  const stage = fsmStage
    ? states.find((s) => s?.id === fsmStage) ||
      states.find((s) => s?.state === fsmStage)
    : null;
  if (stage) return modelOf(stage);
  // Stage couldn't be identified — best-effort by specialist slug.
  const bySpec = specialist
    ? states.find((s) => s?.specialist?.id === specialist && modelOf(s))
    : null;
  return bySpec ? modelOf(bySpec) : null;
}

/**
 * Resolve the per-stage execution backend (``specialist.agent_profile``)
 * the same way as {@link stageModelFromStates}: match the running stage
 * to its ``process.states`` record by state ``id`` (== canonical
 * fsm_stage), then canonical ``state``, then specialist slug. Returns
 * null when unset — the caller then keeps the workspace-bound provider.
 *
 * Only concrete-CLI profiles matter to provider routing; ``main`` /
 * ``auto`` / ``cheaper`` / ``local_cli`` / ``ship_cloud_agent`` deliberately
 * return their literal value (or null) and are mapped to "no override" by
 * the caller — this helper stays a pure config reader.
 */
export function stageAgentProfileFromStates(config, { fsmStage, specialist } = {}) {
  const states = config?.process?.states;
  if (!Array.isArray(states)) return null;
  const profileOf = (s) => {
    const p = s?.specialist?.agent_profile;
    return typeof p === "string" && p.trim() ? p.trim() : null;
  };
  const stage = fsmStage
    ? states.find((s) => s?.id === fsmStage) ||
      states.find((s) => s?.state === fsmStage)
    : null;
  if (stage) return profileOf(stage);
  const bySpec = specialist
    ? states.find((s) => s?.specialist?.id === specialist && profileOf(s))
    : null;
  return bySpec ? profileOf(bySpec) : null;
}

/**
 * Map a per-stage ``agent_profile`` to the agent runtime provider it
 * pins, or null when it defers to the workspace-bound provider. Only the
 * three concrete-CLI profiles override; the abstract ones
 * (main/auto/cheaper/local_cli/ship_cloud_agent) intentionally return
 * null so the workspace binding wins. Kept beside the resolver so the run
 * command and tests share one source of truth.
 */
export const PROFILE_PROVIDER_OVERRIDE = Object.freeze({
  cursor_agent: "cursor",
  codex_cli: "codex",
  claude_code: "claude",
});

export function providerForAgentProfile(agentProfile) {
  if (!agentProfile) return null;
  return PROFILE_PROVIDER_OVERRIDE[agentProfile] || null;
}

/**
 * Pull the agent-role slug out of a routine, preferring the new
 * ``specialist:`` key but falling back to the legacy ``pattern:`` and
 * stripping the historical ``role-`` prefix when present.
 *
 * Object-form ``specialist`` (the process-state record with ``id`` /
 * ``name``) is treated as a slug source via ``specialist.id`` for
 * configs that mirror the process-stage shape into routines.
 */
function pickSpecialistSlug(routine) {
  if (typeof routine.specialist === "string") {
    const v = routine.specialist.trim();
    if (v) return v;
  }
  if (
    routine.specialist &&
    typeof routine.specialist === "object" &&
    typeof routine.specialist.id === "string"
  ) {
    const v = routine.specialist.id.trim();
    if (v) return v;
  }
  if (typeof routine.pattern === "string" && routine.pattern.trim()) {
    const p = routine.pattern.trim();
    return p.startsWith("role-") ? p.slice("role-".length) : p;
  }
  return null;
}

export function laneToExecutable(id, lane) {
  return {
    id,
    type: "lane",
    kind: lane.kind,
    trigger: laneTrigger(lane),
    pattern: lane.pattern,
    patterns: lane.patterns,
    pattern_version: lane.pattern_version,
    fanout: laneFanout(lane),
    idempotency: lane.idempotency || null,
    prompt: null,
    agent_profile: null,
    model: null,
  };
}

export function executablePatterns(executable) {
  return lanePatterns(executable);
}

export function executableFanout(executable) {
  return executable?.fanout || LANE_FANOUT_DEFAULT;
}

export function dueRoutines(config, { event, now = new Date() }) {
  const due = [];
  const skipped = [];
  for (const [routineId, routine] of routineEntries(config)) {
    if (routine.enabled === false) {
      skipped.push({ routine_id: routineId, reason: "disabled" });
      continue;
    }
    const executable = routineToExecutable(routineId, routine);
    if (!routineAcceptsEvent(executable, event)) {
      skipped.push({ routine_id: routineId, reason: "trigger_mismatch", trigger: executable.kind });
      continue;
    }
    if (event === "schedule") {
      const slot = latestCronMatch(executable.trigger.cron, {
        now,
        windowMinutes: executable.trigger.window_minutes,
      });
      if (!slot) {
        skipped.push({ routine_id: routineId, reason: "not_due", trigger: "schedule" });
        continue;
      }
      const windowEnd = new Date(slot.getTime() + executable.trigger.window_minutes * 60_000);
      due.push({
        routine_id: routineId,
        trigger: "schedule",
        cron: executable.trigger.cron,
        scheduled_for: slot.toISOString(),
        window_start: slot.toISOString(),
        window_end: windowEnd.toISOString(),
        window_key: `schedule:${routineId}:${formatWindowKey(slot)}`,
        pipeline_priority: executable.pipeline_priority,
      });
      continue;
    }
    const started = new Date(now);
    due.push({
      routine_id: routineId,
      trigger: event,
      scheduled_for: started.toISOString(),
      window_start: started.toISOString(),
      window_end: started.toISOString(),
      window_key: `${event}:${routineId}:${formatWindowKey(started)}`,
      pipeline_priority: executable.pipeline_priority,
    });
  }
  // Drain-first ordering: highest ``pipeline_priority`` runs first
  // when multiple routines are due in the same tick. Ties break on
  // ``routine_id`` ascending for deterministic test output and a
  // stable claim sequence (so two runners with clock drift don't
  // claim the routines in different orders on the same window).
  due.sort((a, b) => {
    const pa = numberOrZero(a.pipeline_priority);
    const pb = numberOrZero(b.pipeline_priority);
    if (pa !== pb) return pb - pa;
    return a.routine_id < b.routine_id ? -1 : a.routine_id > b.routine_id ? 1 : 0;
  });
  return { due, skipped };
}

export function dueLanesFromRoutines(due) {
  return due.map((routine) => ({
    lane_id: routine.routine_id,
    routine_id: routine.routine_id,
    kind: routine.trigger,
    reason: "due",
    window_key: routine.window_key,
    scheduled_for: routine.scheduled_for,
  }));
}

function normalizeRoutineTrigger(routine) {
  const trigger = routine.trigger && typeof routine.trigger === "object" ? routine.trigger : {};
  if (routine.schedule || trigger.type === "schedule") {
    const schedule = routine.schedule && typeof routine.schedule === "object" ? routine.schedule : {};
    const cron =
      stringOrNull(trigger.cron) ||
      stringOrNull(trigger.interval) ||
      stringOrNull(schedule.cron) ||
      stringOrNull(schedule.interval) ||
      (typeof routine.schedule === "string" ? routine.schedule : null);
    return {
      kind: "schedule",
      cron,
      window_minutes: parseWindowMinutes(trigger.window || schedule.window || routine.window),
      catchup: stringOrNull(trigger.catchup) || stringOrNull(schedule.catchup) || "latest",
    };
  }
  if (trigger.type === "event" || routine.event) {
    return {
      kind: "event",
      event: stringOrNull(trigger.event) || stringOrNull(routine.event),
    };
  }
  return { kind: "once" };
}

function laneTrigger(lane) {
  if (lane.kind === "schedule") {
    return {
      kind: "schedule",
      cron: stringOrNull(lane.cron),
      window_minutes: DEFAULT_SCHEDULE_WINDOW_MINUTES,
      catchup: "latest",
    };
  }
  if (lane.kind === "event") return { kind: "event", event: stringOrNull(lane.on) };
  return { kind: lane.kind || "once" };
}

function routineAcceptsEvent(executable, event) {
  if (event === "schedule") return executable.kind === "schedule";
  if (event === "manual") return ["schedule", "event", "once"].includes(executable.kind);
  if (executable.kind !== "event") return false;
  const configured = executable.trigger.event || "";
  return configured.split(",").map((part) => part.trim()).filter(Boolean).includes(event);
}

function latestCronMatch(cron, { now, windowMinutes }) {
  if (typeof cron !== "string" || !cron.trim()) return null;
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [minute, hour, dom, month, dow] = fields;
  const cursor = new Date(now);
  cursor.setUTCSeconds(0, 0);
  for (let offset = 0; offset <= windowMinutes; offset += 1) {
    const candidate = new Date(cursor.getTime() - offset * 60_000);
    if (
      cronFieldMatches(minute, candidate.getUTCMinutes(), 0, 59) &&
      cronFieldMatches(hour, candidate.getUTCHours(), 0, 23) &&
      cronFieldMatches(dom, candidate.getUTCDate(), 1, 31) &&
      cronFieldMatches(month, candidate.getUTCMonth() + 1, 1, 12) &&
      cronFieldMatches(dow, candidate.getUTCDay(), 0, 6)
    ) {
      return candidate;
    }
  }
  return null;
}

function cronFieldMatches(expr, value, min, max) {
  for (const rawPart of String(expr).split(",")) {
    const part = rawPart.trim();
    if (!part) continue;
    let step = 1;
    let base = part;
    if (part.includes("/")) {
      const pieces = part.split("/");
      base = pieces[0];
      step = Number.parseInt(pieces[1], 10);
      if (!Number.isInteger(step) || step <= 0) continue;
    }
    let start;
    let end;
    if (base === "*") {
      start = min;
      end = max;
    } else if (base.includes("-")) {
      const [a, b] = base.split("-");
      start = Number.parseInt(a, 10);
      end = Number.parseInt(b, 10);
    } else {
      start = Number.parseInt(base, 10);
      end = start;
    }
    if (!Number.isInteger(start) || !Number.isInteger(end)) continue;
    if (start <= value && value <= end && (value - start) % step === 0) return true;
  }
  return false;
}

function parseWindowMinutes(value) {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) return Math.floor(value);
  if (typeof value !== "string" || !value.trim()) return DEFAULT_SCHEDULE_WINDOW_MINUTES;
  const raw = value.trim();
  const match = raw.match(/^(\d+)\s*(m|min|minute|minutes)?$/i);
  if (match) return Math.max(1, Number.parseInt(match[1], 10));
  const hours = raw.match(/^(\d+)\s*(h|hr|hour|hours)$/i);
  if (hours) return Math.max(1, Number.parseInt(hours[1], 10) * 60);
  return DEFAULT_SCHEDULE_WINDOW_MINUTES;
}

function formatWindowKey(date) {
  return date.toISOString().slice(0, 16).replace(/[-:]/g, "").replace("T", "T");
}

function stringOrNull(value) {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function numberOrZero(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number.parseFloat(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}
