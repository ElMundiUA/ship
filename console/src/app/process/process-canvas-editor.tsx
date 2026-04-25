"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { type PointerEvent, useMemo, useRef, useState } from "react";

import { Card } from "@/components/ui";
import type { ApiProcess, ApiProcessState } from "@/lib/api/client";

const NODE_WIDTH = 210;
const NODE_HEIGHT = 108;
const GAP = 56;
const PAD = 72;
const START_Y = 230;
const INSPECTOR_RESERVE = 420;

type Position = { x: number; y: number };
type DragState = {
  id: string;
  pointerId: number;
  offsetX: number;
  offsetY: number;
  moved: boolean;
};

export function ProcessCanvasEditor({
  process,
  selectedStateId,
  editMode,
}: {
  process: ApiProcess;
  selectedStateId?: string;
  editMode: boolean;
}) {
  const router = useRouter();
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const suppressClickRef = useRef(false);
  const initialPositions = useMemo(
    () =>
      Object.fromEntries(
        process.states.map((state, index) => [
          state.id,
          { x: PAD + index * (NODE_WIDTH + GAP), y: START_Y },
        ]),
      ) as Record<string, Position>,
    [process.states],
  );
  const [positions, setPositions] = useState<Record<string, Position>>(
    initialPositions,
  );

  const maxX = Math.max(
    ...process.states.map((state) => positions[state.id]?.x ?? PAD),
    PAD,
  );
  const maxY = Math.max(
    ...process.states.map((state) => positions[state.id]?.y ?? START_Y),
    START_Y,
  );
  const canvasWidth = Math.max(
    1280,
    maxX + NODE_WIDTH + PAD + INSPECTOR_RESERVE,
  );
  const canvasHeight = Math.max(620, maxY + NODE_HEIGHT + PAD);

  function onPointerDown(
    event: PointerEvent<HTMLDivElement>,
    stateId: string,
  ) {
    if (!editMode) return;
    const pos = positions[stateId] ?? initialPositions[stateId];
    if (!pos) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = {
      id: stateId,
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left - pos.x,
      offsetY: event.clientY - rect.top - pos.y,
      moved: false,
    };
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!rect) return;
    const next = {
      x: Math.max(PAD / 2, event.clientX - rect.left - drag.offsetX),
      y: Math.max(96, event.clientY - rect.top - drag.offsetY),
    };
    drag.moved = true;
    setPositions((current) => ({ ...current, [drag.id]: next }));
  }

  function onPointerUp(event: PointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    suppressClickRef.current = drag.moved;
    dragRef.current = null;
  }

  function onNodeClick(stateId: string) {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    router.push(`/process${editMode ? "?mode=edit&" : "?"}state=${stateId}`);
  }

  return (
    <Card className="min-h-[680px] overflow-hidden" padded={false}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/10 px-4 py-3">
        <div>
          <h2 className="font-display text-base font-bold text-white">
            {process.name}
          </h2>
          <p className="mt-0.5 text-xs text-white/45">
            {editMode
              ? "Edit layout mode. Drag state cards around the canvas."
              : "Canvas view. Select a state to inspect instructions, rules, and tasks in the side panel."}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/process"
            className={[
              "rounded-full px-3 py-1 text-xs font-semibold transition",
              !editMode
                ? "bg-aqua/15 text-aqua"
                : "border border-white/10 bg-white/[0.04] text-white/55 hover:text-white",
            ].join(" ")}
          >
            View
          </Link>
          <Link
            href="/process?mode=edit"
            className={[
              "rounded-full px-3 py-1 text-xs font-semibold transition",
              editMode
                ? "bg-aqua/15 text-aqua"
                : "border border-white/10 bg-white/[0.04] text-white/55 hover:text-white",
            ].join(" ")}
          >
            Edit layout
          </Link>
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-white/45">
            {process.states.length} states
          </span>
        </div>
      </div>
      <div className="h-[620px] overflow-auto bg-[radial-gradient(circle_at_1px_1px,rgba(255,255,255,0.10)_1px,transparent_0)] [background-size:24px_24px]">
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
            {process.states.slice(0, -1).map((state, index) => {
              const from = positions[state.id] ?? initialPositions[state.id];
              const toState = process.states[index + 1];
              const to = positions[toState.id] ?? initialPositions[toState.id];
              const x1 = from.x + NODE_WIDTH;
              const x2 = to.x;
              const y1 = from.y + NODE_HEIGHT / 2;
              const y2 = to.y + NODE_HEIGHT / 2;
              const mid = (x1 + x2) / 2;
              return (
                <path
                  key={`${state.id}-edge`}
                  d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke="rgba(255,255,255,0.22)"
                  strokeWidth="2"
                  markerEnd="url(#arrow)"
                />
              );
            })}
            <defs>
              <marker
                id="arrow"
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
                editMode={editMode}
                onPointerDown={(event) => onPointerDown(event, state.id)}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
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
  editMode,
  onPointerDown,
  onPointerMove,
  onPointerUp,
  onClick,
}: {
  state: ApiProcessState;
  selected: boolean;
  step: number;
  x: number;
  y: number;
  editMode: boolean;
  onPointerDown: (event: PointerEvent<HTMLDivElement>) => void;
  onPointerMove: (event: PointerEvent<HTMLDivElement>) => void;
  onPointerUp: (event: PointerEvent<HTMLDivElement>) => void;
  onClick: () => void;
}) {
  return (
    <div
      role="button"
      tabIndex={0}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onClick={onClick}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onClick();
      }}
      style={{ left: x, top: y }}
      className={[
        "absolute block h-[108px] w-[210px] rounded-2xl border p-4 transition",
        editMode ? "cursor-grab active:cursor-grabbing" : "cursor-pointer",
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
        {editMode ? "Drag to move" : "Inspect rules"}
      </div>
    </div>
  );
}
