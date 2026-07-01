import { deriveDescriptionFromPrompt } from "@/lib/derive-routine-description";
import { parseScheduleFromYaml } from "@/lib/routine-schedule-spec";
import type {
  ApiProcess,
  ApiProcessRoutine,
  ApiProcessState,
  ApiProcessTransition,
  ApiRepoConfig,
} from "@/lib/api/client";
import { CANONICAL_STATES, type CanonicalState } from "@/lib/api/types";

// Mirror of backend ``_LEGACY_STAGE_TO_STATE`` — used as a safety net
// when a stage entry in .ship/config.yml predates the ``state`` field.
// Once every workspace has been re-seeded with the canonical stage
// schema, this can shrink to a single "planning" default.
const LEGACY_STAGE_STATE: Record<string, CanonicalState> = {
  task_intake: "planning",
  bug_triage: "planning",
  ba_requirements: "planning",
  tech_arch_plan: "planning",
  qa_arch_plan: "planning",
  dev_implementation: "executing",
  qa_manual: "executing",
  qa_automation: "executing",
  // ``pr_review`` was the legacy 5-state name; ``code_review`` is the
  // Phase 1.5 canonical id. Both map to ``reviewing`` so old configs
  // that haven't been re-seeded yet still resolve.
  pr_review: "reviewing",
  code_review: "reviewing",
};

function canonicalStateFromConfigRow(
  row: Record<string, unknown>,
  stageId: string,
  fallback: ApiProcessState | undefined,
): CanonicalState {
  const fromYaml = stringValue(row.state);
  if (fromYaml && (CANONICAL_STATES as readonly string[]).includes(fromYaml)) {
    return fromYaml as CanonicalState;
  }
  if (fallback?.state) return fallback.state;
  return LEGACY_STAGE_STATE[stageId] ?? "planning";
}

type ProcessStateWithAgentProfile = ApiProcessState & {
  specialist_agent_profile?: string | null;
  // Concrete model id for this stage (e.g. "claude-sonnet-4-6"). null/empty
  // means "use the provider's default model". Serialised as specialist.model.
  specialist_model?: string | null;
};

export type ProcessConfigSource =
  | "repo-process"
  | "repo-lanes"
  | "missing"
  | "fallback";

export function selectConfigSource(
  config: ApiRepoConfig | null,
  repoProcess: ApiProcess | null,
): ProcessConfigSource {
  if (repoProcess) return "repo-process";
  if (config?.exists) return "repo-lanes";
  if (config) return "missing";
  return "fallback";
}

