"use client";

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

import type { ApiProcess, ApiProcessState, ApiProcessTransition } from "@/lib/api/client";

const NODE_WIDTH = 210;
const NODE_HEIGHT = 76;
const GAP = 56;
const PAD = 72;
const START_Y = 170;
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 2;
const ZOOM_STEP = 0.1;

export type Position = { x: number; y: number };
type DragState = {
  id: string;
  pointerId: number | null;
  offsetX: number;
  offsetY: number;
  moved: boolean;
};
type PanState = {
  pointerId: number;
  startX: number;
  startY: number;
  originX: number;
  originY: number;
};

export function ProcessCanvasEditor({
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
  const arrowMarkerId = `process-arrow-${useId().replaceAll(":", "")}`;
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const panDragRef = useRef<PanState | null>(null);
  const positionsRef = useRef<Record<string, Position>>({});
  const panRef = useRef<Position>({ x: 0, y: 0 });
  const zoomRef = useRef(1);
  const suppressClickRef = useRef(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState<Position>({ x: 0, y: 0 });
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
    positionsRef.current = initialPositions;
    setPositions(initialPositions);
  }, [initialPositions]);

  const updateDraggedNode = useCallback((clientX: number, clientY: number) => {
    const drag = dragRef.current;
    if (!drag) return;
    const point = canvasPointFromClient(clientX, clientY);
    if (!point) return;
    const next = {
      x: Math.max(PAD / 2, point.x - drag.offsetX),
      y: Math.max(96, point.y - drag.offsetY),
    };
    drag.moved = true;
    setPositions((current) => {
      const updated = { ...current, [drag.id]: next };
      positionsRef.current = updated;
      return updated;
    });
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
    if (drag.moved) onPositionsChange(positionsRef.current);
  }, [onPositionsChange, removeDragListeners]);

  const stopDraggingWithMouse = useCallback(() => {
    const drag = dragRef.current;
    if (!drag) return;
    suppressClickRef.current = drag.moved;
    dragRef.current = null;
    removeDragListeners();
    window.removeEventListener("mouseup", stopDraggingWithMouse);
    if (drag.moved) onPositionsChange(positionsRef.current);
  }, [onPositionsChange, removeDragListeners]);

  useEffect(() => {
    return () => {
      dragRef.current = null;
      panDragRef.current = null;
      removeDragListeners();
      window.removeEventListener("pointerup", stopDragging);
      window.removeEventListener("pointercancel", stopDragging);
      window.removeEventListener("mouseup", stopDraggingWithMouse);
    };
  }, [removeDragListeners, stopDragging, stopDraggingWithMouse]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return undefined;

    function onWheel(event: WheelEvent) {
      event.preventDefault();

      const wheelViewport = viewportRef.current;
      if (!wheelViewport) return;

      if (event.ctrlKey || event.metaKey) {
        const rect = wheelViewport.getBoundingClientRect();
        const currentZoom = zoomRef.current;
        const nextZoom = clampZoom(currentZoom * Math.exp(-event.deltaY * 0.002));
        const panPosition = panRef.current;
        const viewportPoint = {
          x: event.clientX - rect.left,
          y: event.clientY - rect.top,
        };
        const canvasPoint = {
          x: (viewportPoint.x - panPosition.x) / currentZoom,
          y: (viewportPoint.y - panPosition.y) / currentZoom,
        };
        const nextPan = {
          x: viewportPoint.x - canvasPoint.x * nextZoom,
          y: viewportPoint.y - canvasPoint.y * nextZoom,
        };

        zoomRef.current = nextZoom;
        panRef.current = nextPan;
        setZoom(nextZoom);
        setPan(nextPan);
        return;
      }

      const panPosition = panRef.current;
      const nextPan = {
        x: panPosition.x - event.deltaX,
        y: panPosition.y - event.deltaY,
      };
      panRef.current = nextPan;
      setPan(nextPan);
    }

    viewport.addEventListener("wheel", onWheel, { passive: false });
    return () => viewport.removeEventListener("wheel", onWheel);
  }, []);

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
  const transitionsInteractive = process.transitions.length > 0;
  const edges: ApiProcessTransition[] = useMemo(
    () =>
      process.transitions.length > 0
        ? process.transitions
        : process.states.length > 1
          ? process.states.slice(0, -1).map((state, index) => {
              const to = process.states[index + 1].id;
              return {
                id: `${state.id}_to_${to}_chain`,
                from_state_id: state.id,
                to_state_id: to,
                conditions: [] as { expression: string }[],
              } as ApiProcessTransition;
            })
          : [],
    [process.transitions, process.states],
  );

  function onPointerDown(
    event: ReactPointerEvent<HTMLDivElement>,
    stateId: string,
  ) {
    event.stopPropagation();
    const pos = positions[stateId] ?? initialPositions[stateId];
    if (!pos) return;
    const point = canvasPointFromClient(event.clientX, event.clientY);
    if (!point) return;
    event.preventDefault();
    dragRef.current = {
      id: stateId,
      pointerId: event.pointerId,
      offsetX: point.x - pos.x,
      offsetY: point.y - pos.y,
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
    event.stopPropagation();
    if (dragRef.current) return;
    const pos = positions[stateId] ?? initialPositions[stateId];
    if (!pos) return;
    const point = canvasPointFromClient(event.clientX, event.clientY);
    if (!point) return;
    event.preventDefault();
    dragRef.current = {
      id: stateId,
      pointerId: null,
      offsetX: point.x - pos.x,
      offsetY: point.y - pos.y,
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
    onSelectState(stateId);
  }

  function setPanValue(nextPan: Position) {
    panRef.current = nextPan;
    setPan(nextPan);
  }

  function setZoomValue(nextZoom: number) {
    const clamped = clampZoom(nextZoom);
    zoomRef.current = clamped;
    setZoom(clamped);
  }

  function canvasPointFromClient(clientX: number, clientY: number): Position | null {
    const rect = viewportRef.current?.getBoundingClientRect();
    if (!rect) return null;
    const panPosition = panRef.current;
    const currentZoom = zoomRef.current;
    return {
      x: (clientX - rect.left - panPosition.x) / currentZoom,
      y: (clientY - rect.top - panPosition.y) / currentZoom,
    };
  }

  function zoomAt(clientX: number, clientY: number, nextZoom: number) {
    const rect = viewportRef.current?.getBoundingClientRect();
    if (!rect) return;

    const currentZoom = zoomRef.current;
    const clampedZoom = clampZoom(nextZoom);
    const panPosition = panRef.current;
    const viewportPoint = {
      x: clientX - rect.left,
      y: clientY - rect.top,
    };
    const canvasPoint = {
      x: (viewportPoint.x - panPosition.x) / currentZoom,
      y: (viewportPoint.y - panPosition.y) / currentZoom,
    };

    setZoomValue(clampedZoom);
    setPanValue({
      x: viewportPoint.x - canvasPoint.x * clampedZoom,
      y: viewportPoint.y - canvasPoint.y * clampedZoom,
    });
  }

  function changeZoom(delta: number) {
    const rect = viewportRef.current?.getBoundingClientRect();
    if (!rect) {
      setZoomValue(zoomRef.current + delta);
      return;
    }
    zoomAt(
      rect.left + rect.width / 2,
      rect.top + rect.height / 2,
      zoomRef.current + delta,
    );
  }

  function resetZoom() {
    const rect = viewportRef.current?.getBoundingClientRect();
    if (!rect) {
      setZoomValue(1);
      return;
    }
    zoomAt(rect.left + rect.width / 2, rect.top + rect.height / 2, 1);
  }

  function onViewportPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    if (
      (event.target as Element).closest(
        "[data-process-node], [data-process-control], [data-process-edge]",
      )
    ) {
      return;
    }

    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    const panPosition = panRef.current;
    panDragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: panPosition.x,
      originY: panPosition.y,
    };
  }

  function onViewportPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const panDrag = panDragRef.current;
    if (!panDrag || panDrag.pointerId !== event.pointerId) return;
    event.preventDefault();
    setPanValue({
      x: panDrag.originX + event.clientX - panDrag.startX,
      y: panDrag.originY + event.clientY - panDrag.startY,
    });
  }

  function stopViewportPan(event: ReactPointerEvent<HTMLDivElement>) {
    const panDrag = panDragRef.current;
    if (!panDrag || panDrag.pointerId !== event.pointerId) return;
    panDragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  return (
    <div
      ref={viewportRef}
      onPointerDown={onViewportPointerDown}
      onPointerMove={onViewportPointerMove}
      onPointerUp={stopViewportPan}
      onPointerCancel={stopViewportPan}
      className="relative min-h-[520px] cursor-grab overflow-hidden bg-ink/20 active:cursor-grabbing"
    >
      <div className="pointer-events-none sticky left-0 top-0 z-20 flex h-0 justify-end p-3">
        <div className="pointer-events-auto flex items-center gap-2">
          <div
            data-process-control
            className="flex items-center gap-1 rounded-full border border-white/10 bg-ink/85 p-1 shadow-card backdrop-blur-xl"
          >
            <button
              type="button"
              onClick={() => changeZoom(-ZOOM_STEP)}
              disabled={zoom <= MIN_ZOOM}
              className="h-7 w-7 rounded-full text-sm font-bold text-white/65 transition hover:bg-white/[0.06] hover:text-white disabled:cursor-not-allowed disabled:text-white/25"
              aria-label="Zoom out"
            >
              -
            </button>
            <button
              type="button"
              onClick={resetZoom}
              className="min-w-12 rounded-full px-2 py-1 text-xs font-bold text-white/60 transition hover:bg-white/[0.06] hover:text-white"
              aria-label="Reset zoom"
            >
              {Math.round(zoom * 100)}%
            </button>
            <button
              type="button"
              onClick={() => changeZoom(ZOOM_STEP)}
              disabled={zoom >= MAX_ZOOM}
              className="h-7 w-7 rounded-full text-sm font-bold text-white/65 transition hover:bg-white/[0.06] hover:text-white disabled:cursor-not-allowed disabled:text-white/25"
              aria-label="Zoom in"
            >
              +
            </button>
          </div>
          <button
            type="button"
            data-process-control
            onClick={onAddState}
            className="rounded-full border border-aqua/30 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua shadow-card transition hover:bg-aqua/15"
          >
            Add from palette
          </button>
        </div>
      </div>
      <div className="absolute inset-0">
        <div
          className="absolute left-0 top-0 bg-[radial-gradient(circle_at_1px_1px,rgba(255,255,255,0.10)_1px,transparent_0)] [background-size:24px_24px]"
          style={{
            width: canvasWidth,
            height: canvasHeight,
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
            transformOrigin: "top left",
          }}
        >
          <svg
            aria-hidden
            className="absolute inset-0 h-full w-full"
            viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}
          >
            {edges.map((edge) => {
              const from =
                positions[edge.from_state_id] ??
                initialPositions[edge.from_state_id];
              const to =
                positions[edge.to_state_id] ?? initialPositions[edge.to_state_id];
              if (!from || !to) return null;
              const x1 = from.x + NODE_WIDTH;
              const x2 = to.x;
              const y1 = from.y + NODE_HEIGHT / 2;
              const y2 = to.y + NODE_HEIGHT / 2;
              const mid = (x1 + x2) / 2;
              const d = `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`;
              const isSelected = edge.id === selectedTransitionId;
              const cond = edge.conditions[0]?.expression;
              const midX = (x1 + x2) / 2;
              const midY = (y1 + y2) / 2;
              return (
                <g
                  key={edge.id}
                  data-process-edge
                  style={{ pointerEvents: transitionsInteractive ? "auto" : "none" }}
                >
                  <path
                    d={d}
                    fill="none"
                    stroke="transparent"
                    strokeWidth="20"
                    strokeLinecap="round"
                    onPointerDown={(event) => {
                      if (!transitionsInteractive) return;
                      event.stopPropagation();
                    }}
                    onClick={(event) => {
                      if (!transitionsInteractive) return;
                      event.stopPropagation();
                      onSelectTransition(edge.id);
                    }}
                    className={
                      transitionsInteractive
                        ? "cursor-pointer"
                        : "pointer-events-none"
                    }
                  />
                  <path
                    d={d}
                    fill="none"
                    stroke={
                      isSelected
                        ? "rgba(0, 232, 220, 0.55)"
                        : "rgba(255,255,255,0.22)"
                    }
                    strokeWidth={isSelected ? 3 : 2}
                    markerEnd={`url(#${arrowMarkerId})`}
                    pointerEvents="none"
                  />
                  {cond ? (
                    <text
                      x={midX}
                      y={midY - 10}
                      textAnchor="middle"
                      className="fill-white/45"
                      style={{ fontSize: 9, pointerEvents: "none" }}
                    >
                      {cond.length > 28 ? `${cond.slice(0, 28)}…` : cond}
                    </text>
                  ) : null}
                  {edge.requires_human ? (
                    <g
                      transform={`translate(${midX - 9}, ${midY + 2})`}
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
                      <g transform="translate(2.5, 1.5)">
                        <circle cx="5.5" cy="4" r="2.2" fill="rgba(255,255,255,0.9)" />
                        <path
                          d="M2 12.5c.8-2.2 1.6-3.1 3.5-3.1s2.7.9 3.5 3.1"
                          fill="none"
                          stroke="rgba(255,255,255,0.9)"
                          strokeWidth="1.1"
                          strokeLinecap="round"
                        />
                      </g>
                    </g>
                  ) : null}
                </g>
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
          {process.states.map((state) => {
            const pos = positions[state.id] ?? initialPositions[state.id];
            return (
              <StateNode
                key={state.id}
                state={state}
                selected={state.id === selectedStateId}
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
    </div>
  );
}

function clampZoom(value: number) {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, Number(value.toFixed(2))));
}

function StateNode({
  state,
  selected,
  x,
  y,
  onPointerDown,
  onMouseDown,
  onClick,
}: {
  state: ApiProcessState;
  selected: boolean;
  x: number;
  y: number;
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onMouseDown: (event: ReactMouseEvent<HTMLDivElement>) => void;
  onClick: () => void;
}) {
  return (
    <div
      data-process-node
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
        "absolute block h-[76px] w-[210px] rounded-2xl border px-4 py-3 transition",
        "cursor-grab active:cursor-grabbing",
        selected
          ? "border-aqua/60 bg-aqua/[0.08] shadow-glow"
          : "border-white/10 bg-white/[0.035] hover:border-white/25 hover:bg-white/[0.06]",
      ].join(" ")}
    >
      <div className="min-w-0">
        <div className="truncate font-display text-base font-bold text-white">
          {state.name}
        </div>
        <div className="mt-1 truncate text-xs text-white/55">
          {state.specialist_name}
        </div>
      </div>
    </div>
  );
}
