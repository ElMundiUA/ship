"use client";

import {
  type DragEvent as ReactDragEvent,
  type PointerEvent as ReactPointerEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";

import type { ApiProcessGraph, ApiProcessList, ApiProcessNode } from "@/lib/api/client";

type Props = {
  processList: ApiProcessList;
  repoId?: string;
};

type Position = { x: number; y: number };
type DragState = {
  id: string;
  pointerId: number;
  offsetX: number;
  offsetY: number;
  moved: boolean;
};

const NODE_WIDTH = 226;
const NODE_HEIGHT = 132;
const CREATE_NODE_ID = "node-create-process";

export function ProcessGraphOverview({ processList, repoId }: Props) {
  const router = useRouter();
  const graph = useMemo(() => graphWithFallback(processList), [processList]);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const suppressClickRef = useRef(false);
  const [positions, setPositions] = useState<Record<string, Position>>(() =>
    Object.fromEntries(graph.nodes.map((node) => [node.id, { x: node.x, y: node.y }])),
  );
  const [flyInNodeId, setFlyInNodeId] = useState<string | null>(null);
  const nodes = useMemo(
    () =>
      [
        ...graph.nodes.map((node) => ({
          ...node,
          x: positions[node.id]?.x ?? node.x,
          y: positions[node.id]?.y ?? node.y,
        })),
        {
          id: CREATE_NODE_ID,
          process_id: "__create__",
          type: "process" as const,
          name: "New process",
          description: "Create another top-level workspace process.",
          parent_process_id: null,
          x: positions[CREATE_NODE_ID]?.x ?? 620,
          y: positions[CREATE_NODE_ID]?.y ?? 270,
          status: "ok" as const,
        },
      ],
    [graph.nodes, positions],
  );
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const width = Math.max(860, ...nodes.map((node) => node.x + NODE_WIDTH + 80));
  const height = Math.max(620, ...nodes.map((node) => node.y + NODE_HEIGHT + 110));
  const flyInNode = flyInNodeId ? nodesById.get(flyInNodeId) : null;
  const processCount = processList.processes.filter(
    (process) => !process.parent_process_id,
  ).length;

  useEffect(() => {
    setPositions(
      {
        ...Object.fromEntries(
          graph.nodes.map((node) => [node.id, { x: node.x, y: node.y }]),
        ),
        [CREATE_NODE_ID]: { x: 620, y: 270 },
      },
    );
  }, [graph.nodes]);

  useEffect(() => {
    function onPointerMove(event: PointerEvent) {
      const drag = dragRef.current;
      const viewport = viewportRef.current;
      if (!drag || drag.pointerId !== event.pointerId || !viewport) return;
      event.preventDefault();
      const rect = viewport.getBoundingClientRect();
      const next = {
        x: Math.max(20, event.clientX - rect.left - drag.offsetX),
        y: Math.max(20, event.clientY - rect.top - drag.offsetY),
      };
      drag.moved = true;
      setPositions((current) => ({ ...current, [drag.id]: next }));
    }

    function onPointerUp(event: PointerEvent) {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      suppressClickRef.current = drag.moved;
      dragRef.current = null;
    }

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
    };
  }, []);

  function startDrag(event: ReactPointerEvent<HTMLDivElement>, node: ApiProcessNode) {
    const viewport = viewportRef.current;
    if (!viewport) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    const rect = viewport.getBoundingClientRect();
    const position = positions[node.id] ?? { x: node.x, y: node.y };
    dragRef.current = {
      id: node.id,
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left - position.x,
      offsetY: event.clientY - rect.top - position.y,
      moved: false,
    };
  }

  function openNode(node: ApiProcessNode) {
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    if (node.id === CREATE_NODE_ID) {
      window.alert("New workspace process creation will open the process template picker.");
      return;
    }
    if (node.type === "workspace") return;
    setFlyInNodeId(node.id);
    const href = repoId
      ? `/process/${encodeURIComponent(node.process_id)}?repo=${encodeURIComponent(repoId)}`
      : `/process/${encodeURIComponent(node.process_id)}`;
    window.setTimeout(() => router.push(href), 1150);
  }

  function endNativeDrag(event: ReactDragEvent<HTMLDivElement>, node: ApiProcessNode) {
    const viewport = viewportRef.current;
    if (!viewport || event.clientX === 0 || event.clientY === 0) return;
    const rect = viewport.getBoundingClientRect();
    setPositions((current) => ({
      ...current,
      [node.id]: {
        x: Math.max(20, event.clientX - rect.left - NODE_WIDTH / 2),
        y: Math.max(20, event.clientY - rect.top - NODE_HEIGHT / 2),
      },
    }));
  }

  return (
    <div className="space-y-4">
      {flyInNode ? <ZoomPortal node={flyInNode} /> : null}
      <section className="rounded-[1.4rem] border border-white/10 bg-[#090f1c]/80 px-4 py-3 shadow-xl shadow-black/20">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-display text-xl font-semibold text-white">
                Workspace Map
              </h1>
              <span className="rounded-full border border-aqua/20 bg-aqua/[0.07] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-aqua/75">
                {processCount} process
              </span>
            </div>
            <p className="mt-1 max-w-2xl text-sm text-white/50">
              Top-level automation map. Open a process to enter its internal
              flow.
            </p>
          </div>
          <div className="flex items-center gap-2 text-xs">
            <span className="rounded-full border border-white/10 bg-white/[0.035] px-3 py-1.5 text-white/50">
              Drag nodes
            </span>
            <span className="rounded-full border border-aqua/20 bg-aqua/[0.07] px-3 py-1.5 font-semibold text-aqua/80">
              + New process
            </span>
          </div>
        </div>
      </section>

      <section className="relative overflow-hidden rounded-[2rem] border border-white/10 bg-[#030711] p-3 shadow-2xl shadow-black/40">
        <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_50%_40%,rgba(99,245,255,0.12),transparent_32%),radial-gradient(circle_at_18%_70%,rgba(168,85,247,0.12),transparent_28%)]" />
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(rgba(255,255,255,0.035)_1px,transparent_1px),linear-gradient(90deg,rgba(255,255,255,0.035)_1px,transparent_1px)] bg-[size:32px_32px] opacity-60" />
        <div
          ref={viewportRef}
          className="relative overflow-hidden rounded-[1.6rem] border border-white/10"
        >
        <div
          className={[
            "relative transition-transform duration-700",
            flyInNode ? "scale-[2.55] opacity-40 blur-[1px]" : "scale-100",
          ].join(" ")}
          style={{
            width,
            height,
            transformOrigin: flyInNode
              ? `${flyInNode.x + NODE_WIDTH / 2}px ${flyInNode.y + NODE_HEIGHT / 2}px`
              : "50% 50%",
          }}
        >
          <svg
            className="pointer-events-none absolute inset-0"
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            aria-hidden="true"
          >
            <defs>
              <marker
                id="process-arrow"
                markerWidth="10"
                markerHeight="10"
                refX="8"
                refY="3"
                orient="auto"
                markerUnits="strokeWidth"
              >
                <path d="M0,0 L0,6 L9,3 z" fill="rgba(99, 245, 255, 0.7)" />
              </marker>
              <filter id="edge-glow">
                <feGaussianBlur stdDeviation="3" result="coloredBlur" />
                <feMerge>
                  <feMergeNode in="coloredBlur" />
                  <feMergeNode in="SourceGraphic" />
                </feMerge>
              </filter>
            </defs>
            {graph.links.map((link) => {
              const from = link.from_node_id ? nodesById.get(link.from_node_id) : null;
              const to = link.to_node_id ? nodesById.get(link.to_node_id) : null;
              if (!from || !to) return null;
              const fromCenter = { x: from.x + NODE_WIDTH / 2, y: from.y + NODE_HEIGHT / 2 };
              const toCenter = { x: to.x + NODE_WIDTH / 2, y: to.y + NODE_HEIGHT / 2 };
              const horizontal = Math.abs(toCenter.x - fromCenter.x) >= Math.abs(toCenter.y - fromCenter.y);
              const fromX = horizontal
                ? fromCenter.x + (toCenter.x >= fromCenter.x ? NODE_WIDTH / 2 : -NODE_WIDTH / 2)
                : fromCenter.x;
              const fromY = horizontal
                ? fromCenter.y
                : fromCenter.y + (toCenter.y >= fromCenter.y ? NODE_HEIGHT / 2 : -NODE_HEIGHT / 2);
              const toX = horizontal
                ? toCenter.x + (toCenter.x >= fromCenter.x ? -NODE_WIDTH / 2 : NODE_WIDTH / 2)
                : toCenter.x;
              const toY = horizontal
                ? toCenter.y
                : toCenter.y + (toCenter.y >= fromCenter.y ? -NODE_HEIGHT / 2 : NODE_HEIGHT / 2);
              const midX = (fromX + toX) / 2;
              return (
                <g key={link.id}>
                  <path
                    d={`M ${fromX} ${fromY} C ${midX} ${fromY}, ${midX} ${toY}, ${toX} ${toY}`}
                    fill="none"
                    stroke="rgba(99, 245, 255, 0.16)"
                    strokeLinecap="round"
                    strokeWidth="14"
                    filter="url(#edge-glow)"
                  />
                  <path
                    d={`M ${fromX} ${fromY} C ${midX} ${fromY}, ${midX} ${toY}, ${toX} ${toY}`}
                    fill="none"
                    stroke="rgba(99, 245, 255, 0.82)"
                    strokeLinecap="round"
                    strokeWidth="3.5"
                    filter="url(#edge-glow)"
                    markerEnd="url(#process-arrow)"
                  />
                </g>
              );
            })}
          </svg>

          {graph.links.map((link) => {
            const from = link.from_node_id ? nodesById.get(link.from_node_id) : null;
            const to = link.to_node_id ? nodesById.get(link.to_node_id) : null;
            if (!from || !to) return null;
            const fromCenter = {
              x: from.x + NODE_WIDTH / 2,
              y: from.y + NODE_HEIGHT / 2,
            };
            const toCenter = {
              x: to.x + NODE_WIDTH / 2,
              y: to.y + NODE_HEIGHT / 2,
            };
            if (Math.abs(fromCenter.x - toCenter.x) > 32) return null;
            const top = Math.min(from.y, to.y) + NODE_HEIGHT - 4;
            const bottom = Math.max(from.y, to.y) + 4;
            return (
              <div
                key={`${link.id}-beam`}
                className="pointer-events-none absolute z-10 w-2 rounded-full bg-gradient-to-b from-aqua/85 via-aqua/45 to-aqua/15 shadow-[0_0_34px_rgba(99,245,255,0.7)]"
                style={{
                  left: fromCenter.x - 4,
                  top,
                  height: Math.max(36, bottom - top),
                }}
              />
            );
          })}

          {nodes.map((node) => (
            <ProcessNodeCard
              key={node.id}
              node={node}
              taskCount={
                processList.processes.find((process) => process.id === node.process_id)
                  ?.task_count ?? 0
              }
              onPointerDown={startDrag}
              onOpen={openNode}
              onNativeDragEnd={endNativeDrag}
            />
          ))}
        </div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2 border-t border-white/10 pt-3">
          {graph.links.map((link) => {
            const from = link.from_node_id ? nodesById.get(link.from_node_id) : null;
            const to = link.to_node_id ? nodesById.get(link.to_node_id) : null;
            return (
              <span
                key={link.id}
                className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-[11px] text-white/55"
              >
                {from?.name ?? link.from_process_id} → {to?.name ?? link.to_process_id}
                {link.label ? ` · ${link.label}` : ""}
              </span>
            );
          })}
        </div>
      </section>

      <section className="grid gap-3 md:grid-cols-3">
        {processList.processes
          .filter((process) => !process.parent_process_id)
          .map((process) => (
            <div
              key={process.id}
              className="rounded-2xl border border-white/10 bg-white/[0.035] p-4"
            >
              <p className="text-sm font-semibold text-white">{process.name}</p>
              <p className="mt-1 text-xs text-white/55">
                {process.description || "Workspace process template"}
              </p>
              <p className="mt-3 text-xs text-white/45">
                {process.state_count} states · {process.task_count} active tasks ·{" "}
                {process.blocked_count} blocked
              </p>
            </div>
          ))}
      </section>
    </div>
  );
}

