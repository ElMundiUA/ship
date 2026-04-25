"use client";

import { useRouter } from "next/navigation";
import {
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";

import { Card } from "@/components/ui";
import type { ApiProcess, ApiProcessState, ApiRepoConfig } from "@/lib/api/client";
import { ProcessConfigProposalFields } from "./process-config-proposal-fields";

const NODE_WIDTH = 210;
const NODE_HEIGHT = 108;
const GAP = 56;
const PAD = 72;
const START_Y = 170;

type Position = { x: number; y: number };
type DragState = {
  id: string;
  pointerId: number | null;
  offsetX: number;
  offsetY: number;
  moved: boolean;
};

export function ProcessCanvasEditor({
  workspaceId,
  process,
  selectedStateId,
  repoId,
  config,
  processConfig,
}: {
  workspaceId: string;
  process: ApiProcess;
  selectedStateId?: string;
  repoId?: string;
  config: ApiRepoConfig | null;
  processConfig: Record<string, unknown>;
}) {
  const router = useRouter();
  const arrowMarkerId = `process-arrow-${useId().replaceAll(":", "")}`;
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const suppressClickRef = useRef(false);
  const initialPositions = useMemo(
    () =>
      Object.fromEntries(
        process.states.map((state, index) => [
          state.id,
          state.layout ?? { x: PAD + index * (NODE_WIDTH + GAP), y: START_Y },
        ]),
      ) as Record<string, Position>,
    [process.states],
  );
  const [positions, setPositions] = useState<Record<string, Position>>(
    initialPositions,
  );

  useEffect(() => {
    setPositions(initialPositions);
  }, [initialPositions]);

  const layoutJson = useMemo(
    () =>
      JSON.stringify(
        Object.fromEntries(
          Object.entries(positions).map(([stateId, position]) => [
            stateId,
            {
              x: Math.round(position.x),
              y: Math.round(position.y),
            },
          ]),
        ),
      ),
    [positions],
  );
  const initialLayoutJson = useMemo(
    () =>
      JSON.stringify(
        Object.fromEntries(
          Object.entries(initialPositions).map(([stateId, position]) => [
            stateId,
            {
              x: Math.round(position.x),
              y: Math.round(position.y),
            },
          ]),
        ),
      ),
    [initialPositions],
  );
  const hasLayoutChanges = layoutJson !== initialLayoutJson;

  const updateDraggedNode = useCallback((clientX: number, clientY: number) => {
    const drag = dragRef.current;
    if (!drag) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const next = {
      x: Math.max(PAD / 2, clientX - rect.left - drag.offsetX),
      y: Math.max(96, clientY - rect.top - drag.offsetY),
    };
    drag.moved = true;
    setPositions((current) => ({ ...current, [drag.id]: next }));
  }, []);

  const moveDraggedNode = useCallback((event: PointerEvent) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    event.preventDefault();
    updateDraggedNode(event.clientX, event.clientY);
  }, [updateDraggedNode]);

  const moveDraggedNodeWithMouse = useCallback((event: MouseEvent) => {
    if (!dragRef.current) return;
    event.preventDefault();
    updateDraggedNode(event.clientX, event.clientY);
  }, [updateDraggedNode]);

  const removeDragListeners = useCallback(() => {
    window.removeEventListener("pointermove", moveDraggedNode);
    window.removeEventListener("mousemove", moveDraggedNodeWithMouse);
  }, [moveDraggedNode, moveDraggedNodeWithMouse]);

  const stopDragging = useCallback((event: PointerEvent) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    suppressClickRef.current = drag.moved;
    dragRef.current = null;
    removeDragListeners();
    window.removeEventListener("pointerup", stopDragging);
    window.removeEventListener("pointercancel", stopDragging);
  }, [removeDragListeners]);

  const stopDraggingWithMouse = useCallback(() => {
    const drag = dragRef.current;
    if (!drag) return;
    suppressClickRef.current = drag.moved;
    dragRef.current = null;
    removeDragListeners();
    window.removeEventListener("mouseup", stopDraggingWithMouse);
  }, [removeDragListeners]);

  useEffect(() => {
    return () => {
      dragRef.current = null;
      removeDragListeners();
      window.removeEventListener("pointerup", stopDragging);
      window.removeEventListener("pointercancel", stopDragging);
      window.removeEventListener("mouseup", stopDraggingWithMouse);
    };
  }, [removeDragListeners, stopDragging, stopDraggingWithMouse]);

  const maxX = Math.max(
    ...process.states.map((state) => positions[state.id]?.x ?? PAD),
    PAD,
  );
  const maxY = Math.max(
    ...process.states.map((state) => positions[state.id]?.y ?? START_Y),
    START_Y,
  );
  const canvasWidth = Math.max(
    1120,
    maxX + NODE_WIDTH + PAD,
  );
  const canvasHeight = Math.max(520, maxY + NODE_HEIGHT + PAD);
  const edges =
    process.transitions.length > 0
      ? process.transitions
      : process.states.slice(0, -1).map((state, index) => ({
          id: `${state.id}_to_${process.states[index + 1].id}`,
          from_state_id: state.id,
          to_state_id: process.states[index + 1].id,
          conditions: [],
        }));

  function onPointerDown(
    event: ReactPointerEvent<HTMLDivElement>,
    stateId: string,
  ) {
    const pos = positions[stateId] ?? initialPositions[stateId];
    if (!pos) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    event.preventDefault();
    dragRef.current = {
      id: stateId,
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left - pos.x,
      offsetY: event.clientY - rect.top - pos.y,
      moved: false,
    };
    window.addEventListener("pointermove", moveDraggedNode);
    window.addEventListener("pointerup", stopDragging);
    window.addEventListener("pointercancel", stopDragging);
  }

  function onMouseDown(
    event: ReactMouseEvent<HTMLDivElement>,
    stateId: string,
  ) {
    if (dragRef.current) return;
    const pos = positions[stateId] ?? initialPositions[stateId];
    if (!pos) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    event.preventDefault();
    dragRef.current = {
      id: stateId,
      pointerId: null,
      offsetX: event.clientX - rect.left - pos.x,
      offsetY: event.clientY - rect.top - pos.y,
      moved: false,
    };
    window.addEventListener("mousemove", moveDraggedNodeWithMouse);
    window.addEventListener("mouseup", stopDraggingWithMouse);
  }

  function onNodeClick(stateId: string) {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    const params = new URLSearchParams({ state: stateId });
    if (repoId) params.set("repo", repoId);
    router.push(`/process?${params.toString()}`);
  }

  return (
    <Card className="h-full min-h-[560px] overflow-hidden" padded={false}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
        <div>
          <h2 className="font-display text-base font-bold text-white">
            {process.name}
          </h2>
          <p className="mt-0.5 text-xs text-white/45">
            Drag cards to reshape the process. Select a card to edit its launch
            and completion rules.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <form action="/api/process/config-propose" method="post">
            <ProcessConfigProposalFields
              workspaceId={workspaceId}
              repoId={repoId}
              config={config}
              processConfig={processConfig}
              layoutJson={layoutJson}
            />
            <button
              type="submit"
              disabled={!repoId || !hasLayoutChanges}
              className="rounded-full border border-aqua/25 bg-aqua/10 px-3 py-1 text-xs font-semibold text-aqua transition hover:bg-aqua/15 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-white/35"
            >
              Save layout
            </button>
          </form>
          <span className="rounded-full bg-aqua/15 px-3 py-1 text-xs font-semibold text-aqua">
            Editing
          </span>
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-white/45">
            {process.states.length} states
          </span>
        </div>
      </div>
      <div className="h-[calc(100%-73px)] overflow-auto bg-[radial-gradient(circle_at_1px_1px,rgba(255,255,255,0.10)_1px,transparent_0)] [background-size:24px_24px]">
        <div
          ref={canvasRef}
          className="relative"
          style={{ width: canvasWidth, height: canvasHeight }}
        >
          <svg
            aria-hidden
            className="absolute inset-0 h-full w-full"
            viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}
          >
            {edges.map((edge) => {
              const from =
                positions[edge.from_state_id] ?? initialPositions[edge.from_state_id];
              const to =
                positions[edge.to_state_id] ?? initialPositions[edge.to_state_id];
              if (!from || !to) return null;
              const x1 = from.x + NODE_WIDTH;
              const x2 = to.x;
              const y1 = from.y + NODE_HEIGHT / 2;
              const y2 = to.y + NODE_HEIGHT / 2;
              const mid = (x1 + x2) / 2;
              return (
                <path
                  key={edge.id}
                  d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke="rgba(255,255,255,0.22)"
                  strokeWidth="2"
                  markerEnd={`url(#${arrowMarkerId})`}
                />
              );
            })}
            <defs>
              <marker
                id={arrowMarkerId}
                markerHeight="8"
                markerWidth="8"
                orient="auto"
                refX="7"
                refY="4"
              >
                <path d="M 0 0 L 8 4 L 0 8 z" fill="rgba(255,255,255,0.32)" />
              </marker>
            </defs>
          </svg>
          {process.states.map((state, index) => {
            const pos = positions[state.id] ?? initialPositions[state.id];
            return (
              <StateNode
                key={state.id}
                state={state}
                selected={state.id === selectedStateId}
                step={index + 1}
                x={pos.x}
                y={pos.y}
                onPointerDown={(event) => onPointerDown(event, state.id)}
                onMouseDown={(event) => onMouseDown(event, state.id)}
                onClick={() => onNodeClick(state.id)}
              />
            );
          })}
        </div>
      </div>
    </Card>
  );
}

