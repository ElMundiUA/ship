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

import type { ApiProcess, ApiProcessState, ApiProcessTransition } from "@/lib/api/client";

// Re-exported so existing import sites keep working.
export type Position = { x: number; y: number };

const NODE_WIDTH = 220;
const NODE_HEIGHT = 92;
const GRID = 16;
const DEFAULT_GAP = 56;
const DEFAULT_PAD = 72;
const DEFAULT_START_Y = 170;

type StateNodeData = {
  state: ApiProcessState;
  selected: boolean;
  onClick: () => void;
};

type TransitionEdgeData = {
  transition: ApiProcessTransition;
  selected: boolean;
  onClick: (id: string) => void;
};

type StateRFNode = RFNode<StateNodeData, "state">;
type TransitionRFEdge = RFEdge<TransitionEdgeData, "transition">;

/**
 * State graph editor for one process — React Flow under the hood
 * (snap-to-grid pan/zoom, animated edges, minimap, controls) with the
 * same prop signature as the legacy canvas. Custom ``state`` node
 * renders the existing card design; ``transition`` edge optionally
 * shows the condition expression and the "requires human" pip.
 */
export function ProcessCanvasEditor(props: {
  process: ApiProcess;
  selectedStateId?: string;
  selectedTransitionId?: string | null;
  onSelectState: (stateId: string) => void;
  onSelectTransition: (transitionId: string) => void;
  onAddState: () => void;
  onPositionsChange: (positions: Record<string, Position>) => void;
}) {
  // ReactFlow needs to live inside a Provider for fitView / instance
  // hooks. Wrap once at the editor boundary so consumers don't have to.
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
}: {
  process: ApiProcess;
  selectedStateId?: string;
  selectedTransitionId?: string | null;
  onSelectState: (stateId: string) => void;
  onSelectTransition: (transitionId: string) => void;
  onAddState: () => void;
  onPositionsChange: (positions: Record<string, Position>) => void;
}) {
  // Position seeds: prefer state.layout, otherwise lay out left-to-right.
  const layoutSeed = useMemo<Record<string, Position>>(
    () =>
      Object.fromEntries(
        process.states.map((state, index) => [
          state.id,
          state.layout ?? {
            x: DEFAULT_PAD + index * (NODE_WIDTH + DEFAULT_GAP),
            y: DEFAULT_START_Y,
          },
        ]),
      ),
    [process.states],
  );

  const [positions, setPositions] = useState<Record<string, Position>>(layoutSeed);

  // Re-seed when the upstream process changes (different process loaded
  // or layout reset). Also reset selection-driven recomputes.
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

  const nodes = useMemo<StateRFNode[]>(
    () =>
      process.states.map((state) => {
        const pos = positions[state.id] ?? layoutSeed[state.id];
        return {
          id: state.id,
          type: "state",
          position: { x: pos.x, y: pos.y },
          data: {
            state,
            selected: state.id === selectedStateId,
            onClick: () => handleSelectState(state.id),
          },
          // Tell React Flow our node is fixed-size so its bounds + edges
          // line up before any first measurement pass.
          width: NODE_WIDTH,
          height: NODE_HEIGHT,
          draggable: true,
        };
      }),
    [process.states, positions, layoutSeed, selectedStateId, handleSelectState],
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

  const onNodesChange: OnNodesChange = useCallback(
    (changes) => {
      let dirty = false;
      const next = { ...positions };
      for (const change of changes as NodeChange[]) {
        if (change.type === "position" && change.position) {
          // While dragging, change.dragging === true; commit only on
          // settle (release) so we don't fire onPositionsChange 60×/s.
          next[change.id] = {
            x: Math.max(GRID, Math.round(change.position.x / GRID) * GRID),
            y: Math.max(GRID, Math.round(change.position.y / GRID) * GRID),
          };
          if (!change.dragging) dirty = true;
          else {
            // live preview — apply but don't commit upstream yet
            setPositions(next);
            return;
          }
        }
      }
      if (dirty) {
        setPositions(next);
        onPositionsChange(next);
      }
    },
    [positions, onPositionsChange],
  );

  const nodeTypes = useMemo(() => ({ state: StateNode }), []);
  const edgeTypes = useMemo(() => ({ transition: TransitionEdge }), []);

  return (
    <div className="relative h-[640px] min-h-[480px] overflow-hidden bg-[#040814]">
      {/* Floating Add-state pill, top-right */}
      <div className="pointer-events-none absolute right-3 top-3 z-10">
        <button
          type="button"
          onClick={onAddState}
          className="pointer-events-auto rounded-full border border-aqua/35 bg-aqua/15 px-3 py-1.5 text-xs font-semibold text-aqua shadow-lg shadow-aqua/5 transition hover:bg-aqua/25"
        >
          Add from palette
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
        fitViewOptions={{ padding: 0.18, maxZoom: 1, minZoom: 0.4 }}
        minZoom={0.25}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        // Gentle deceleration on pan; default is OK but slightly punchy.
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
          gap={GRID}
          size={1}
          color="rgba(207, 169, 107, 0.18)"
        />
        <Controls
          className="!rounded-2xl !border !border-white/10 !bg-black/65 !text-white"
          showInteractive={false}
        />
        <MiniMap
          className="!rounded-xl !border !border-white/10 !bg-black/60"
          maskColor="rgba(4, 8, 20, 0.55)"
          nodeColor={(node) => {
            const data = node.data as StateNodeData | undefined;
            if (data?.selected) return "rgba(207,169,107,0.65)";
            return "rgba(255,255,255,0.18)";
          }}
          pannable
          zoomable
        />
      </ReactFlow>
    </div>
  );
}

function StateNode({ data }: NodeProps<StateRFNode>) {
  const { state, selected, onClick } = data;
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={(event) => {
        // Don't trigger selection if user is mid-drag — React Flow already
        // suppresses click on drag-stop, but be defensive.
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
        "rounded-[1.35rem] border px-4 py-3 transition",
        "cursor-grab shadow-2xl active:cursor-grabbing select-none",
        selected
          ? "border-aqua/70 bg-[linear-gradient(135deg,rgba(207,169,107,0.18),rgba(207,169,107,0.06))] shadow-aqua/20"
          : "border-white/12 bg-[linear-gradient(135deg,rgba(255,255,255,0.09),rgba(255,255,255,0.035))] hover:border-aqua/35 hover:bg-white/[0.07]",
      ].join(" ")}
    >
      <Handle type="target" position={RFPosition.Left} className="!h-2 !w-2 !border-aqua/40 !bg-aqua/40" />
      <div className="flex h-full min-w-0 flex-col justify-between">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="truncate font-display text-base font-bold text-white">
              {state.name}
            </div>
            <div className="mt-1 truncate text-xs text-white/55">
              {state.specialist_name}
            </div>
          </div>
          <span className="mt-0.5 h-2 w-2 shrink-0 rounded-full bg-aqua shadow-[0_0_18px_rgba(207,169,107,0.8)]" />
        </div>
        <div className="flex items-center justify-between text-[10px] uppercase tracking-[0.18em] text-white/35">
          <span>State</span>
          <span>{state.runtime.health}</span>
        </div>
      </div>
      <Handle type="source" position={RFPosition.Right} className="!h-2 !w-2 !border-aqua/40 !bg-aqua/40" />
    </div>
  );
}

function TransitionEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
}: EdgeProps<TransitionRFEdge>) {
  const { transition, selected, onClick } = data ?? ({} as TransitionEdgeData);
  // Smooth bezier path for the edge.
  const midX = (sourceX + targetX) / 2;
  const path = `M ${sourceX} ${sourceY} C ${midX} ${sourceY}, ${midX} ${targetY}, ${targetX} ${targetY}`;
  const labelX = (sourceX + targetX) / 2;
  const labelY = (sourceY + targetY) / 2 - 12;
  const cond = transition?.conditions[0]?.expression;
  const stroke = selected
    ? "rgba(207, 169, 107, 0.85)"
    : "rgba(207, 169, 107, 0.32)";
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
        stroke={stroke}
        strokeWidth={selected ? 3 : 2}
        markerEnd="url(#rf-arrow-champagne)"
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
      {transition?.requires_human ? (
        <g
          transform={`translate(${labelX - 9}, ${labelY + 6})`}
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
      {/* Inject one champagne arrowhead per render — id is stable so the
       * defs collapse across all edges in the canvas. */}
      <defs>
        <marker
          id="rf-arrow-champagne"
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