export function processFromRepoConfig(
  config: ApiRepoConfig | null,
  fallback: ApiProcess,
): ApiProcess | null {
  const rawProcess = asRecord(config?.parsed?.process);
  if (!rawProcess) return null;

  // Repo configs serialise a single process today (the SDLC, id
  // "development"). When the operator opens a different process — e.g.
  // ``/process/decomposition`` for the project-anchor pipeline — the
  // YAML overlay would otherwise replace the API's correct projection
  // with the SDLC and the editor reads as if every process is the
  // SDLC. Skip the YAML merge unless its id matches the process the
  // operator actually navigated to. The API projection is canonical
  // for processes the YAML doesn't model.
  const yamlId = stringValue(rawProcess.id) ?? null;
  if (yamlId && yamlId !== fallback.id) return null;

  const rawStates = Array.isArray(rawProcess.states) ? rawProcess.states : null;
  if (!rawStates || rawStates.length === 0) return null;

  const fallbackById = new Map(fallback.states.map((state) => [state.id, state]));
  const states = rawStates
    .map((item, index) => stateFromConfig(item, fallbackById, index))
    .filter((state): state is ApiProcessState => state != null);

  if (states.length === 0) return null;

  const transitionByPair = new Map(
    fallback.transitions.map((transition) => [
      `${transition.from_state_id}->${transition.to_state_id}`,
      transition,
    ]),
  );
  const rawTransitions = Array.isArray(rawProcess.transitions)
    ? rawProcess.transitions
    : null;
  const transitionsFromConfig = rawTransitions && rawTransitions.length > 0
    ? rawTransitions
        .map((item, index) => {
          const row = asRecord(item);
          if (!row) return null;
          const from = stringValue(row.from);
          const to = stringValue(row.to);
          if (!from || !to) return null;
          const fallbackTransition = transitionByPair.get(`${from}->${to}`);
          return {
            id: fallbackTransition?.id ?? `${from}_to_${to}_${index + 1}`,
            from_state_id: from,
            to_state_id: to,
            conditions: stringValue(row.condition)
              ? [{ expression: stringValue(row.condition) as string }]
              : fallbackTransition?.conditions ?? [],
            requires_human: requiresHumanFromConfigRow(row, fallbackTransition),
          } as ApiProcessTransition;
        })
        .filter(
          (transition): transition is ApiProcessTransition => transition != null,
        )
    : states.slice(0, -1).map(
        (state, index) =>
          ({
            id: `${state.id}_to_${states[index + 1].id}`,
            from_state_id: state.id,
            to_state_id: states[index + 1].id,
            conditions: [{ expression: "exit_conditions_met == true" }],
          }) as ApiProcessTransition,
      );

  const rawRoutines = routineRowsFromConfig(rawProcess.routines);
  const routines = rawRoutines
    ? mergeProcessRoutines(fallback.routines, rawRoutines)
    : fallback.routines;

  return {
    ...fallback,
    id: stringValue(rawProcess.id) ?? fallback.id,
    name: stringValue(rawProcess.name) ?? fallback.name,
    primary: booleanValue(rawProcess.primary) ?? fallback.primary,
    state_count: states.length,
    states,
    transitions: transitionsFromConfig,
    routines,
    schedule: scheduleFromConfig(rawProcess.schedule) ?? fallback.schedule ?? null,
  };
}

export function processConfigFromApiProcess(process: ApiProcess): Record<string, unknown> {
  return {
    id: process.id,
    name: process.name,
    primary: process.primary,
    states: process.states.map((state) => ({
      id: state.id,
      name: state.name,
      state: state.state,
      specialist: {
        id: state.specialist_id,
        name: state.specialist_name,
        agent_profile: agentProfileFromState(state),
        ...(modelFromState(state) ? { model: modelFromState(state) } : {}),
      },
      instructions: state.instructions,
      ...(state.layout ? { layout: state.layout } : {}),
      ...(state.triggers && state.triggers.length
        ? { triggers: state.triggers }
        : {}),
      ...(state.exit_conditions && state.exit_conditions.length
        ? { exit_conditions: state.exit_conditions }
        : {}),
      ...(state.block_conditions && state.block_conditions.length
        ? { block_conditions: state.block_conditions }
        : {}),
    })),
    transitions: process.transitions.map((transition) => ({
      from: transition.from_state_id,
      to: transition.to_state_id,
      condition: transition.conditions[0]?.expression,
      ...(transition.requires_human ? { requires_human: true } : {}),
    })),
    ...(process.schedule ? { schedule: process.schedule } : {}),
    process_schema_version: 1,
    routines: Object.fromEntries(process.routines.map((routine) => {
      const row: Record<string, unknown> = {
        name: routine.name,
      };
      if (routine.enabled === false) row.enabled = false;
      if (routine.schedule) row.cadence = routine.schedule;
      if (routine.trigger && Object.keys(routine.trigger).length) {
        row.trigger = routine.trigger;
      } else if (routine.schedule) {
        row.trigger = { type: "schedule", cron: routine.schedule, window: "30m" };
      }
      if (routine.prompt?.trim()) row.prompt = routine.prompt;
      if (routine.specialist_id) row.specialist_id = routine.specialist_id;
      if (routine.specialist_name) row.specialist_name = routine.specialist_name;
      if (routine.scope && Object.keys(routine.scope).length) row.scope = routine.scope;
      if (routine.output && Object.keys(routine.output).length) row.output = routine.output;
      if (routine.prompt_record) row.prompt_record = routine.prompt_record;
      const userDescription = routine.description?.trim();
      if (userDescription) {
        row.description = userDescription;
      } else if (routine.prompt?.trim()) {
        row.description = deriveDescriptionFromPrompt(routine.prompt);
      }
      if (routine.schedule_spec) {
        row.schedule = routine.schedule_spec;
      }
      return [routine.id, row] as const;
    })),
  };
}