function StateNode({
  state,
  selected,
  step,
  x,
  y,
  onPointerDown,
  onMouseDown,
  onClick,
}: {
  state: ApiProcessState;
  selected: boolean;
  step: number;
  x: number;
  y: number;
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onMouseDown: (event: ReactMouseEvent<HTMLDivElement>) => void;
  onClick: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      draggable={false}
      onPointerDown={onPointerDown}
      onMouseDown={onMouseDown}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onClick();
        }
      }}
      style={{ left: x, top: y, touchAction: "none", userSelect: "none" }}
      className={[
        "absolute block h-[108px] w-[210px] rounded-2xl border p-4 transition",
        "cursor-grab active:cursor-grabbing",
        selected
          ? "border-aqua/60 bg-aqua/[0.08] shadow-glow"
          : "border-white/10 bg-white/[0.035] hover:border-white/25 hover:bg-white/[0.06]",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-white/35">
            Step {step}
          </div>
          <div className="mt-1 truncate font-display text-base font-bold text-white">
            {state.name}
          </div>
          <div className="mt-1 truncate text-xs text-white/55">
            {state.specialist_name}
          </div>
        </div>
        <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] font-semibold text-white/45">
          {step}
        </span>
      </div>
      <div className="mt-3 rounded-xl border border-white/10 bg-black/20 px-3 py-1.5 text-xs text-white/55">
        Drag to move · click to edit
      </div>
    </div>
  );
}
