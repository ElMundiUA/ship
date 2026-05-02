"use client";

import { useCallback, useMemo, useState, type DragEvent as ReactDragEvent } from "react";

import type { ApiProcess, ApiProcessState } from "@/lib/api/client";
import {
  CANONICAL_STATES,
  type CanonicalState,
} from "@/lib/api/types";

// Re-exported so existing import sites keep working.
export type Position = { x: number; y: number };

const DRAG_STAGE_MIME = "application/x-ship-stage";

const LANE_LABEL: Record<CanonicalState, string> = {
  backlog: "Backlog",
  planning: "Planning",
  executing: "Executing",
  reviewing: "Reviewing",
  awaiting_input: "Awaiting input",
  blocked: "Blocked",
  closed: "Closed",
};

const LANE_HINT: Record<CanonicalState, string> = {
  backlog: "Untouched · agents ignore",
  planning: "Intake / BA / architects scope",
  executing: "Dev / QA cycles",
  reviewing: "Awaiting human approval",
  awaiting_input: "Frozen on clarification",
  blocked: "Frozen on external blocker",
  closed: "Terminal",
};

const LANE_TINT: Record<CanonicalState, string> = {
  backlog: "rgba(255,255,255,0.04)",
  planning: "rgba(99, 200, 255, 0.09)",
  executing: "rgba(207, 169, 107, 0.10)",
  reviewing: "rgba(168, 85, 247, 0.10)",
  awaiting_input: "rgba(255, 196, 87, 0.09)",
  blocked: "rgba(244, 114, 114, 0.09)",
  closed: "rgba(120, 200, 140, 0.09)",
};

const LANE_DOT: Record<CanonicalState, string> = {
  backlog: "rgba(255,255,255,0.5)",
  planning: "rgba(99, 200, 255, 0.85)",
  executing: "rgba(207, 169, 107, 0.95)",
  reviewing: "rgba(168, 85, 247, 0.85)",
  awaiting_input: "rgba(255, 196, 87, 0.85)",
  blocked: "rgba(244, 114, 114, 0.85)",
  closed: "rgba(120, 200, 140, 0.85)",
};

// Linear flow lanes (the "happy path" through the FSM). Overlay
// lanes (awaiting_input, blocked) hold stages that can be entered
// from any active lane via labels — they don't sit in the sequence.
const FLOW_LANES: CanonicalState[] = [
  "backlog",
  "planning",
  "executing",
  "reviewing",
  "closed",
];

// Cross-bucket transitions where the operator is the trigger. Mirrors
// backend ``_USER_TRANSITION_PAIRS`` so the canvas can paint user-only
// hand-offs differently from agent-driven ones without an extra round-
// trip.
const USER_TRANSITION_PAIRS: ReadonlySet<string> = new Set(
  [
    ["backlog", "planning"],
    ["reviewing", "closed"],
    ["reviewing", "planning"],
    ["reviewing", "executing"],
    ["awaiting_input", "planning"],
    ["awaiting_input", "executing"],
    ["awaiting_input", "reviewing"],
    ["blocked", "planning"],
    ["blocked", "executing"],
    ["blocked", "reviewing"],
  ].map(([from, to]) => `${from}→${to}`),
);

function transitionActor(
  fromLane: CanonicalState,
  toLane: CanonicalState,
): "user" | "agent" {
  if (fromLane === toLane) return "agent";
  return USER_TRANSITION_PAIRS.has(`${fromLane}→${toLane}`) ? "user" : "agent";
}

/**
 * Process FSM editor — CSS Grid swim-lanes with drag-drop reorder.
 *
 * No arrows. Sequence is encoded by stage order:
 *
 *   - Within a lane: stage[N] → stage[N+1] is an agent transition.
 *   - Last stage of a lane → first stage of the next non-empty lane in
 *     the canonical flow (backlog → planning → executing → reviewing →
 *     closed); the actor depends on the bucket pair (Backlog → Planning
 *     is user-only because the operator drags the ticket; Reviewing →
 *     Closed is user-only because the human approves; everything else
 *     is agent).
 *
 * Drag a stage onto another stage in the same lane → reorder.
 * Drag onto a different lane's empty area → change canonical state.
 * The ``next: <stage>`` hint at the bottom of each card makes the
 * implicit transition visible without drawing edges.
 */
