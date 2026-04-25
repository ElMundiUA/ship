"use client";

import { useEffect, useMemo, useState } from "react";

import type {
  ApiProcess,
  ApiProcessState,
  ApiRepoConfig,
} from "@/lib/api/client";
import { ProcessCanvasEditor, type Position } from "./process-canvas-editor";
import { processConfigFromApiProcess } from "./process-config";
import { ProcessConfigProposalFields } from "./process-config-proposal-fields";
import { StateEditor } from "./state-editor";

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
  const [states, setStates] = useState(process.states);
  const [transitions, setTransitions] = useState(process.transitions);
  const [activeStateId, setActiveStateId] = useState(
    selectedStateId ?? process.states[0]?.id,
  );

  useEffect(() => {
    setStates(process.states);
    setTransitions(process.transitions);
    setActiveStateId(selectedStateId ?? process.states[0]?.id);
  }, [process, selectedStateId]);

  const processDraft = useMemo<ApiProcess>(
    () => ({
      ...process,
      state_count: states.length,
      states,
      transitions,
    }),
    [process, states, transitions],
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
  const selectedState =
    states.find((state) => state.id === activeStateId) ?? states[0];

  function updateState(nextState: ApiProcessState) {
    setStates((current) =>
      current.map((state) => (state.id === nextState.id ? nextState : state)),
    );
  }

  function updatePositions(positions: Record<string, Position>) {
    setStates((current) =>
      current.map((state) => {
        const position = positions[state.id];
        return position
          ? {
              ...state,
              layout: {
                x: Math.round(position.x),
                y: Math.round(position.y),
              },
            }
          : state;
      }),
    );
  }

  function addState() {
    const baseId = "new_state";
    const nextId = uniqueStateId(baseId, states);
    const selectedIndex = Math.max(
      states.findIndex((state) => state.id === selectedState?.id),
      0,
    );
    const anchor = selectedState ?? states[states.length - 1];
    const nextState: ApiProcessState = {
      id: nextId,
      name: "New State",
      specialist_id: "owner",
      specialist_name: "Owner",
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
                ? "Unsaved changes will be proposed together."
                : "State settings, transitions, and canvas layout save together."}
            </div>
          </div>
          <button
            type="submit"
            disabled={!repoId || !dirty}
            className="rounded-full border border-aqua/30 bg-aqua/10 px-4 py-2 text-xs font-bold text-aqua transition hover:bg-aqua/15 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.05] disabled:text-white/35"
          >
            Open config PR
          </button>
        </form>
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
        transitions={transitions}
        config={config}
        onStateChange={updateState}
        onTransitionsChange={setTransitions}
        onAddState={addState}
        onDeleteState={deleteState}
      />
    </section>
  );
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