function ZoomPortal({ node }: { node: ApiProcessNode }) {
  const fragments = [
    { label: "states.yml", x: -230, y: -130, rotate: -16, delay: 120 },
    { label: "handoffs.json", x: 250, y: -112, rotate: 14, delay: 180 },
    { label: "roles.md", x: -270, y: 114, rotate: 10, delay: 230 },
    { label: "schedule.cron", x: 220, y: 138, rotate: -12, delay: 280 },
    { label: "tracker.map", x: 0, y: 210, rotate: 4, delay: 330 },
  ];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-[#01040c]">
      <style jsx global>{`
        @keyframes process-tunnel-pulse {
          0% {
            transform: translate(-50%, -50%) scale(0.2);
            opacity: 0;
          }
          35% {
            opacity: 0.8;
          }
          100% {
            transform: translate(-50%, -50%) scale(2.4);
            opacity: 0;
          }
        }
        @keyframes process-core-dive {
          0% {
            transform: perspective(900px) translateZ(0) scale(1) rotateX(0deg);
            opacity: 1;
            filter: blur(0);
          }
          62% {
            transform: perspective(900px) translateZ(260px) scale(1.35) rotateX(3deg);
            opacity: 1;
            filter: blur(0);
          }
          100% {
            transform: perspective(900px) translateZ(640px) scale(3.2) rotateX(10deg);
            opacity: 0;
            filter: blur(10px);
          }
        }
        @keyframes process-fragment-burst {
          0% {
            transform: translate(-50%, -50%) scale(0.72) rotate(0deg);
            opacity: 0;
          }
          22% {
            opacity: 1;
          }
          74% {
            transform: translate(calc(-50% + var(--fx)), calc(-50% + var(--fy))) scale(1) rotate(var(--fr));
            opacity: 1;
          }
          100% {
            transform: translate(calc(-50% + var(--fx) * 1.35), calc(-50% + var(--fy) * 1.35)) scale(1.8) rotate(var(--fr));
            opacity: 0;
            filter: blur(8px);
          }
        }
        @keyframes process-scanline-drop {
          0% {
            transform: translateY(-100%);
            opacity: 0;
          }
          20% {
            opacity: 0.7;
          }
          100% {
            transform: translateY(100%);
            opacity: 0;
          }
        }
      `}</style>
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_center,rgba(99,245,255,0.18),rgba(1,4,12,0.28)_30%,rgba(1,4,12,1)_68%)]" />
      <div className="absolute inset-0 bg-[linear-gradient(rgba(99,245,255,0.05)_1px,transparent_1px),linear-gradient(90deg,rgba(99,245,255,0.045)_1px,transparent_1px)] bg-[size:48px_48px] opacity-60" />
      {[0, 1, 2, 3].map((index) => (
        <div
          key={index}
          className="absolute left-1/2 top-1/2 rounded-[42%] border border-aqua/20 shadow-[0_0_80px_rgba(99,245,255,0.20)]"
          style={{
            width: 220 + index * 120,
            height: 220 + index * 120,
            animation: `process-tunnel-pulse 1100ms ${index * 110}ms ease-out forwards`,
          }}
        />
      ))}
      <div
        className="absolute inset-x-0 top-0 h-1/2 bg-gradient-to-b from-aqua/15 via-transparent to-transparent"
        style={{ animation: "process-scanline-drop 920ms ease-out forwards" }}
      />

      <div className="relative h-[560px] w-[760px] max-w-[90vw]">
        <div
          className="absolute left-1/2 top-1/2 w-[420px] max-w-[76vw] rounded-[2rem] border border-aqua/40 bg-[linear-gradient(135deg,rgba(99,245,255,0.24),rgba(168,85,247,0.14),rgba(255,255,255,0.08))] p-8 shadow-[0_0_180px_rgba(99,245,255,0.38)]"
          style={{
            transform: "translate(-50%, -50%)",
            animation: "process-core-dive 980ms ease-in forwards",
          }}
        >
          <div className="text-[10px] font-bold uppercase tracking-[0.36em] text-aqua/80">
            Diving into process
          </div>
          <div className="mt-3 font-display text-4xl font-bold text-white">
            {node.name}
          </div>
          <p className="mt-3 max-w-md text-sm leading-6 text-white/65">
            Resolving internal states, handoffs, schedule, roles, and tracker
            contracts.
          </p>
          <div className="mt-6 grid grid-cols-5 gap-1">
            {Array.from({ length: 15 }).map((_, index) => (
              <span
                key={index}
                className="h-1 rounded-full bg-aqua/70 shadow-[0_0_18px_rgba(99,245,255,0.9)]"
                style={{ opacity: 1 - index * 0.045 }}
              />
            ))}
          </div>
        </div>

        {fragments.map((fragment) => (
          <div
            key={fragment.label}
            className="absolute left-1/2 top-1/2 w-36 rounded-2xl border border-white/15 bg-[#071222]/90 p-3 shadow-[0_0_60px_rgba(99,245,255,0.18)] backdrop-blur-xl"
            style={{
              ["--fx" as string]: `${fragment.x}px`,
              ["--fy" as string]: `${fragment.y}px`,
              ["--fr" as string]: `${fragment.rotate}deg`,
              animation: `process-fragment-burst 1040ms ${fragment.delay}ms ease-out forwards`,
              opacity: 0,
            }}
          >
            <div className="mb-2 h-1 w-10 rounded-full bg-aqua/70" />
            <div className="font-mono text-[11px] font-semibold text-white/85">
              {fragment.label}
            </div>
            <div className="mt-2 space-y-1">
              <div className="h-1 rounded-full bg-white/20" />
              <div className="h-1 w-3/4 rounded-full bg-white/15" />
              <div className="h-1 w-1/2 rounded-full bg-aqua/30" />
            </div>
          </div>
        ))}

        <div className="absolute inset-x-0 bottom-10 text-center text-[10px] font-bold uppercase tracking-[0.38em] text-aqua/65">
          Opening internal canvas
        </div>
      </div>
    </div>
  );
}