function stateFromConfig(
  value: unknown,
  fallbackById: Map<string, ApiProcessState>,
  index: number,
): ApiProcessState | null {
  const row = asRecord(value);
  if (!row) return null;

  const id = stringValue(row.id) ?? stringValue(row.state_id) ?? `state_${index + 1}`;
  const fallback = fallbackById.get(id);
  const specialist = asRecord(row.specialist);

  return {
    id,
    name: stringValue(row.name) ?? fallback?.name ?? titleFromId(id),
    state: canonicalStateFromConfigRow(row, id, fallback),
    specialist_id:
      stringValue(row.specialist_id) ??
      stringValue(specialist?.id) ??
      fallback?.specialist_id ??
      "owner",
    specialist_name:
      stringValue(row.specialist_name) ??
      stringValue(specialist?.name) ??
      fallback?.specialist_name ??
      "Owner",
    specialist_agent_profile:
      stringValue(specialist?.agent_profile) ??
      stringValue(row.agent_profile) ??
      agentProfileFromState(fallback) ??
      "main",
    specialist_model:
      stringValue(specialist?.model) ??
      stringValue(row.model) ??
      modelFromState(fallback),
    instructions:
      stringValue(row.instructions) ??
      stringValue(row.description) ??
      fallback?.instructions ??
      "",
    layout: layoutFromConfig(row.layout) ?? fallback?.layout ?? null,
    triggers:
      triggersFromConfig(row.triggers) ??
      fallback?.triggers ??
      [{ type: "manual", interval: null, event: null }],
    exit_conditions:
      conditionsFromConfig(row.exit_conditions) ??
      fallback?.exit_conditions ??
      [],
    block_conditions:
      conditionsFromConfig(row.block_conditions) ??
      fallback?.block_conditions ??
      [],
    runtime: fallback?.runtime ?? {
      task_count: 0,
      blocked_count: 0,
      last_execution_time: null,
      health: "ok",
    },
  } as ProcessStateWithAgentProfile;
}

function routineRowsFromConfig(value: unknown): Record<string, unknown>[] | null {
  if (Array.isArray(value)) {
    return value
      .map((item) => asRecord(item))
      .filter((row): row is Record<string, unknown> => row != null);
  }
  const map = asRecord(value);
  if (!map) return null;
  return Object.entries(map)
    .map(([id, item]) => {
      const row = asRecord(item);
      return row ? ({ id, ...row } as Record<string, unknown>) : null;
    })
    .filter((row): row is Record<string, unknown> => row != null);
}

