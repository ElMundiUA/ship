"use client";

import { useEffect, useMemo, useState } from "react";

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
}: {
  workspaceId: string;
  process: ApiProcess;
  selectedStateId?: string;
  repoId?: string;
  config: ApiRepoConfig | null;
}) {
  const [processName, setProcessName] = useState(process.name);
  const [processPrimary, setProcessPrimary] = useState(process.primary);
  const [states, setStates] = useState(process.states);
  const [transitions, setTransitions] = useState(process.transitions);
  const [activeStateId, setActiveStateId] = useState(
    initialActiveStateId(process.states, selectedStateId),
  );

  useEffect(() => {
    setProcessName(process.name);
    setProcessPrimary(process.primary);
    setStates(process.states);
    setTransitions(process.transitions);
    setActiveStateId(initialActiveStateId(process.states, selectedStateId));
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
  }

  function renameState(currentId: string, nextId: string): boolean {
    const normalized = normalizeStateId(nextId);
    if (!normalized || normalized === currentId) return false;
    if (states.some((state) => state.id === normalized)) return false;

    setStates((current) =>
      current.map((state) =>
        state.id === currentId ? { ...state, id: normalized } : state,
      ),
    );
    setTransitions((current) =>
      current.map((transition, index) => {
        const fromStateId =
          transition.from_state_id === currentId
            ? normalized
            : transition.from_state_id;
        const toStateId =
          transition.to_state_id === currentId ? normalized : transition.to_state_id;
        return {
          ...transition,
          from_state_id: fromStateId,
          to_state_id: toStateId,
          id: transitionId(fromStateId, toStateId, index),
        };
      }),
    );
    setActiveStateId(normalized);
    return true;
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
      instructions: "Describe what should happen in this step.",
      layout: {
        x: (anchor?.layout?.x ?? 72) + 266,
        y: anchor?.layout?.y ?? 170,
      },
      triggers: [{ type: "manual", interval: null, event: null }],
      exit_conditions: [{ expression: "state_complete == true" }],
      block_conditions: [{ expression: "requires_human_input == true" }],
      runtime: {
        task_count: 0,
        blocked_count: 0,
        last_execution_time: null,
        health: "ok",
      },
    };
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
  }

  return (
    <section className="grid min-h-[calc(100vh-180px)] grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="space-y-3">
        <form
          action="/api/process/config-propose"
          method="post"
          className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.035] px-4 py-3"
        >
          <ProcessConfigProposalFields
            workspaceId={workspaceId}
            repoId={repoId}
            config={config}
            processConfig={processConfig}
          />
          <div>
            <div className="text-xs font-bold text-white">Process draft</div>
            <div className="mt-0.5 text-xs text-white/45">
              {dirty
                ? draftSummary
                : "State settings, transitions, and canvas layout save together."}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
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
          </div>
        </form>
        <div className="grid gap-3 rounded-2xl border border-white/10 bg-white/[0.035] p-4 md:grid-cols-[minmax(0,1fr)_220px]">
          <label className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
              Process name
            </span>
            <input
              value={processName}
              onChange={(event) => setProcessName(event.target.value)}
              placeholder="Development Process"
              className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
            />
            <span className="mt-1 block text-xs text-white/40">
              Saved as <code className="font-mono">process.name</code> in the repo
              config.
            </span>
          </label>
          <div className="rounded-xl border border-white/10 bg-black/10 p-3">
            <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
              Process role
            </div>
            <label className="mt-3 flex items-start gap-2 text-sm text-white/75">
              <input
                type="checkbox"
                checked={processPrimary}
                onChange={(event) => setProcessPrimary(event.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-white/20 bg-white/[0.04] accent-aqua"
              />
              <span>
                Primary process
                <span className="mt-1 block text-xs text-white/40">
                  Opens by default when this repo has multiple processes.
                </span>
              </span>
            </label>
          </div>
        </div>
        <ProcessCanvasEditor
          process={processDraft}
          selectedStateId={selectedState?.id}
          onSelectState={setActiveStateId}
          onPositionsChange={updatePositions}
        />
      </div>
      <StateEditor
        repoId={repoId}
        state={selectedState}
        states={states}
        specialistOptions={specialistOptions}
        transitions={transitions}
        config={config}
        onStateChange={updateState}
        onStateRename={renameState}
        onTransitionsChange={setTransitions}
        onAddState={addState}
        onDeleteState={deleteState}
      />
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
    instructions: state.instructions,
    layout: state.layout ?? null,
    triggers: state.triggers,
    exit_conditions: state.exit_conditions,
    block_conditions: state.block_conditions,
  });
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

function normalizeStateId(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_");
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
