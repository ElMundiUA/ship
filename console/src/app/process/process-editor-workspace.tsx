"use client";

import { type ReactNode, useEffect, useMemo, useState } from "react";

import type {
  ApiProcess,
  ApiProcessSpecialist,
  ApiProcessState,
  ApiRepoConfig,
} from "@/lib/api/client";
import { ProcessCanvasEditor, type Position } from "./process-canvas-editor";
import { processConfigFromApiProcess } from "./process-config";
import { ProcessConfigProposalFields } from "./process-config-proposal-fields";
import { BASE_SPECIALIST_CATALOG } from "./specialist-catalog";
import { StateEditor, type SpecialistOption } from "./state-editor";

export function ProcessEditorWorkspace({
  workspaceId,
  process,
  selectedStateId,
  repoId,
  config,
  tabs,
}: {
  workspaceId: string;
  process: ApiProcess;
  selectedStateId?: string;
  repoId?: string;
  config: ApiRepoConfig | null;
  tabs?: ReactNode;
}) {
  const [processName, setProcessName] = useState(process.name);
  const [processPrimary, setProcessPrimary] = useState(process.primary);
  const [states, setStates] = useState(process.states);
  const [transitions, setTransitions] = useState(process.transitions);
  const [activeStateId, setActiveStateId] = useState(
    initialActiveStateId(process.states, selectedStateId),
  );
  const [selectedTransitionId, setSelectedTransitionId] = useState<string | null>(
    null,
  );

  useEffect(() => {
    setProcessName(process.name);
    setProcessPrimary(process.primary);
    setStates(process.states);
    setTransitions(process.transitions);
    setActiveStateId(initialActiveStateId(process.states, selectedStateId));
    setSelectedTransitionId(null);
  }, [process, selectedStateId]);

  const processDraft = useMemo<ApiProcess>(
    () => ({
      ...process,
      name: processName.trim() || process.name,
      primary: processPrimary,
      state_count: states.length,
      states,
      transitions,
    }),
    [process, processName, processPrimary, states, transitions],
  );
  const processConfig = useMemo(
    () => processConfigFromApiProcess(processDraft),
    [processDraft],
  );
  const initialProcessConfig = useMemo(
    () => processConfigFromApiProcess(process),
    [process],
  );
  const dirty =
    JSON.stringify(processConfig) !== JSON.stringify(initialProcessConfig);
  const draftSummary = useMemo(
    () => summarizeDraftChanges(process, processDraft, states, transitions),
    [process, processDraft, states, transitions],
  );
  const selectedState =
    states.find((state) => state.id === activeStateId) ?? states[0];
  const specialistOptions = useMemo(
    () => buildSpecialistOptions(process.specialists, states),
    [process.specialists, states],
  );

  function updateState(nextState: ApiProcessState) {
    setStates((current) =>
      current.map((state) => (state.id === nextState.id ? nextState : state)),
    );
  }

  function resetDraft() {
    setProcessName(process.name);
    setProcessPrimary(process.primary);
    setStates(process.states);
    setTransitions(process.transitions);
    setActiveStateId(initialActiveStateId(process.states, selectedStateId));
    setSelectedTransitionId(null);
  }

  function selectStateId(stateId: string) {
    setActiveStateId(stateId);
    setSelectedTransitionId(null);
  }

  function selectTransitionId(transitionId: string) {
    const t = transitions.find((row) => row.id === transitionId);
    setSelectedTransitionId(transitionId);
    if (t) setActiveStateId(t.from_state_id);
  }

  function updatePositions(positions: Record<string, Position>) {
    setStates((current) => {
      let changed = false;
      const next = current.map((state) => {
        const position = positions[state.id];
        if (!position) return state;
        const layout = {
          x: Math.round(position.x),
          y: Math.round(position.y),
        };
        if (state.layout?.x === layout.x && state.layout?.y === layout.y) {
          return state;
        }
        changed = true;
        return { ...state, layout };
      });
      return changed ? next : current;
    });
  }

  function addState() {
    const baseId = "new_state";
    const nextId = uniqueStateId(baseId, states);
    const selectedIndex = Math.max(
      states.findIndex((state) => state.id === selectedState?.id),
      0,
    );
    const anchor = selectedState ?? states[states.length - 1];
    const defaultSpecialist = specialistOptions[0] ?? {
      id: "owner",
      name: "Owner",
      role: "Responsible owner for this state.",
    };
    const nextState: ApiProcessState = {
      id: nextId,
      name: "New State",
      specialist_id: defaultSpecialist.id,
      specialist_name: defaultSpecialist.name,
      specialist_agent_profile: "main",
      instructions: defaultSpecialist.role,
      layout: {
        x: (anchor?.layout?.x ?? 72) + 266,
        y: anchor?.layout?.y ?? 170,
      },
      triggers: defaultSdlcStateTriggers(),
      exit_conditions: [{ expression: "state_complete == true" }],
      block_conditions: [{ expression: "requires_human_input == true" }],
      runtime: {
        task_count: 0,
        blocked_count: 0,
        last_execution_time: null,
        health: "ok",
      },
    } as ApiProcessState;
    setStates((current) => [
      ...current.slice(0, selectedIndex + 1),
      nextState,
      ...current.slice(selectedIndex + 1),
    ]);
    if (selectedState) {
      const nextTransition = {
        id: transitionId(selectedState.id, nextState.id, transitions.length),
        from_state_id: selectedState.id,
        to_state_id: nextState.id,
        conditions: [{ expression: "exit_conditions_met == true" }],
      };
      setTransitions((current) => [...current, nextTransition]);
    }
    setActiveStateId(nextState.id);
    setSelectedTransitionId(null);
  }

  function deleteState(stateId: string) {
    if (states.length <= 1) return;
    const index = states.findIndex((state) => state.id === stateId);
    const fallback =
      states[index + 1]?.id ?? states[index - 1]?.id ?? states[0]?.id;
    setStates((current) => current.filter((state) => state.id !== stateId));
    setTransitions((current) =>
      current.filter(
        (transition) =>
          transition.from_state_id !== stateId && transition.to_state_id !== stateId,
      ),
    );
    setActiveStateId(fallback);
    setSelectedTransitionId(null);
  }

  return (
    <section className="min-h-[calc(100vh-180px)] overflow-hidden rounded-2xl border border-white/10 bg-white/[0.035] shadow-card backdrop-blur-xl">
      <div className="border-b border-white/10 px-4 py-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            {tabs}
            <div className={tabs ? "mt-3" : undefined}>
              <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
                <input
                  type="text"
                  value={processName}
                  onChange={(event) => setProcessName(event.target.value)}
                  placeholder={process.name}
                  aria-label="Process name"
                  className="min-w-0 flex-1 border-b border-transparent bg-transparent font-display text-base font-bold text-white outline-none transition placeholder:text-white/35 hover:border-white/15 focus:border-aqua/40"
                />
                <label className="flex shrink-0 cursor-pointer items-center gap-1.5 text-[11px] font-medium text-white/45">
                  <input
                    type="checkbox"
                    checked={processPrimary}
                    onChange={(event) =>
                      setProcessPrimary(event.target.checked)
                    }
                    className="h-3 w-3 rounded border-white/25 bg-white/[0.04] accent-aqua"
                  />
                  <span title="Opens by default for this repo.">Default</span>
                </label>
              </div>
              <p className="mt-1 text-xs text-white/45">
                {dirty
                  ? draftSummary
                  : "Drag cards, edit process settings, and save the config PR from one place."}
              </p>
            </div>
          </div>
          <form
            action="/api/process/config-propose"
            method="post"
            className="flex flex-wrap items-center gap-2"
          >
            <ProcessConfigProposalFields
              workspaceId={workspaceId}
              repoId={repoId}
              config={config}
              processConfig={processConfig}
            />
            <button
              type="button"
              disabled={!dirty}
              onClick={resetDraft}
              className="rounded-full border border-white/10 bg-white/[0.04] px-4 py-2 text-xs font-bold text-white/60 transition hover:border-white/20 hover:text-white disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.03] disabled:text-white/30"
            >
              Discard changes
            </button>
            <button
              type="submit"
              disabled={!repoId || !dirty}
              className="rounded-full border border-aqua/30 bg-aqua/10 px-4 py-2 text-xs font-bold text-aqua transition hover:bg-aqua/15 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.05] disabled:text-white/35"
            >
              Open config PR
            </button>
          </form>
        </div>
      </div>

      <div className="grid min-h-0 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_360px]">
        <ProcessCanvasEditor
          process={processDraft}
          selectedStateId={selectedState?.id}
          selectedTransitionId={selectedTransitionId}
          onSelectState={selectStateId}
          onSelectTransition={selectTransitionId}
          onAddState={addState}
          onPositionsChange={updatePositions}
        />
        <StateEditor
          repoId={repoId}
          state={selectedState}
          states={states}
          specialistOptions={specialistOptions}
          config={config}
          embedded
          onStateChange={updateState}
          onDeleteState={deleteState}
        />
      </div>
    </section>
  );
}