function scheduleFromConfig(value: unknown): ApiProcess["schedule"] | null {
  const row = asRecord(value);
  if (!row) return null;
  const trigger = asRecord(row.trigger);
  const slotsRaw = Array.isArray(row.slots) ? row.slots : [];
  const slots = slotsRaw
    .map((item, index) => {
      const slot = asRecord(item);
      if (!slot) return null;
      const localTime = stringValue(slot.local_time);
      const ids = Array.isArray(slot.specialists)
        ? slot.specialists
        : Array.isArray(slot.specialist_ids)
          ? slot.specialist_ids
          : [];
      const specialistIds = ids.filter((id): id is string => typeof id === "string" && !!id.trim());
      if (!localTime || specialistIds.length === 0) return null;
      return {
        id: stringValue(slot.id) ?? `slot_${index + 1}`,
        local_time: localTime,
        weekdays: Array.isArray(slot.weekdays)
          ? slot.weekdays.filter((day): day is number => typeof day === "number")
          : [1, 2, 3, 4, 5],
        specialist_ids: specialistIds,
        label: stringValue(slot.label) ?? null,
      };
    })
    .filter((slot): slot is NonNullable<typeof slot> => slot != null);
  return {
    trigger: trigger
      ? {
          kind:
            stringValue(trigger.kind) === "event" || stringValue(trigger.kind) === "manual"
              ? (stringValue(trigger.kind) as "event" | "manual")
              : "schedule",
          event: stringValue(trigger.event) ?? null,
        }
      : { kind: slots.length ? "schedule" : "manual", event: null },
    time_zone: stringValue(row.time_zone) ?? "UTC",
    slots,
  };
}

function layoutFromConfig(value: unknown): ApiProcessState["layout"] | null {
  const row = asRecord(value);
  if (!row) return null;
  const x = numberValue(row.x);
  const y = numberValue(row.y);
  if (x == null || y == null) return null;
  return { x, y };
}

function triggersFromConfig(value: unknown): ApiProcessState["triggers"] | null {
  if (!Array.isArray(value)) return null;
  const triggers = value
    .map((item) => {
      const row = asRecord(item);
      const type = stringValue(row?.type);
      if (!type) return null;
      return {
        type: type as ApiProcessState["triggers"][number]["type"],
        interval: stringValue(row?.interval) ?? null,
        event: stringValue(row?.event) ?? null,
      };
    })
    .filter((trigger): trigger is ApiProcessState["triggers"][number] => trigger != null);
  return triggers.length ? triggers : null;
}

