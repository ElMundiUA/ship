"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent as ReactDragEvent,
} from "react";

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

// Re-exported so existing import sites keep working with the older
// React Flow signature. Layout positions are no longer used by the
// editor — stages live wherever their canonical ``state`` puts them
// — but we keep the prop wired so legacy callers still receive a
// well-formed map.
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
  backlog: "rgba(255,255,255,0.05)",
  planning: "rgba(99, 200, 255, 0.10)",
  executing: "rgba(207, 169, 107, 0.10)",
  reviewing: "rgba(168, 85, 247, 0.10)",
  awaiting_input: "rgba(255, 196, 87, 0.10)",
  blocked: "rgba(244, 114, 114, 0.10)",
  closed: "rgba(120, 200, 140, 0.10)",
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

/**
 * Process FSM editor — CSS Grid swim-lanes with an SVG overlay for
 * transition arrows.
 *
 * Replaces the React Flow canvas. The 7 canonical lifecycle states
 * (backlog / planning / executing / reviewing / awaiting_input /
 * blocked / closed) become 7 grid columns; stage cards live inside
 * their state's column. Drag a card across columns → fires
 * onStageStateChange. SVG overlay draws bezier arrows between cards;
 * a ResizeObserver recomputes paths whenever the layout shifts.
 *
 * This is intentionally HTML/CSS-first: each lane is just a column,
 * each stage just a button, each arrow just a path. No coordinate
 * math, no canvas pan/zoom — the FSM fits the viewport, and "stage
 * stuck in the wrong lane" can't happen because the lane IS the
 * state field.
 */
export function ProcessCanvasEditor({
  process,
  selectedStateId,
  selectedTransitionId,
  onSelectState,
  onSelectTransition,
  onAddState,
  onPositionsChange: _onPositionsChange,
  onStageStateChange,
}: {
  process: ApiProcess;
  selectedStateId?: string;
  selectedTransitionId?: string | null;
  onSelectState: (stateId: string) => void;
  onSelectTransition: (transitionId: string) => void;
  onAddState: () => void;
  /** Kept for backward compatibility — unused by the grid editor. */
  onPositionsChange?: (positions: Record<string, Position>) => void;
  onStageStateChange?: (stageId: string, nextState: CanonicalState) => void;
}) {
  // Group stages by their canonical state. Stage order within a lane
  // follows the source order in process.states so the operator's
  // intent (which stage comes first in the YAML) is honoured visually.
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

  // Refs for every stage card so the SVG overlay can compute path
  // endpoints in container coordinates.
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cardRefs = useRef<Map<string, HTMLElement>>(new Map());
  // Bumping this token forces the SVG overlay to re-measure rects on
  // the next paint. Drag-end / resize / layout changes all bump it.
  const [layoutToken, setLayoutToken] = useState(0);
  const [dragOverLane, setDragOverLane] = useState<CanonicalState | null>(null);

  // Re-measure when the process states or transitions change.
  useEffect(() => {
    setLayoutToken((t) => t + 1);
  }, [process.states, process.transitions]);

  // Re-measure on container resize so arrows track when the viewport
  // changes width or content reflows.
  useEffect(() => {
    const container = containerRef.current;
    if (!container || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setLayoutToken((t) => t + 1));
    ro.observe(container);
    return () => ro.disconnect();
  }, []);

  const setCardRef = useCallback((id: string, node: HTMLElement | null) => {
    if (node) cardRefs.current.set(id, node);
    else cardRefs.current.delete(id);
  }, []);

  function handleDropOnLane(
    event: ReactDragEvent<HTMLDivElement>,
    lane: CanonicalState,
  ) {
    event.preventDefault();
    setDragOverLane(null);
    const stageId = event.dataTransfer.getData(DRAG_STAGE_MIME);
    if (!stageId) return;
    const stage = process.states.find((s) => s.id === stageId);
    if (!stage || stage.state === lane) return;
    onStageStateChange?.(stageId, lane);
    setLayoutToken((t) => t + 1);
  }

  return (
    <div
      ref={containerRef}
      className="relative h-[720px] min-h-[560px] overflow-hidden bg-[#040814]"
    >
      <div className="pointer-events-none absolute right-3 top-3 z-30">
        <button
          type="button"
          onClick={onAddState}
          className="pointer-events-auto rounded-full border border-aqua/35 bg-aqua/15 px-3 py-1.5 text-xs font-semibold text-aqua shadow-lg shadow-aqua/5 transition hover:bg-aqua/25"
        >
          + Add stage
        </button>
      </div>

      {/* Scroll wraps the swim-lane grid so wider FSMs (more columns
       * than fit at default viewport) scroll horizontally without
       * losing the lane headers. */}
      <div className="h-full overflow-auto px-4 pb-6 pt-4">
        <div
          className="relative grid gap-3"
          style={{
            gridTemplateColumns: `repeat(${CANONICAL_STATES.length}, minmax(220px, 1fr))`,
          }}
        >
          {CANONICAL_STATES.map((lane) => (
            <LaneColumn
              key={lane}
              lane={lane}
              stages={stagesByLane.get(lane) ?? []}
              selectedStageId={selectedStateId}
              isDropTarget={dragOverLane === lane}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
                setDragOverLane(lane);
              }}
              onDragLeave={() => setDragOverLane(null)}
              onDrop={(event) => handleDropOnLane(event, lane)}
              onSelectStage={onSelectState}
              setCardRef={setCardRef}
            />
          ))}

          {/* SVG arrows overlay positioned over the grid. Pointer events
           * pass through except where a path explicitly grabs them. */}
          <ArrowsOverlay
            containerRef={containerRef}
            cardRefs={cardRefs}
            transitions={process.transitions}
            selectedTransitionId={selectedTransitionId}
            onSelectTransition={onSelectTransition}
            layoutToken={layoutToken}
          />
        </div>
      </div>
    </div>
  );
}

