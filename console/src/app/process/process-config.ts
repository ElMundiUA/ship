import type {
  ApiProcess,
  ApiProcessState,
  ApiRepoConfig,
} from "@/lib/api/client";

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
          const from = stringValue(row?.from);
          const to = stringValue(row?.to);
          if (!from || !to) return null;
          const fallbackTransition = transitionByPair.get(`${from}->${to}`);
          return {
            id: fallbackTransition?.id ?? `${from}_to_${to}_${index + 1}`,
            from_state_id: from,
            to_state_id: to,
            conditions: stringValue(row?.condition)
              ? [{ expression: stringValue(row?.condition) as string }]
              : fallbackTransition?.conditions ?? [],
          };
        })
        .filter((transition): transition is ApiProcess["transitions"][number] => transition != null)
    : states.slice(0, -1).map((state, index) => ({
        id: `${state.id}_to_${states[index + 1].id}`,
        from_state_id: state.id,
        to_state_id: states[index + 1].id,
        conditions: [{ expression: "exit_conditions_met == true" }],
      }));

  return {
    ...fallback,
    id: stringValue(rawProcess.id) ?? fallback.id,
    name: stringValue(rawProcess.name) ?? fallback.name,
    state_count: states.length,
    states,
    transitions: transitionsFromConfig,
  };
}

export function processConfigFromApiProcess(process: ApiProcess): Record<string, unknown> {
  return {
    id: process.id,
    name: process.name,
    primary: process.id === "development",
    states: process.states.map((state) => ({
      id: state.id,
      name: state.name,
      specialist: {
        id: state.specialist_id,
        name: state.specialist_name,
      },
      instructions: state.instructions,
      ...(state.layout ? { layout: state.layout } : {}),
      triggers: state.triggers,
      exit_conditions: state.exit_conditions,
      block_conditions: state.block_conditions,
    })),
    transitions: process.transitions.map((transition) => ({
      from: transition.from_state_id,
      to: transition.to_state_id,
      condition: transition.conditions[0]?.expression,
    })),
    routines: process.routines.map((routine) => ({
      id: routine.id,
      name: routine.name,
      cadence: routine.schedule,
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
      [{ expression: "state_complete == true" }],
    block_conditions:
      conditionsFromConfig(row.block_conditions) ??
      fallback?.block_conditions ??
      [{ expression: "requires_human_input == true" }],
    runtime: fallback?.runtime ?? {
      task_count: 0,
      blocked_count: 0,
      last_execution_time: null,
      health: "ok",
    },
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

function titleFromId(value: string): string {
  return value.replaceAll("_", " ").replaceAll("-", " ");
}