function conditionsFromConfig(
  value: unknown,
): ApiProcessState["exit_conditions"] | null {
  if (!Array.isArray(value)) return null;
  const conditions = value
    .map((item) => {
      const row = asRecord(item);
      const expression = stringValue(row?.expression);
      return expression ? { expression } : null;
    })
    .filter((condition): condition is ApiProcessState["exit_conditions"][number] => condition != null);
  return conditions.length ? conditions : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function booleanValue(value: unknown): boolean | undefined {
  return typeof value === "boolean" ? value : undefined;
}

function requiresHumanFromConfigRow(
  row: Record<string, unknown>,
  fallback: ApiProcessTransition | undefined,
): boolean | undefined {
  const direct = booleanValue(row["requires_human"]);
  if (direct === true) return true;
  if (direct === false) return undefined;
  return fallback?.requires_human ? true : undefined;
}

function agentProfileFromState(state: ApiProcessState | undefined): string {
  const extended = state as ProcessStateWithAgentProfile | undefined;
  return extended?.specialist_agent_profile || "main";
}

function modelFromState(state: ApiProcessState | undefined): string | null {
  const extended = state as ProcessStateWithAgentProfile | undefined;
  const value = extended?.specialist_model;
  return value && value.trim() ? value : null;
}

function mergeProcessRoutines(
  projected: ApiProcessRoutine[],
  yamlRoutines: Record<string, unknown>[],
): ApiProcessRoutine[] {
  const yamlById = new Map(
    yamlRoutines
      .map((item) => {
        const row = asRecord(item);
        const id = row ? stringValue(row.id) : undefined;
        return id && row ? ([id, row] as const) : null;
      })
      .filter(
        (entry): entry is readonly [string, Record<string, unknown>] =>
          entry != null,
      ),
  );
  const seenYaml = new Set<string>();
  const merged = projected.map((routine) => {
    const y = yamlById.get(routine.id);
    if (!y) return routine;
    seenYaml.add(routine.id);
    return applyRoutineYamlOverlay(routine, y);
  });
  for (const [id, y] of yamlById) {
    if (seenYaml.has(id)) continue;
    const created = routineFromYamlOnly(id, y);
    if (created) merged.push(created);
  }
  return merged;
}

function applyRoutineYamlOverlay(
  base: ApiProcessRoutine,
  y: Record<string, unknown>,
): ApiProcessRoutine {
  const name = stringValue(y.name) ?? base.name;
  const trigger = asRecord(y.trigger);
  const cadence = stringValue(y.cadence) ?? stringValue(trigger?.cron);
  const specialist = asRecord(y.specialist);
  const description = stringValue(y.description);
  const prompt =
    stringValue(y.prompt) ?? stringValue(y.instructions) ?? base.prompt;
  const scheduleSpec = parseScheduleFromYaml(y, cadence ?? null);
  return {
    ...base,
    name,
    schedule: cadence ?? base.schedule,
    specialist_id:
      stringValue(specialist?.id) ??
      stringValue(y.specialist_id) ??
      base.specialist_id,
    specialist_name:
      stringValue(specialist?.name) ??
      stringValue(y.specialist_name) ??
      base.specialist_name,
    prompt: prompt || "",
    instructions: prompt || base.instructions,
    description: description ?? base.description,
    schedule_spec: scheduleSpec,
    trigger: trigger ?? base.trigger ?? null,
    scope: asRecord(y.scope) ?? base.scope ?? null,
    output: asRecord(y.output) ?? base.output ?? null,
    prompt_record: promptRecordFromConfig(y.prompt_record) ?? base.prompt_record ?? null,
    enabled: coalesceRoutineEnabled(
      booleanFromYaml(y.enabled),
      base.enabled,
    ),
  };
}

function routineFromYamlOnly(
  id: string,
  y: Record<string, unknown>,
): ApiProcessRoutine | null {
  const name = stringValue(y.name);
  if (!name) return null;
  const specialist = asRecord(y.specialist);
  const trigger = asRecord(y.trigger);
  const cadence = stringValue(y.cadence) ?? stringValue(trigger?.cron) ?? null;
  const prompt =
    stringValue(y.prompt) ?? stringValue(y.instructions) ?? "";
  const spec = parseScheduleFromYaml(y, cadence);
  return {
    id,
    name,
    specialist_id:
      stringValue(specialist?.id) ?? stringValue(y.specialist_id) ?? "devops_platform",
    specialist_name:
      stringValue(specialist?.name) ??
      stringValue(y.specialist_name) ??
      "DevOps/platform",
    schedule: cadence,
    prompt: prompt || "",
    instructions: prompt || undefined,
    last_run: null,
    status: null,
    description: stringValue(y.description) ?? undefined,
    trigger: trigger ?? null,
    scope: asRecord(y.scope),
    output: asRecord(y.output),
    prompt_record: promptRecordFromConfig(y.prompt_record),
    schedule_spec: spec,
    enabled: booleanFromYaml(y.enabled) ?? true,
  };
}

function promptRecordFromConfig(
  value: unknown,
): ApiProcessRoutine["prompt_record"] | null {
  const row = asRecord(value);
  if (!row) return null;
  const id = stringValue(row.id);
  const version = numberValue(row.version);
  const source = stringValue(row.source);
  if (!id || version == null || !source) return null;
  return {
    id,
    version,
    source,
    assumptions: Array.isArray(row.assumptions)
      ? row.assumptions.filter((item): item is string => typeof item === "string")
      : undefined,
  };
}

function booleanFromYaml(value: unknown): boolean | undefined {
  if (value === true) return true;
  if (value === false) return false;
  return undefined;
}

function coalesceRoutineEnabled(
  fromYaml: boolean | undefined,
  base: boolean | undefined,
): boolean | undefined {
  if (fromYaml !== undefined) return fromYaml;
  if (base === false) return false;
  return base;
}

function titleFromId(value: string): string {
  return value.replaceAll("_", " ").replaceAll("-", " ");
}