function buildSpecialistOptions(
  processSpecialists: ApiProcessSpecialist[],
  states: ApiProcessState[],
): SpecialistOption[] {
  const options = new Map<string, SpecialistOption>();
  for (const specialist of BASE_SPECIALIST_CATALOG) {
    options.set(specialist.id, {
      ...specialist,
      source: "catalog",
    });
  }
  for (const specialist of processSpecialists) {
    options.set(specialist.id, {
      id: specialist.id,
      name: specialist.name,
      role: specialist.role,
      source: "process",
    });
  }
  for (const state of states) {
    if (!options.has(state.specialist_id)) {
      options.set(state.specialist_id, {
        id: state.specialist_id,
        name: state.specialist_name,
        role: "Custom role from this process config.",
        source: "custom",
      });
    }
  }
  if (options.size === 0) {
    options.set("owner", {
      id: "owner",
      name: "Owner",
      role: "Responsible owner for this state.",
      source: "custom",
    });
  }
  return Array.from(options.values());
}

function initialActiveStateId(
  states: ApiProcessState[],
  selectedStateId?: string,
) {
  return states.some((state) => state.id === selectedStateId)
    ? selectedStateId
    : states[0]?.id;
}

function summarizeDraftChanges(
  initialProcess: ApiProcess,
  draftProcess: ApiProcess,
  states: ApiProcessState[],
  transitions: ApiProcess["transitions"],
) {
  const initialStateIds = new Set(initialProcess.states.map((state) => state.id));
  const currentStateIds = new Set(states.map((state) => state.id));
  const addedStates = states.filter((state) => !initialStateIds.has(state.id)).length;
  const removedStates = initialProcess.states.filter(
    (state) => !currentStateIds.has(state.id),
  ).length;

  const initialStateById = new Map(
    initialProcess.states.map((state) => [state.id, stateFingerprint(state)]),
  );
  const changedStates = states.filter((state) => {
    const initial = initialStateById.get(state.id);
    return initial != null && initial !== stateFingerprint(state);
  }).length;

  const transitionsChanged =
    transitionFingerprint(initialProcess.transitions) !==
    transitionFingerprint(transitions);
  const processSettingsChanged =
    initialProcess.name !== draftProcess.name ||
    initialProcess.primary !== draftProcess.primary;

  const parts = [
    processSettingsChanged ? "process settings changed" : null,
    addedStates ? pluralize(addedStates, "state added", "states added") : null,
    removedStates
      ? pluralize(removedStates, "state removed", "states removed")
      : null,
    changedStates
      ? pluralize(changedStates, "state changed", "states changed")
      : null,
    transitionsChanged ? "transitions changed" : null,
  ].filter(Boolean);

  return parts.length > 0
    ? `Unsaved: ${parts.join(", ")}.`
    : "Unsaved changes will be proposed together.";
}

