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
import { ProcessReviewSummary, processChangeSummary } from "./process-review-summary";
import { ProcessValidationPanel } from "./process-validation-panel";
import { validateProcess } from "./process-validation";
import { BASE_SPECIALIST_CATALOG } from "./specialist-catalog";
import { StateEditor, type SpecialistOption } from "./state-editor";
import { TrackerProjectionsTable } from "./tracker-projections-table";

export function ProcessEditorWorkspace({
  workspaceId,
  process,
  selectedStateId,
  repoId,
  config,
  tabs,
  trackerKind,
}: {
  workspaceId: string;
  process: ApiProcess;
  selectedStateId?: string;
  repoId?: string;
  config: ApiRepoConfig | null;
  tabs?: ReactNode;
  /** Workspace's bound tracker kind (linear/jira/github/notion). Picks
   *  which column the projection table opens on by default. */
  trackerKind?: "linear" | "jira" | "github" | "notion";
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
  const changeSummary = useMemo(
    () => processChangeSummary(process, processDraft, dirty ? ["Flow reviewed"] : []),
    [process, processDraft, dirty],
  );
  const draftSummary = useMemo(
    () => summarizeDraftChanges(process, processDraft, states, transitions),
    [process, processDraft, states, transitions],
  );
  const validation = useMemo(
    () => validateProcess({ stages: states, transitions }),
    [states, transitions],
  );
  const hasErrors = validation.errors.length > 0;
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

  // Fired when the operator drags a stage card across a swim-lane
  // boundary. The canvas snaps the position to the new lane; we mirror
  // that by setting the stage's canonical state field, which is what
  // the publish flow writes back to .ship/config.yml.
  function updateStageState(
    stageId: string,
    nextState: ApiProcessState["state"],
  ) {
    setStates((current) =>
      current.map((stage) =>
        stage.id === stageId && stage.state !== nextState
          ? { ...stage, state: nextState }
          : stage,
      ),
    );
  }

  function addState(template: NodeTemplate = NODE_TEMPLATES[0]) {
    const baseId = template.baseId;
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
    const templateSpecialist =
      specialistOptions.find((option) => option.id === template.specialistId) ??
      defaultSpecialist;
    const nextState: ApiProcessState = {
      id: nextId,
      name: template.name,
      specialist_id: templateSpecialist.id,
      specialist_name: templateSpecialist.name,
      specialist_agent_profile: "main",
      instructions: templateSpecialist.role,
      // New stages default to the planning bucket; operator can drag
      // them to the right swim-lane on the canvas to change.
      state: "planning",
      layout: {
        x: (anchor?.layout?.x ?? 72) + 266,
        y: anchor?.layout?.y ?? 170,
      },
      triggers: defaultSdlcStateTriggers(),
      exit_conditions: [],
      block_conditions: [],
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
    <section className="min-h-[calc(100vh-180px)] overflow-hidden rounded-[2rem] border border-aqua/15 bg-[radial-gradient(circle_at_top_left,rgba(99,245,255,0.10),transparent_30%),linear-gradient(135deg,rgba(255,255,255,0.07),rgba(255,255,255,0.025))] shadow-2xl shadow-black/40 backdrop-blur-xl">
      <div className="border-b border-white/10 px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            {tabs}
            <div className={tabs ? "mt-4" : undefined}>
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="rounded-full border border-aqua/20 bg-aqua/[0.08] px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.22em] text-aqua/80">
                  Process editor
                </span>
                <span className="rounded-full border border-white/10 bg-black/20 px-2.5 py-1 text-[10px] font-semibold text-white/45">
                  {states.length} nodes · {transitions.length || Math.max(0, states.length - 1)} handoffs
                </span>
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
                <input
                  type="text"
                  value={processName}
                  onChange={(event) => setProcessName(event.target.value)}
                  placeholder={process.name}
                  aria-label="Process name"
                  className="min-w-0 flex-1 border-b border-transparent bg-transparent font-display text-2xl font-bold text-white outline-none transition placeholder:text-white/35 hover:border-white/15 focus:border-aqua/40"
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
                  : "Drag the flow, tune the selected node, then publish a reviewable process change."}
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
              changeSummary={changeSummary}
            />
            <button
              type="button"
              disabled={!dirty}
              onClick={resetDraft}
              className="rounded-full border border-white/10 bg-black/25 px-4 py-2 text-xs font-bold text-white/65 transition hover:border-white/20 hover:text-white disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.03] disabled:text-white/30"
            >
              Discard changes
            </button>
            <button
              type="submit"
              disabled={!repoId || !dirty || hasErrors}
              title={
                hasErrors
                  ? `Fix ${validation.errors.length} validation error${validation.errors.length === 1 ? "" : "s"} below before publishing.`
                  : undefined
              }
              className="rounded-full border border-aqua/35 bg-aqua/15 px-4 py-2 text-xs font-bold text-aqua shadow-lg shadow-aqua/5 transition hover:bg-aqua/20 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.05] disabled:text-white/35"
            >
              Publish process changes
            </button>
          </form>
        </div>
        <div className="mt-3">
          <ProcessReviewSummary
            initial={process}
            draft={processDraft}
            changedAreas={dirty ? ["Flow reviewed"] : []}
          />
        </div>
      </div>

      <div className="grid min-h-0 grid-cols-1 xl:grid-cols-[230px_minmax(0,1fr)_390px]">
        <NodePalette onAdd={addState} />
        <ProcessCanvasEditor
          process={processDraft}
          selectedStateId={selectedState?.id}
          selectedTransitionId={selectedTransitionId}
          onSelectState={selectStateId}
          onSelectTransition={selectTransitionId}
          onAddState={() => addState()}
          onPositionsChange={updatePositions}
          onStageStateChange={updateStageState}
        />
        <StateEditor
          processId={process.id}
          repoId={repoId}
          state={selectedState}
          states={states}
          schedule={processDraft.schedule ?? null}
          transitions={transitions}
          specialistOptions={specialistOptions}
          config={config}
          embedded
          onStateChange={updateState}
          onDeleteState={deleteState}
        />
      </div>

      {/* Tracker projection table — replaces the dedicated Tracker tab.
          Each row is one of the seven canonical lifecycle states; columns
          tab between the four supported trackers. Defaults from the
          adapter ship "all-mapped" out of the box. */}
      <div className="space-y-3 border-t border-white/10 bg-black/20 p-4">
        <TrackerProjectionsTable
          trackerMapping={processDraft.tracker_mapping ?? {}}
          defaultTracker={trackerKind}
        />
        {/* Validation panel — gates the Publish button. Errors block,
            warnings inform. Anchors let the operator jump to the
            offending stage / transition. */}
        <ProcessValidationPanel
          result={validation}
          onJumpToStage={(stageId) => selectStateId(stageId)}
          onJumpToTransition={(transitionId) =>
            selectTransitionId(transitionId)
          }
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

type NodeTemplate = {
  id: string;
  name: string;
  description: string;
  baseId: string;
  specialistId: string;
};

const NODE_TEMPLATES: NodeTemplate[] = [
  {
    id: "specialist_work",
    name: "Specialist work",
    description: "A ticket-driven process step owned by a role template.",
    baseId: "specialist_work",
    specialistId: "business_analyst",
  },
  {
    id: "human_approval",
    name: "Human approval",
    description: "Pause the handoff until a person approves in Ship Inbox.",
    baseId: "human_approval",
    specialistId: "product_manager",
  },
  {
    id: "clarification",
    name: "Clarification",
    description: "Ask for missing information and return to the flow.",
    baseId: "clarification",
    specialistId: "intake",
  },
  {
    id: "quality_review",
    name: "Quality review",
    description: "Validate acceptance criteria before release.",
    baseId: "quality_review",
    specialistId: "qa_engineer",
  },
];

function NodePalette({ onAdd }: { onAdd: (template: NodeTemplate) => void }) {
  const primaryTemplate = NODE_TEMPLATES[0] as NodeTemplate;
  return (
    <aside className="border-b border-white/10 bg-[radial-gradient(circle_at_top,rgba(99,245,255,0.10),transparent_34%),rgba(0,0,0,0.22)] p-4 xl:border-b-0 xl:border-r">
      <button
        type="button"
        onClick={() => onAdd(primaryTemplate)}
        className="group relative w-full overflow-hidden rounded-[1.6rem] border border-aqua/25 bg-[linear-gradient(135deg,rgba(99,245,255,0.18),rgba(168,85,247,0.10),rgba(255,255,255,0.04))] p-4 text-left shadow-2xl shadow-aqua/5 transition hover:-translate-y-1 hover:border-aqua/45"
      >
        <div className="absolute -right-8 -top-8 h-24 w-24 rounded-full bg-aqua/20 blur-2xl transition group-hover:bg-aqua/30" />
        <div className="relative text-[10px] font-bold uppercase tracking-[0.26em] text-aqua/75">
          Create subprocess
        </div>
        <div className="relative mt-3 font-display text-lg font-bold text-white">
          New work block
        </div>
        <p className="relative mt-2 text-xs leading-relaxed text-white/55">
          Adds a draggable process block to this development canvas.
        </p>
        <div className="relative mt-4 inline-flex rounded-full border border-aqua/30 bg-aqua/10 px-3 py-1 text-xs font-semibold text-aqua">
          Add to canvas
        </div>
      </button>

      <div className="mt-4">
        <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-white/35">
          Building blocks
        </div>
        <div className="grid gap-2">
        {NODE_TEMPLATES.map((template) => (
          <button
            key={template.id}
            type="button"
            onClick={() => onAdd(template)}
            className="group w-full rounded-2xl border border-white/10 bg-black/25 px-3 py-2.5 text-left transition hover:border-aqua/25 hover:bg-aqua/[0.055]"
          >
            <div className="flex items-center justify-between gap-2">
              <div className="text-xs font-semibold text-white/85">{template.name}</div>
              <span className="grid h-5 w-5 place-items-center rounded-full border border-white/10 text-aqua/50 transition group-hover:border-aqua/30 group-hover:text-aqua">
                +
              </span>
            </div>
            <div className="mt-1 text-[11px] leading-relaxed text-white/40">
              {template.description}
            </div>
          </button>
        ))}
        </div>
      </div>
    </aside>
  );
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
    state: state.state,
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