function ProcessNodeCard({
  node,
  taskCount,
  onPointerDown,
  onOpen,
  onNativeDragEnd,
}: {
  node: ApiProcessNode;
  taskCount: number;
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>, node: ApiProcessNode) => void;
  onOpen: (node: ApiProcessNode) => void;
  onNativeDragEnd: (event: ReactDragEvent<HTMLDivElement>, node: ApiProcessNode) => void;
}) {
  const isWorkspace = node.type === "workspace";
  const isCreate = node.id === CREATE_NODE_ID;
  if (isWorkspace) {
    return (
      <div
        role="button"
        tabIndex={0}
        draggable
        onPointerDown={(event) => onPointerDown(event, node)}
        onDragEnd={(event) => onNativeDragEnd(event, node)}
        className={[
          "group absolute block cursor-grab select-none rounded-[1.7rem] border border-aqua/40 p-5 shadow-2xl",
          "bg-[linear-gradient(135deg,rgba(99,245,255,0.18),rgba(99,245,255,0.06))] ring-1 ring-aqua/20 active:cursor-grabbing",
        ].join(" ")}
        style={{
          left: node.x,
          top: node.y,
          width: NODE_WIDTH,
          minHeight: NODE_HEIGHT,
          touchAction: "none",
        }}
      >
        <div className="pointer-events-none absolute -inset-4 rounded-[2rem] border border-aqua/15 opacity-50" />
        <div className="flex items-center justify-between gap-2">
          <span className="rounded-full border border-aqua/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-aqua/80">
            Workspace
          </span>
          <span className={healthClass(node.status)}>{node.status}</span>
        </div>
        <p className="mt-3 text-base font-semibold text-white">{node.name}</p>
        <p className="mt-1 text-xs leading-5 text-white/60">
          {node.description}
        </p>
        <p className="mt-3 text-xs text-aqua/75">Root orchestration node</p>
      </div>
    );
  }
  const isSubprocess = node.type === "subprocess";
  return (
    <div
      role="button"
      tabIndex={0}
      draggable
      onPointerDown={(event) => onPointerDown(event, node)}
      onDragEnd={(event) => onNativeDragEnd(event, node)}
      onClick={() => onOpen(node)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") onOpen(node);
      }}
      className={[
        "group absolute block cursor-grab select-none rounded-[1.35rem] border p-4 shadow-2xl transition active:cursor-grabbing",
        "hover:-translate-y-1 hover:border-aqua/50 hover:bg-white/[0.09]",
        isCreate
          ? "border-dashed border-aqua/35 bg-aqua/[0.045]"
          : isSubprocess
          ? "border-violet-300/25 bg-[linear-gradient(135deg,rgba(168,85,247,0.14),rgba(255,255,255,0.04))]"
          : "border-white/15 bg-[linear-gradient(135deg,rgba(255,255,255,0.11),rgba(255,255,255,0.04))]",
      ].join(" ")}
      style={{
        left: node.x,
        top: node.y,
        width: NODE_WIDTH,
        minHeight: NODE_HEIGHT,
        touchAction: "none",
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="rounded-full border border-white/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/50">
          {isCreate ? "Create" : isSubprocess ? "Subprocess" : "Process"}
        </span>
        <span className={healthClass(node.status)}>{node.status}</span>
      </div>
      <p className="mt-3 text-sm font-semibold text-white">{node.name}</p>
      <p className="mt-1 line-clamp-3 text-xs leading-5 text-white/55">
        {node.description || "Open editor"}
      </p>
      <div className="mt-3 flex items-center justify-between text-xs text-white/45">
        <span>{isCreate ? "Template picker" : `${taskCount} active tasks`}</span>
        <span className="text-aqua/75 transition group-hover:text-aqua">
          {isCreate ? "Create" : "Enter"}
        </span>
      </div>
    </div>
  );
}

function graphWithFallback(processList: ApiProcessList): ApiProcessGraph {
  if (processList.process_graph.nodes.length > 0) return processList.process_graph;
  const processNodes = processList.processes
    .filter((process) => !process.parent_process_id)
    .map((process, index) => ({
    id: `node-${process.id}`,
    process_id: process.id,
    type: process.node_type ?? "process",
    name: process.name,
    description: process.description ?? "",
    parent_process_id: process.parent_process_id ?? null,
    template_id: process.template_id ?? null,
    x: 80 + index * 260,
    y: process.parent_process_id ? 330 : 40,
    status: process.health,
    }));
  return {
    nodes: [
      {
        id: "node-workspace",
        process_id: "workspace",
        type: "workspace",
        name: "Workspace",
        description: "Root orchestration map for this workspace.",
        parent_process_id: null,
        x: 330,
        y: 270,
        status: "ok",
      },
      ...processNodes,
    ],
    links: [
      {
        id: "workspace-to-development",
        from_process_id: "workspace",
        from_state_id: null,
        from_node_id: "node-workspace",
        to_process_id: processList.primary_process_id,
        to_state_id: null,
        to_node_id: `node-${processList.primary_process_id}`,
        type: "handoff",
        conditions: [],
        label: "Development process",
      },
      ...processList.process_graph.links,
    ],
  };
}

function healthClass(health: ApiProcessNode["status"]) {
  if (health === "degraded") {
    return "rounded-full bg-amber-300/10 px-2 py-1 text-[10px] font-semibold text-amber-200";
  }
  if (health === "failed") {
    return "rounded-full bg-coral/10 px-2 py-1 text-[10px] font-semibold text-coral";
  }
  return "rounded-full bg-aqua/10 px-2 py-1 text-[10px] font-semibold text-aqua";
}
