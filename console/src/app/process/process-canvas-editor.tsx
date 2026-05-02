"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position as RFPosition,
  ReactFlow,
  ReactFlowProvider,
  type Edge as RFEdge,
  type EdgeProps,
  type Node as RFNode,
  type NodeChange,
  type NodeProps,
  type OnNodesChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useCallback, useEffect, useMemo, useState } from "react";

import type {
  ApiProcess,
  ApiProcessState,
  ApiProcessTransition,
} from "@/lib/api/client";
import {
  CANONICAL_STATES,
  type CanonicalState,
  type TransitionActor,
} from "@/lib/api/types";

// Re-exported so existing import sites keep working.
export type Position = { x: number; y: number };

// Stage cards.
const NODE_WIDTH = 220;
const NODE_HEIGHT = 92;
const GRID = 8;

// Swim-lane geometry. Lanes are wider than nodes so cards have padding
// inside; lane height grows with the tallest column. Lane backdrops
// render as React-Flow nodes (non-interactive) at z-index 0; stage
// cards float above them.
const LANE_PADDING_X = 24;
const LANE_INNER_WIDTH = NODE_WIDTH + LANE_PADDING_X * 2;
const LANE_HEADER_HEIGHT = 56;
const LANE_TOP = 0;
const LANE_HEIGHT = 1100; // tall enough to host ~10 stages per column
const NODE_VERTICAL_GAP = 24;

const LANE_DESCRIPTIONS: Record<CanonicalState, string> = {
  backlog:
    "Ticket exists; agents wait for the operator to drag it into Todo.",
  planning:
    "Intake / BA / architects scope the work. Multiple stages cycle here while the ticket stays in Linear's Todo column.",
  executing:
    "Developer / QA cycles run here while the ticket sits in In Progress.",
  reviewing:
    "Final review pending the operator's approval.",
  awaiting_input:
    "Frozen, waiting on a human answer. Overlay — ticket stays in its current Linear column with a clarification label.",
  blocked:
    "Frozen on an external blocker. Overlay — ticket stays in its current column with a blocked label.",
  closed: "Terminal.",
};

const LANE_LABEL: Record<CanonicalState, string> = {
  backlog: "Backlog",
  planning: "Planning",
  executing: "Executing",
  reviewing: "Reviewing",
  awaiting_input: "Awaiting input",
  blocked: "Blocked",
  closed: "Closed",
};

const LANE_ACCENT: Record<CanonicalState, string> = {
  backlog: "rgba(255,255,255,0.10)",
  planning: "rgba(99, 200, 255, 0.18)",
  executing: "rgba(207, 169, 107, 0.20)",
  reviewing: "rgba(168, 85, 247, 0.18)",
  awaiting_input: "rgba(255, 196, 87, 0.18)",
  blocked: "rgba(244, 114, 114, 0.18)",
  closed: "rgba(120, 200, 140, 0.18)",
};

function laneIndex(state: CanonicalState): number {
  return CANONICAL_STATES.indexOf(state);
}

function laneX(state: CanonicalState): number {
  return laneIndex(state) * LANE_INNER_WIDTH;
}

type StageNodeData = {
  state: ApiProcessState;
  selected: boolean;
  onClick: () => void;
};

type LaneNodeData = {
  lane: CanonicalState;
};

type TransitionEdgeData = {
  transition: ApiProcessTransition;
  selected: boolean;
  onClick: (id: string) => void;
};

type StageRFNode = RFNode<StageNodeData, "stage">;
type LaneRFNode = RFNode<LaneNodeData, "lane">;
type TransitionRFEdge = RFEdge<TransitionEdgeData, "transition">;

/**
 * State graph editor for one process — React Flow under the hood with
 * a swim-lane layout. Each lane represents one of the seven canonical
 * lifecycle states (backlog → closed); stage cards live inside their
 * state's lane. Dragging a stage between lanes changes its canonical
 * state field, which is the projection axis adapters bind to native
 * tracker columns.
 */
export function ProcessCanvasEditor(props: {
  process: ApiProcess;
  selectedStateId?: string;
  selectedTransitionId?: string | null;
  onSelectState: (stateId: string) => void;
  onSelectTransition: (transitionId: string) => void;
  onAddState: () => void;
  onPositionsChange: (positions: Record<string, Position>) => void;
  /** Optional: fired when a stage is dragged into a different lane. */
  onStageStateChange?: (stageId: string, nextState: CanonicalState) => void;
}) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

