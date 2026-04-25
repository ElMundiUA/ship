"use client";

import { useMemo, useState } from "react";

import type {
  ApiProcess,
  ApiProcessState,
  ApiProcessTransition,
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
    states.find((state) => state.id === selectedStateId) ?? states[0];

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
              State settings, transitions, and canvas layout save together.
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
          repoId={repoId}
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
      />
    </section>
  );
}

export function makeTransitionId(
  transition: Pick<ApiProcessTransition, "from_state_id" | "to_state_id">,
  index: number,
) {
  return `${transition.from_state_id}_to_${transition.to_state_id}_${index + 1}`;
}