function LaneColumn({
  lane,
  stages,
  selectedStageId,
  isDropTarget,
  onDragOver,
  onDragLeave,
  onDrop,
  onSelectStage,
  setCardRef,
}: {
  lane: CanonicalState;
  stages: ApiProcessState[];
  selectedStageId: string | undefined;
  isDropTarget: boolean;
  onDragOver: (event: ReactDragEvent<HTMLDivElement>) => void;
  onDragLeave: () => void;
  onDrop: (event: ReactDragEvent<HTMLDivElement>) => void;
  onSelectStage: (stageId: string) => void;
  setCardRef: (id: string, node: HTMLElement | null) => void;
}) {
  return (
    <div
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={[
        "flex min-h-full flex-col gap-2 rounded-xl border p-2 transition",
        isDropTarget
          ? "border-aqua/55 bg-aqua/[0.08] shadow-[inset_0_0_0_1px_rgba(207,169,107,0.4)]"
          : "border-white/8",
      ].join(" ")}
      style={{ background: isDropTarget ? undefined : LANE_TINT[lane] }}
    >
      <div className="sticky top-0 z-10 -mx-2 -mt-2 mb-1 rounded-t-xl border-b border-white/5 bg-[#040814]/95 px-3 py-2 backdrop-blur">
        <div className="flex items-center gap-2">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ background: LANE_DOT[lane] }}
            aria-hidden
          />
          <span className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/65">
            {LANE_LABEL[lane]}
          </span>
          {stages.length > 0 && (
            <span className="ml-auto text-[10px] font-semibold text-white/35">
              {stages.length}
            </span>
          )}
        </div>
        <div className="mt-0.5 line-clamp-1 text-[10px] text-white/35">
          {LANE_HINT[lane]}
        </div>
      </div>

      {stages.length === 0 ? (
        <div className="grid flex-1 place-items-center rounded-lg border border-dashed border-white/10 px-2 py-4 text-center text-[10px] leading-snug text-white/30">
          No stages
          <br />
          <span className="text-white/20">drop one here</span>
        </div>
      ) : (
        stages.map((stage) => (
          <StageCard
            key={stage.id}
            stage={stage}
            lane={lane}
            selected={stage.id === selectedStageId}
            onClick={() => onSelectStage(stage.id)}
            registerRef={setCardRef}
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
  onClick,
  registerRef,
}: {
  stage: ApiProcessState;
  lane: CanonicalState;
  selected: boolean;
  onClick: () => void;
  registerRef: (id: string, node: HTMLElement | null) => void;
}) {
  return (
    <button
      ref={(node) => registerRef(stage.id, node)}
      type="button"
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData(DRAG_STAGE_MIME, stage.id);
        event.dataTransfer.effectAllowed = "move";
      }}
      onClick={onClick}
      data-stage-id={stage.id}
      className={[
        "rounded-xl border px-3 py-2 text-left shadow transition",
        "cursor-grab active:cursor-grabbing focus:outline-none focus-visible:ring-2 focus-visible:ring-aqua/60",
        selected
          ? "border-aqua/70 bg-[linear-gradient(135deg,rgba(207,169,107,0.20),rgba(207,169,107,0.05))] shadow-aqua/30"
          : "border-white/15 bg-[linear-gradient(135deg,rgba(255,255,255,0.10),rgba(255,255,255,0.03))] hover:border-aqua/40",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <div className="truncate font-display text-sm font-bold text-white">
            {stage.name}
          </div>
          <div className="mt-0.5 truncate text-[11px] text-white/55">
            {stage.specialist_name}
          </div>
        </div>
        <span
          className="mt-0.5 h-2 w-2 shrink-0 rounded-full"
          style={{
            background: LANE_DOT[lane],
            boxShadow: `0 0 12px ${LANE_DOT[lane]}`,
          }}
          aria-hidden
        />
      </div>
    </button>
  );
}

function actorColor(actor: TransitionActor | undefined): string {
  if (actor === "user") return "rgba(255, 196, 87, 0.85)";
  if (actor === "either") return "rgba(207, 169, 107, 0.7)";
  return "rgba(207, 169, 107, 0.55)";
}

function actorLabel(actor: TransitionActor | undefined): string {
  if (actor === "user") return "user";
  if (actor === "either") return "either";
  return "agent";
}

/**
 * SVG overlay drawing transition arrows between stage cards.
 *
 * Reads the per-card refs registered by StageCard, computes each
 * card's rect in container-relative coordinates, and emits a smooth
 * bezier between source.right-centre → target.left-centre. Click on
 * the path fires onSelectTransition. The overlay re-renders on every
 * layoutToken bump (drag-end, resize, process change).
 *
 * Pointer-events: the SVG container is pointer-events: none so it
 * doesn't eat clicks meant for cards underneath; each path opts back
 * in via ``pointer-events: stroke`` so clicks land on the arrow.
 */
function ArrowsOverlay({
  containerRef,
  cardRefs,
  transitions,
  selectedTransitionId,
  onSelectTransition,
  layoutToken,
}: {
  containerRef: React.RefObject<HTMLDivElement | null>;
  cardRefs: React.RefObject<Map<string, HTMLElement>>;
  transitions: ApiProcessTransition[];
  selectedTransitionId: string | null | undefined;
  onSelectTransition: (transitionId: string) => void;
  layoutToken: number;
}) {
  const [paths, setPaths] = useState<
    Array<{
      id: string;
      d: string;
      labelX: number;
      labelY: number;
      stroke: string;
      label: string;
      selected: boolean;
      condition?: string;
    }>
  >([]);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const containerRect = container.getBoundingClientRect();
    const next: typeof paths = [];
    for (const t of transitions) {
      const fromEl = cardRefs.current?.get(t.from_state_id);
      const toEl = cardRefs.current?.get(t.to_state_id);
      if (!fromEl || !toEl) continue;
      const fromRect = fromEl.getBoundingClientRect();
      const toRect = toEl.getBoundingClientRect();
      // Source: right-middle of card. Target: left-middle of card.
      // Coordinates are in container scroll-content space — subtract
      // container's left/top, add the container's scrollLeft/scrollTop
      // so the SVG (which sits in the same scrolling context) lines up.
      const sx = fromRect.right - containerRect.left + container.scrollLeft;
      const sy =
        fromRect.top + fromRect.height / 2 - containerRect.top + container.scrollTop;
      const tx = toRect.left - containerRect.left + container.scrollLeft;
      const ty =
        toRect.top + toRect.height / 2 - containerRect.top + container.scrollTop;
      // Smooth bezier with horizontal control offset proportional to
      // the gap. Minimum 60px control offset so adjacent-cell arrows
      // still bow gently instead of going straight.
      const offset = Math.max(60, Math.abs(tx - sx) * 0.4);
      const d = `M ${sx} ${sy} C ${sx + offset} ${sy}, ${tx - offset} ${ty}, ${tx} ${ty}`;
      next.push({
        id: t.id,
        d,
        labelX: (sx + tx) / 2,
        labelY: (sy + ty) / 2 - 8,
        stroke: actorColor(t.trigger_actor),
        label: actorLabel(t.trigger_actor),
        selected: t.id === selectedTransitionId,
        condition: t.conditions[0]?.expression,
      });
    }
    setPaths(next);
  }, [
    containerRef,
    cardRefs,
    transitions,
    selectedTransitionId,
    layoutToken,
  ]);

  return (
    <svg
      className="pointer-events-none absolute inset-0 z-20 h-full w-full"
      style={{ overflow: "visible" }}
      aria-hidden
    >
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
      {paths.map((p) => (
        <g
          key={p.id}
          onClick={(event) => {
            event.stopPropagation();
            onSelectTransition(p.id);
          }}
          style={{ pointerEvents: "auto", cursor: "pointer" }}
        >
          {/* Wide invisible hit target so clicks land on thin edges. */}
          <path
            d={p.d}
            fill="none"
            stroke="transparent"
            strokeWidth={20}
            pointerEvents="stroke"
          />
          <path
            d={p.d}
            fill="none"
            stroke={p.stroke}
            strokeWidth={p.selected ? 3 : 2}
            markerEnd="url(#rf-arrow-actor)"
            pointerEvents="stroke"
          />
          {p.condition ? (
            <text
              x={p.labelX}
              y={p.labelY}
              textAnchor="middle"
              className="fill-white/55"
              style={{ fontSize: 10, pointerEvents: "none" }}
            >
              {p.condition.length > 28
                ? `${p.condition.slice(0, 28)}…`
                : p.condition}
            </text>
          ) : null}
          <text
            x={p.labelX}
            y={p.labelY + 12}
            textAnchor="middle"
            style={{
              fontSize: 9,
              pointerEvents: "none",
              letterSpacing: "0.16em",
              textTransform: "uppercase",
              fontWeight: 700,
            }}
            fill={p.stroke}
          >
            {p.label}
          </text>
        </g>
      ))}
    </svg>
  );
}
