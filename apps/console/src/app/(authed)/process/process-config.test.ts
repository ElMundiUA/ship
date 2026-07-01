import { describe, expect, it } from "vitest";

import type { ApiProcess, ApiProcessState, ApiRepoConfig } from "@/lib/api/client";
import { processConfigFromApiProcess, processFromRepoConfig } from "./process-config";

type EditableState = ApiProcessState & { specialist_model?: string | null };

function stateWith(extra: Partial<EditableState> = {}): ApiProcessState {
  return {
    id: "planning",
    name: "Planning",
    specialist_id: "intake",
    specialist_name: "Intake",
    instructions: "",
    state: "planning",
    layout: null,
    triggers: [{ type: "manual", interval: null, event: null }],
    exit_conditions: [],
    block_conditions: [],
    runtime: {
      task_count: 0,
      blocked_count: 0,
      last_execution_time: null,
      health: "ok",
    },
    ...extra,
  } as ApiProcessState;
}

function processWith(states: ApiProcessState[]): ApiProcess {
  return {
    id: "development",
    name: "Development",
    primary: true,
    state_count: states.length,
    task_count: 0,
    blocked_count: 0,
    health: "ok",
    description: "",
    specialists: [],
    states,
    transitions: [],
    tasks: [],
    routines: [],
    process_graph: { nodes: [], links: [] },
    adapter_diagnostics: [],
  } as unknown as ApiProcess;
}

describe("per-stage model round-trips through .ship/config.yml", () => {
  it("serialises a chosen model under specialist.model", () => {
    const proc = processWith([stateWith({ specialist_model: "claude-sonnet-4-6" })]);
    const config = processConfigFromApiProcess(proc) as {
      states: { specialist: { model?: string } }[];
    };
    expect(config.states[0].specialist.model).toBe("claude-sonnet-4-6");
  });

  it("omits model when unset so the provider default is used", () => {
    const proc = processWith([stateWith()]);
    const config = processConfigFromApiProcess(proc) as {
      states: { specialist: { model?: string } }[];
    };
    expect(config.states[0].specialist.model).toBeUndefined();
  });

  it("parses specialist.model back from a saved config", () => {
    const serialized = processConfigFromApiProcess(
      processWith([stateWith({ specialist_model: "gpt-5-codex" })]),
    );
    const config = {
      exists: true,
      parsed: { process: serialized },
    } as unknown as ApiRepoConfig;

    const parsed = processFromRepoConfig(config, processWith([stateWith()]));
    expect(parsed).not.toBeNull();
    const state0 = parsed!.states[0] as EditableState;
    expect(state0.specialist_model).toBe("gpt-5-codex");
  });
});
