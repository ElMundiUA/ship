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

  return {
    ...fallback,
    name: stringValue(rawProcess.name) ?? fallback.name,
    state_count: states.length,
    states,
    transitions: states.slice(0, -1).map((state, index) => ({
      id: `${state.id}_to_${states[index + 1].id}`,
      from_state_id: state.id,
      to_state_id: states[index + 1].id,
      conditions: [{ expression: "exit_conditions_met == true" }],
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
    triggers: fallback?.triggers ?? [{ type: "manual", interval: null, event: null }],
    exit_conditions:
      fallback?.exit_conditions ?? [{ expression: "state_complete == true" }],
    block_conditions:
      fallback?.block_conditions ?? [{ expression: "requires_human_input == true" }],
    runtime: fallback?.runtime ?? {
      task_count: 0,
      blocked_count: 0,
      last_execution_time: null,
      health: "ok",
    },
  };
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function titleFromId(value: string): string {
  return value.replaceAll("_", " ").replaceAll("-", " ");
}