function stateFingerprint(state: ApiProcessState) {
  return JSON.stringify({
    id: state.id,
    name: state.name,
    specialist_id: state.specialist_id,
    specialist_name: state.specialist_name,
    specialist_agent_profile: agentProfileFromState(state),
    instructions: state.instructions,
    layout: state.layout ?? null,
    triggers: state.triggers,
    exit_conditions: state.exit_conditions,
    block_conditions: state.block_conditions,
  });
}

function defaultSdlcStateTriggers(): ApiProcessState["triggers"] {
  return [
    {
      type: "schedule",
      interval: "0 9,13,17 * * 1-5",
      event: null,
    },
  ];
}

function agentProfileFromState(state: ApiProcessState) {
  return (
    (state as ApiProcessState & { specialist_agent_profile?: string | null })
      .specialist_agent_profile || "main"
  );
}

function transitionFingerprint(transitions: ApiProcess["transitions"]) {
  return JSON.stringify(
    transitions.map((transition) => ({
      from_state_id: transition.from_state_id,
      to_state_id: transition.to_state_id,
      conditions: transition.conditions,
    })),
  );
}

function pluralize(count: number, singular: string, plural: string) {
  return `${count} ${count === 1 ? singular : plural}`;
}

function uniqueStateId(baseId: string, states: ApiProcessState[]) {
  const existing = new Set(states.map((state) => state.id));
  if (!existing.has(baseId)) return baseId;
  for (let index = 2; index < 1000; index += 1) {
    const candidate = `${baseId}_${index}`;
    if (!existing.has(candidate)) return candidate;
  }
  return `${baseId}_${Date.now()}`;
}

function transitionId(fromStateId: string, toStateId: string, index: number) {
  return `${fromStateId}_to_${toStateId}_${index + 1}`;
}