function CanvasInner({
  process,
  selectedStateId,
  selectedTransitionId,
  onSelectState,
  onSelectTransition,
  onAddState,
  onPositionsChange,
  onStageStateChange,
}: {
  process: ApiProcess;
  selectedStateId?: string;
  selectedTransitionId?: string | null;
  onSelectState: (stateId: string) => void;
  onSelectTransition: (transitionId: string) => void;
  onAddState: () => void;
  onPositionsChange: (positions: Record<string, Position>) => void;
  onStageStateChange?: (stageId: string, nextState: CanonicalState) => void;
}) {
  // Auto-layout: stages stack vertically inside their state's lane,
  // ordered by (legacy) x position when present so user-defined orderings
  // are preserved within a column. Operators can drag within or across
  // lanes; the layoutSeed seeds React Flow positions, then user drag
  // changes are remembered in local positions state.
  const layoutSeed = useMemo<Record<string, Position>>(() => {
    const groupedByLane = new Map<CanonicalState, ApiProcessState[]>();
    for (const stage of process.states) {
      const list = groupedByLane.get(stage.state) ?? [];
      list.push(stage);
      groupedByLane.set(stage.state, list);
    }
    const out: Record<string, Position> = {};
    for (const [lane, stages] of groupedByLane) {
      // Order by legacy x so previously laid-out stages keep their order.
      const sorted = [...stages].sort(
        (a, b) => (a.layout?.x ?? 0) - (b.layout?.x ?? 0),
      );
      const x = laneX(lane) + LANE_PADDING_X;
      let y = LANE_TOP + LANE_HEADER_HEIGHT + NODE_VERTICAL_GAP;
      for (const stage of sorted) {
        out[stage.id] = { x, y };
        y += NODE_HEIGHT + NODE_VERTICAL_GAP;
      }
    }
    return out;
  }, [process.states]);

  const [positions, setPositions] = useState<Record<string, Position>>(layoutSeed);

  useEffect(() => {
    setPositions(layoutSeed);
  }, [layoutSeed]);

  const handleSelectState = useCallback(
    (id: string) => onSelectState(id),
    [onSelectState],
  );
  const handleSelectTransition = useCallback(
    (id: string) => onSelectTransition(id),
    [onSelectTransition],
  );

  // Lane backdrops as static React Flow nodes (non-draggable, behind
  // stage cards). Rendered first so they sit at z=0.
  const laneNodes = useMemo<LaneRFNode[]>(
    () =>
      CANONICAL_STATES.map((lane) => ({
        id: `__lane__${lane}`,
        type: "lane",
        position: { x: laneX(lane), y: LANE_TOP },
        data: { lane },
        // Mark as non-interactive — React Flow won't let it be selected
        // or dragged; clicks pass through to children.
        draggable: false,
        selectable: false,
        focusable: false,
        zIndex: 0,
      })),
    [],
  );

  const stageNodes = useMemo<StageRFNode[]>(() => {
    return process.states.map((stage) => {
      const pos = positions[stage.id] ?? layoutSeed[stage.id];
      return {
        id: stage.id,
        type: "stage",
        position: { x: pos.x, y: pos.y },
        data: {
          state: stage,
          selected: stage.id === selectedStateId,
          onClick: () => handleSelectState(stage.id),
        },
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        draggable: true,
        zIndex: 10,
      };
    });
  }, [process.states, positions, layoutSeed, selectedStateId, handleSelectState]);

  const nodes = useMemo<Array<StageRFNode | LaneRFNode>>(
    () => [...laneNodes, ...stageNodes],
    [laneNodes, stageNodes],
  );

  const edges = useMemo<TransitionRFEdge[]>(
    () =>
      process.transitions.map((transition) => ({
        id: transition.id,
        source: transition.from_state_id,
        target: transition.to_state_id,
        type: "transition",
        data: {
          transition,
          selected: transition.id === selectedTransitionId,
          onClick: handleSelectTransition,
        },
        animated: transition.id === selectedTransitionId,
      })),
    [process.transitions, selectedTransitionId, handleSelectTransition],
  );

  const stageById = useMemo(
    () => new Map(process.states.map((s) => [s.id, s] as const)),
    [process.states],
  );

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => {
      let positionDirty = false;
      let stateChange: { stageId: string; nextState: CanonicalState } | null = null;
      const next = { ...positions };
      for (const change of changes as NodeChange[]) {
        if (change.type === "position" && change.position) {
          // Skip lane backdrops — they aren't draggable but defensive.
          if (change.id.startsWith("__lane__")) continue;
          // Snap to grid for smooth alignment.
          const snapped = {
            x: Math.max(0, Math.round(change.position.x / GRID) * GRID),
            y: Math.max(LANE_TOP + LANE_HEADER_HEIGHT, Math.round(change.position.y / GRID) * GRID),
          };
          next[change.id] = snapped;
          if (change.dragging) {
            // Live preview, no upstream commit yet.
            setPositions(next);
            return;
          }
          // Drag settled — figure out which lane the centre of the card
          // landed in; if it's a different lane, fire onStageStateChange.
          const cardCentre = snapped.x + NODE_WIDTH / 2;
          const targetLaneIdx = Math.max(
            0,
            Math.min(
              CANONICAL_STATES.length - 1,
              Math.floor(cardCentre / LANE_INNER_WIDTH),
            ),
          );
          const targetLane = CANONICAL_STATES[targetLaneIdx];
          const stage = stageById.get(change.id);
          if (stage && stage.state !== targetLane) {
            stateChange = { stageId: change.id, nextState: targetLane };
            // Snap the card to the lane's interior x so it sits cleanly.
            next[change.id] = {
              x: laneX(targetLane) + LANE_PADDING_X,
              y: snapped.y,
            };
          }
          positionDirty = true;
        }
      }
      if (positionDirty) {
        setPositions(next);
        onPositionsChange(next);
      }
      if (stateChange && onStageStateChange) {
        onStageStateChange(stateChange.stageId, stateChange.nextState);
      }
    },
    [positions, onPositionsChange, onStageStateChange, stageById],
  );

  const nodeTypes = useMemo(() => ({ stage: StageNode, lane: LaneNode }), []);
  const edgeTypes = useMemo(() => ({ transition: TransitionEdge }), []);

  return (
    <div className="relative h-[720px] min-h-[560px] overflow-hidden bg-[#040814]">
      {/* Floating Add-state pill, top-right */}
      <div className="pointer-events-none absolute right-3 top-3 z-20">
        <button
          type="button"
          onClick={onAddState}
          className="pointer-events-auto rounded-full border border-aqua/35 bg-aqua/15 px-3 py-1.5 text-xs font-semibold text-aqua shadow-lg shadow-aqua/5 transition hover:bg-aqua/25"
        >
          Add stage
        </button>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgeClick={(_event, edge) => handleSelectTransition(edge.id)}
        snapToGrid
        snapGrid={[GRID, GRID]}
        fitView
        fitViewOptions={{ padding: 0.06, maxZoom: 1, minZoom: 0.3 }}
        minZoom={0.2}
        maxZoom={1.6}
        proOptions={{ hideAttribution: true }}
        panOnDrag
        zoomOnScroll
        zoomOnPinch
        nodesConnectable={false}
        nodesDraggable
        elementsSelectable
        selectNodesOnDrag={false}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={GRID * 2}
          size={1}
          color="rgba(207, 169, 107, 0.10)"
        />
        <Controls
          className="!rounded-2xl !border !border-white/10 !bg-black/65 !text-white"
          showInteractive={false}
        />
        <MiniMap
          className="!rounded-xl !border !border-white/10 !bg-black/60"
          maskColor="rgba(4, 8, 20, 0.55)"
          nodeColor={(node) => {
            if (node.type === "lane") {
              const data = node.data as LaneNodeData | undefined;
              return data ? LANE_ACCENT[data.lane] : "rgba(255,255,255,0.05)";
            }
            const data = node.data as StageNodeData | undefined;
            if (data?.selected) return "rgba(207,169,107,0.65)";
            return "rgba(255,255,255,0.32)";
          }}
          pannable
          zoomable
        />
      </ReactFlow>
    </div>
  );
}