export function ProcessCanvasEditor({
  process,
  selectedStateId,
  onSelectState,
  onAddState,
  onPositionsChange: _onPositionsChange,
  onStageStateChange,
  onReorderStages,
}: {
  process: ApiProcess;
  selectedStateId?: string;
  selectedTransitionId?: string | null;
  onSelectState: (stateId: string) => void;
  onSelectTransition?: (transitionId: string) => void;
  onAddState: () => void;
  /** Kept for backward compatibility — unused by the grid editor. */
  onPositionsChange?: (positions: Record<string, Position>) => void;
  onStageStateChange?: (stageId: string, nextState: CanonicalState) => void;
  /** Move ``stageId`` to land just before ``targetStageId`` (or at the
   *  end of ``targetLane`` if ``targetStageId`` is null). The editor
   *  workspace mirrors this into a process.states array splice. */
  onReorderStages?: (
    stageId: string,
    targetLane: CanonicalState,
    targetStageId: string | null,
  ) => void;
}) {
  const stagesByLane = useMemo(() => {
    const map = new Map<CanonicalState, ApiProcessState[]>();
    for (const lane of CANONICAL_STATES) map.set(lane, []);
    for (const stage of process.states) {
      const lane = (CANONICAL_STATES as readonly string[]).includes(stage.state)
        ? (stage.state as CanonicalState)
        : "planning";
      map.get(lane)!.push(stage);
    }
    return map;
  }, [process.states]);

  // Compute "next stage" for each stage so the card can render the
  // implicit transition hint without rebuilding the chain on every render.
  const nextByStageId = useMemo(() => {
    const map = new Map<string, { stage: ApiProcessState; actor: "user" | "agent" }>();
    for (let laneIdx = 0; laneIdx < FLOW_LANES.length; laneIdx += 1) {
      const lane = FLOW_LANES[laneIdx];
      const stagesInLane = stagesByLane.get(lane) ?? [];
      for (let i = 0; i < stagesInLane.length; i += 1) {
        const current = stagesInLane[i];
        let next: ApiProcessState | undefined;
        let nextLane: CanonicalState = lane;
        if (i + 1 < stagesInLane.length) {
          next = stagesInLane[i + 1];
        } else {
          // Walk forward through subsequent flow lanes for the next non-
          // empty one.
          for (let j = laneIdx + 1; j < FLOW_LANES.length; j += 1) {
            const candidates = stagesByLane.get(FLOW_LANES[j]) ?? [];
            if (candidates.length > 0) {
              next = candidates[0];
              nextLane = FLOW_LANES[j];
              break;
            }
          }
        }
        if (next) {
          map.set(current.id, {
            stage: next,
            actor: transitionActor(lane, nextLane),
          });
        }
      }
    }
    return map;
  }, [stagesByLane]);

  const [dragOverLane, setDragOverLane] = useState<CanonicalState | null>(null);
  const [dragOverStageId, setDragOverStageId] = useState<string | null>(null);

  const handleDropOnLane = useCallback(
    (event: ReactDragEvent<HTMLDivElement>, lane: CanonicalState) => {
      event.preventDefault();
      setDragOverLane(null);
      setDragOverStageId(null);
      const stageId = event.dataTransfer.getData(DRAG_STAGE_MIME);
      if (!stageId) return;
      const stage = process.states.find((s) => s.id === stageId);
      if (!stage) return;
      // Drop into the lane's blank area = append to end of that lane.
      // If the stage was already in another lane, this also flips its state.
      if (stage.state !== lane && onStageStateChange) {
        onStageStateChange(stageId, lane);
      }
      if (onReorderStages) {
        onReorderStages(stageId, lane, null);
      }
    },
    [process.states, onStageStateChange, onReorderStages],
  );

  const handleDropOnStage = useCallback(
    (
      event: ReactDragEvent<HTMLDivElement>,
      targetStage: ApiProcessState,
      targetLane: CanonicalState,
    ) => {
      event.preventDefault();
      event.stopPropagation();
      setDragOverLane(null);
      setDragOverStageId(null);
      const stageId = event.dataTransfer.getData(DRAG_STAGE_MIME);
      if (!stageId || stageId === targetStage.id) return;
      const stage = process.states.find((s) => s.id === stageId);
      if (!stage) return;
      if (stage.state !== targetLane && onStageStateChange) {
        onStageStateChange(stageId, targetLane);
      }
      if (onReorderStages) {
        onReorderStages(stageId, targetLane, targetStage.id);
      }
    },
    [process.states, onStageStateChange, onReorderStages],
  );

  return (
    <div className="relative h-[640px] min-h-[480px] overflow-hidden bg-[#040814]">
      <div className="pointer-events-none absolute right-3 top-3 z-30">
        <button
          type="button"
          onClick={onAddState}
          className="pointer-events-auto rounded-full border border-aqua/35 bg-aqua/15 px-3 py-1.5 text-xs font-semibold text-aqua shadow-lg shadow-aqua/5 transition hover:bg-aqua/25"
        >
          + Add stage
        </button>
      </div>
      <div className="h-full overflow-auto px-4 pb-6 pt-4">
        <div
          className="grid gap-2"
          style={{
            gridTemplateColumns: `repeat(${CANONICAL_STATES.length}, minmax(190px, 1fr))`,
          }}
        >
          {CANONICAL_STATES.map((lane) => (
            <LaneColumn
              key={lane}
              lane={lane}
              stages={stagesByLane.get(lane) ?? []}
              selectedStageId={selectedStateId}
              dragOverLane={dragOverLane === lane}
              dragOverStageId={dragOverStageId}
              nextByStageId={nextByStageId}
              onDragOverLane={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                setDragOverLane(lane);
              }}
              onDragLeaveLane={() => setDragOverLane(null)}
              onDropOnLane={(event) => handleDropOnLane(event, lane)}
              onDragOverStage={(stageId, event) => {
                event.preventDefault();
                event.stopPropagation();
                event.dataTransfer.dropEffect = "move";
                setDragOverStageId(stageId);
              }}
              onDropOnStage={(stage, event) =>
                handleDropOnStage(event, stage, lane)
              }
              onSelectStage={onSelectState}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function LaneColumn({
  lane,
  stages,
  selectedStageId,
  dragOverLane,
  dragOverStageId,
  nextByStageId,
  onDragOverLane,
  onDragLeaveLane,
  onDropOnLane,
  onDragOverStage,
  onDropOnStage,
  onSelectStage,
}: {
  lane: CanonicalState;
  stages: ApiProcessState[];
  selectedStageId: string | undefined;
  dragOverLane: boolean;
  dragOverStageId: string | null;
  nextByStageId: Map<string, { stage: ApiProcessState; actor: "user" | "agent" }>;
  onDragOverLane: (event: ReactDragEvent<HTMLDivElement>) => void;
  onDragLeaveLane: () => void;
  onDropOnLane: (event: ReactDragEvent<HTMLDivElement>) => void;
  onDragOverStage: (
    stageId: string,
    event: ReactDragEvent<HTMLDivElement>,
  ) => void;
  onDropOnStage: (
    targetStage: ApiProcessState,
    event: ReactDragEvent<HTMLDivElement>,
  ) => void;
  onSelectStage: (stageId: string) => void;
}) {
  return (
    <div
      onDragOver={onDragOverLane}
      onDragLeave={onDragLeaveLane}
      onDrop={onDropOnLane}
      className={[
        "flex min-h-[280px] flex-col gap-1.5 rounded-xl border p-1.5 transition",
        dragOverLane
          ? "border-aqua/55 bg-aqua/[0.06] shadow-[inset_0_0_0_1px_rgba(207,169,107,0.4)]"
          : "border-white/8",
      ].join(" ")}
      style={{ background: dragOverLane ? undefined : LANE_TINT[lane] }}
    >
      <div className="sticky top-0 z-10 flex items-center gap-1.5 rounded-t-xl border-b border-white/5 bg-[#040814]/95 px-2 py-1.5 backdrop-blur">
        <span
          className="h-1.5 w-1.5 rounded-full"
          style={{ background: LANE_DOT[lane] }}
          aria-hidden
        />
        <span
          className="text-[9px] font-bold uppercase tracking-[0.18em] text-white/65"
          title={LANE_HINT[lane]}
        >
          {LANE_LABEL[lane]}
        </span>
        {stages.length > 0 && (
          <span className="ml-auto text-[10px] font-semibold text-white/35">
            {stages.length}
          </span>
        )}
      </div>

      {stages.length === 0 ? (
        <div className="grid flex-1 place-items-center rounded-md border border-dashed border-white/10 px-2 py-3 text-center text-[10px] leading-snug text-white/25">
          drop a stage
        </div>
      ) : (
        stages.map((stage) => (
          <StageCard
            key={stage.id}
            stage={stage}
            lane={lane}
            selected={stage.id === selectedStageId}
            isDragTarget={dragOverStageId === stage.id}
            next={nextByStageId.get(stage.id) ?? null}
            onClick={() => onSelectStage(stage.id)}
            onDragOver={(event) => onDragOverStage(stage.id, event)}
            onDrop={(event) => onDropOnStage(stage, event)}
          />
        ))
      )}
    </div>
  );
}

function StageCard({
  stage,
  lane,
  selected,
  isDragTarget,
  next,
  onClick,
  onDragOver,
  onDrop,
}: {
  stage: ApiProcessState;
  lane: CanonicalState;
  selected: boolean;
  isDragTarget: boolean;
  next: { stage: ApiProcessState; actor: "user" | "agent" } | null;
  onClick: () => void;
  onDragOver: (event: ReactDragEvent<HTMLDivElement>) => void;
  onDrop: (event: ReactDragEvent<HTMLDivElement>) => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData(DRAG_STAGE_MIME, stage.id);
        event.dataTransfer.effectAllowed = "move";
      }}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onClick();
        }
      }}
      data-stage-id={stage.id}
      className={[
        "rounded-lg border px-2.5 py-2 text-left shadow transition",
        "cursor-grab active:cursor-grabbing focus:outline-none focus-visible:ring-2 focus-visible:ring-aqua/60",
        selected
          ? "border-aqua/70 bg-[linear-gradient(135deg,rgba(207,169,107,0.20),rgba(207,169,107,0.05))] shadow-aqua/30"
          : isDragTarget
            ? "border-aqua/55 bg-aqua/[0.10] ring-1 ring-aqua/40"
            : "border-white/15 bg-[linear-gradient(135deg,rgba(255,255,255,0.10),rgba(255,255,255,0.03))] hover:border-aqua/40",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-bold text-white">
            {stage.name}
          </div>
          <div className="mt-0.5 truncate text-[10px] text-white/55">
            {stage.specialist_name}
          </div>
        </div>
        <span
          className="mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full"
          style={{
            background: LANE_DOT[lane],
            boxShadow: `0 0 8px ${LANE_DOT[lane]}`,
          }}
          aria-hidden
        />
      </div>
      {next ? (
        <div
          className={[
            "mt-1.5 flex items-center gap-1 truncate border-t border-white/5 pt-1 text-[9px] uppercase tracking-widest",
            next.actor === "user" ? "text-amber-200/80" : "text-white/40",
          ].join(" ")}
        >
          <span aria-hidden>→</span>
          <span className="truncate">{next.stage.name}</span>
          <span
            className={[
              "ml-auto shrink-0 rounded-sm px-1 text-[8px] font-bold",
              next.actor === "user"
                ? "bg-amber-300/15 text-amber-200"
                : "bg-white/5 text-white/45",
            ].join(" ")}
          >
            {next.actor}
          </span>
        </div>
      ) : (
        <div className="mt-1.5 truncate border-t border-white/5 pt-1 text-[9px] uppercase tracking-widest text-white/25">
          terminal
        </div>
      )}
    </div>
  );
}