function LaneNode({ data }: NodeProps<LaneRFNode>) {
  const { lane } = data;
  const accent = LANE_ACCENT[lane];
  return (
    <div
      style={{
        width: LANE_INNER_WIDTH,
        height: LANE_HEIGHT,
        background: `linear-gradient(180deg, ${accent} 0%, rgba(8, 12, 22, 0) 70%)`,
        borderLeft: "1px solid rgba(255,255,255,0.06)",
        borderRight: "1px solid rgba(255,255,255,0.06)",
      }}
      className="pointer-events-none relative"
    >
      <div className="border-b border-white/5 px-3 py-3">
        <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/50">
          {LANE_LABEL[lane]}
        </div>
        <div className="mt-1 line-clamp-3 text-[10px] leading-snug text-white/35">
          {LANE_DESCRIPTIONS[lane]}
        </div>
      </div>
    </div>
  );
}

function StageNode({ data }: NodeProps<StageRFNode>) {
  const { state, selected, onClick } = data;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onClick();
        }
      }}
      style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
      className={[
        "rounded-2xl border px-3 py-2 transition",
        "cursor-grab shadow-2xl active:cursor-grabbing select-none",
        selected
          ? "border-aqua/70 bg-[linear-gradient(135deg,rgba(207,169,107,0.20),rgba(207,169,107,0.05))] shadow-aqua/30"
          : "border-white/15 bg-[linear-gradient(135deg,rgba(255,255,255,0.10),rgba(255,255,255,0.03))] hover:border-aqua/40",
      ].join(" ")}
    >
      <Handle type="target" position={RFPosition.Left} className="!h-2 !w-2 !border-aqua/40 !bg-aqua/40" />
      <div className="flex h-full min-w-0 flex-col justify-between">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate font-display text-sm font-bold text-white">
              {state.name}
            </div>
            <div className="mt-0.5 truncate text-[11px] text-white/55">
              {state.specialist_name}
            </div>
          </div>
          <span
            className="mt-0.5 h-2 w-2 shrink-0 rounded-full"
            style={{
              background: LANE_ACCENT[state.state],
              boxShadow: `0 0 14px ${LANE_ACCENT[state.state]}`,
            }}
          />
        </div>
        <div className="flex items-center justify-between text-[9px] uppercase tracking-[0.18em] text-white/35">
          <span>{state.runtime.health}</span>
          <span className="rounded-sm border border-white/10 bg-black/30 px-1 py-0.5 text-white/55">
            {LANE_LABEL[state.state]}
          </span>
        </div>
      </div>
      <Handle type="source" position={RFPosition.Right} className="!h-2 !w-2 !border-aqua/40 !bg-aqua/40" />
    </div>
  );
}

function actorStyle(actor: TransitionActor | undefined): {
  stroke: string;
  dash: string | undefined;
  label: string;
} {
  switch (actor) {
    case "user":
      return {
        stroke: "rgba(255, 196, 87, 0.85)",
        dash: undefined,
        label: "user",
      };
    case "either":
      return {
        stroke: "rgba(207, 169, 107, 0.85)",
        dash: "6 4",
        label: "either",
      };
    case "agent":
    default:
      return {
        stroke: "rgba(207, 169, 107, 0.55)",
        dash: undefined,
        label: "agent",
      };
  }
}

function TransitionEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
}: EdgeProps<TransitionRFEdge>) {
  const { transition, selected, onClick } = data ?? ({} as TransitionEdgeData);
  const style = actorStyle(transition?.trigger_actor);
  const midX = (sourceX + targetX) / 2;
  const path = `M ${sourceX} ${sourceY} C ${midX} ${sourceY}, ${midX} ${targetY}, ${targetX} ${targetY}`;
  const labelX = (sourceX + targetX) / 2;
  const labelY = (sourceY + targetY) / 2 - 10;
  const cond = transition?.conditions[0]?.expression;
  return (
    <g
      onClick={(event) => {
        event.stopPropagation();
        if (transition) onClick(transition.id);
      }}
      className="cursor-pointer"
    >
      {/* Wide invisible hit target so clicks land on thin edges. */}
      <path
        d={path}
        fill="none"
        stroke="transparent"
        strokeWidth={20}
        pointerEvents="stroke"
      />
      <path
        d={path}
        fill="none"
        stroke={style.stroke}
        strokeWidth={selected ? 3 : 2}
        strokeDasharray={style.dash}
        markerEnd="url(#rf-arrow-actor)"
      />
      {cond ? (
        <text
          x={labelX}
          y={labelY}
          textAnchor="middle"
          className="fill-white/55"
          style={{ fontSize: 10, pointerEvents: "none" }}
        >
          {cond.length > 28 ? `${cond.slice(0, 28)}…` : cond}
        </text>
      ) : null}
      <text
        x={labelX}
        y={labelY + 14}
        textAnchor="middle"
        style={{
          fontSize: 9,
          pointerEvents: "none",
          letterSpacing: "0.16em",
          textTransform: "uppercase",
          fontWeight: 700,
        }}
        fill={style.stroke}
      >
        {style.label}
      </text>
      {transition?.requires_human ? (
        <g
          transform={`translate(${labelX - 9}, ${labelY + 22})`}
          pointerEvents="none"
        >
          <circle
            cx="9"
            cy="8"
            r="8"
            fill="rgba(0,0,0,0.45)"
            stroke="rgba(255,255,255,0.25)"
            strokeWidth="1"
          />
          <text
            x="9"
            y="11"
            textAnchor="middle"
            style={{ fontSize: 9 }}
            className="fill-white/85"
          >
            ★
          </text>
        </g>
      ) : null}
      <defs>
        <marker
          id="rf-arrow-actor"
          markerHeight="8"
          markerWidth="8"
          orient="auto"
          refX="7"
          refY="4"
        >
          <path d="M 0 0 L 8 4 L 0 8 z" fill="rgba(207, 169, 107, 0.7)" />
        </marker>
      </defs>
    </g>
  );
}
