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
  useReactFlow,
  type Edge as RFEdge,
  type Node as RFNode,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { useRouter } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import type { ApiProcessGraph, ApiProcessList, ApiProcessNode } from "@/lib/api/client";

const NODE_WIDTH = 230;
const NODE_HEIGHT = 134;
const CREATE_NODE_ID = "node-create-process";

type ProcessNodeData = {
  node: ApiProcessNode;
  taskCount: number;
  /** True when the operator just clicked this tile and the camera is
   *  flying to it; tile scales up + glows so the destination is obvious. */
  isZoomTarget?: boolean;
  /** True when *some other* tile is being zoomed to — this tile fades. */
  isZoomBackdrop?: boolean;
};

type ProcessRFNode = RFNode<ProcessNodeData, "process">;

/**
 * Top-level workspace map at /process — every workspace process appears
 * as a tile, with the workspace itself as the root. Click on a process
 * tile triggers an animated zoom-in (React Flow's fitView with duration)
 * before navigating into the inner state graph at /process/[processId].
 *
 * The cinematic effect is two phases:
 *   1. fitView({ nodes: [target], duration: 900 }) — the camera dives
 *      to the clicked tile.
 *   2. After the tween settles (~950ms), router.push() to the detail
 *      route. The page transition feels continuous because the camera
 *      is already on top of the destination tile.
 */
export function ProcessGraphOverview({
  processList,
  repoId,
}: {
  processList: ApiProcessList;
  repoId?: string;
}) {
  return (
    <ReactFlowProvider>
      <OverviewInner processList={processList} repoId={repoId} />
    </ReactFlowProvider>
  );
}

function OverviewInner({
  processList,
  repoId,
}: {
  processList: ApiProcessList;
  repoId?: string;
}) {
  const router = useRouter();
  const flow = useReactFlow();
  const [zoomingTo, setZoomingTo] = useState<string | null>(null);

  const graph = useMemo(() => graphWithFallback(processList), [processList]);

  const nodes = useMemo<ProcessRFNode[]>(() => {
    const live: ProcessRFNode[] = graph.nodes.map((node) => ({
      id: node.id,
      type: "process",
      position: { x: node.x, y: node.y },
      data: {
        node,
        taskCount:
          processList.processes.find((p) => p.id === node.process_id)
            ?.task_count ?? 0,
        isZoomTarget: zoomingTo === node.id,
        isZoomBackdrop: zoomingTo !== null && zoomingTo !== node.id,
      },
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      draggable: node.type !== "workspace",
    }));
    // Synthetic "+ New process" tile — kept as a draggable React-Flow
    // node so it lives in the same grid as real processes instead of
    // floating off-canvas.
    live.push({
      id: CREATE_NODE_ID,
      type: "process",
      position: { x: 620, y: 270 },
      data: {
        node: {
          id: CREATE_NODE_ID,
          process_id: "__create__",
          type: "process",
          name: "New process",
          description: "Create another top-level workspace process.",
          parent_process_id: null,
          x: 620,
          y: 270,
          status: "ok",
        },
        taskCount: 0,
        isZoomTarget: false,
        isZoomBackdrop: zoomingTo !== null,
      },
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      draggable: true,
    });
    return live;
  }, [graph.nodes, processList.processes, zoomingTo]);

  const edges = useMemo<RFEdge[]>(
    () =>
      graph.links
        .filter((l) => l.from_node_id && l.to_node_id)
        .map((l) => ({
          id: l.id,
          source: l.from_node_id as string,
          target: l.to_node_id as string,
          type: "smoothstep",
          animated: false,
          style: { stroke: "rgba(207,169,107,0.45)", strokeWidth: 2 },
        })),
    [graph.links],
  );

  const onNodeClick = useCallback(
    async (_event: unknown, node: RFNode) => {
      const data = node.data as ProcessNodeData | undefined;
      if (!data) return;
      const apiNode = data.node;
      if (apiNode.type === "workspace") return;
      if (apiNode.id === CREATE_NODE_ID) {
        window.alert("New workspace process — template picker coming soon.");
        return;
      }
      // Cinematic zoom: mark the clicked tile (CSS scales it up,
      // dims the rest), dive the camera onto it via fitView, await
      // the tween, then route. fitView() in @xyflow/react v12 returns
      // a Promise that resolves on animation end, which is what makes
      // the timing feel like one continuous motion instead of two
      // separately-fired phases (the old setTimeout could fire before
      // or after the actual tween — visibly desynced).
      if (zoomingTo) return;
      setZoomingTo(apiNode.id);
      const href = repoId
        ? `/process/${encodeURIComponent(apiNode.process_id)}?repo=${encodeURIComponent(repoId)}`
        : `/process/${encodeURIComponent(apiNode.process_id)}`;
      try {
        await flow.fitView({
          nodes: [{ id: apiNode.id }],
          duration: 1100,
          padding: 0.02,
          maxZoom: 3,
        });
      } catch {
        // fitView may throw if the node measurement isn't ready yet —
        // route anyway so click never feels dead.
      }
      router.push(href);
    },
    [flow, repoId, router, zoomingTo],
  );

  const nodeTypes = useMemo(() => ({ process: ProcessTileNode }), []);
  const processCount = processList.processes.filter(
    (p) => !p.parent_process_id,
  ).length;

  return (
    <div className="space-y-4">
      <section className="rounded-[1.4rem] border border-white/10 bg-[#090f1c]/80 px-4 py-3 shadow-xl shadow-black/20">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-display text-xl font-semibold text-white">
                Workspace Map
              </h1>
              <span className="rounded-full border border-aqua/20 bg-aqua/[0.07] px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-aqua/75">
                {processCount} process{processCount === 1 ? "" : "es"}
              </span>
            </div>
            <p className="mt-1 max-w-2xl text-sm text-white/50">
              Top-level automation map. Click a process to zoom into its internal flow.
            </p>
          </div>
        </div>
      </section>

      <section
        className={[
          "relative h-[640px] overflow-hidden rounded-[2rem] border border-white/10 bg-[#030711] shadow-2xl shadow-black/40 transition-opacity duration-300",
          zoomingTo ? "opacity-90" : "opacity-100",
        ].join(" ")}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodeClick={onNodeClick}
          fitView
          fitViewOptions={{ padding: 0.18, maxZoom: 1.1, minZoom: 0.45 }}
          minZoom={0.25}
          maxZoom={2.5}
          proOptions={{ hideAttribution: true }}
          panOnDrag
          zoomOnScroll
          zoomOnPinch
          nodesConnectable={false}
          nodesDraggable
          elementsSelectable={false}
          selectNodesOnDrag={false}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={32}
            size={1}
            color="rgba(207, 169, 107, 0.16)"
          />
          <Controls
            className="!rounded-2xl !border !border-white/10 !bg-black/65 !text-white"
            showInteractive={false}
          />
          <MiniMap
            className="!rounded-xl !border !border-white/10 !bg-black/60"
            maskColor="rgba(3, 7, 17, 0.55)"
            nodeColor={(node) => {
              const data = node.data as ProcessNodeData | undefined;
              if (!data) return "rgba(255,255,255,0.18)";
              if (data.node.type === "workspace") return "rgba(207,169,107,0.85)";
              if (data.node.id === CREATE_NODE_ID) return "rgba(207,169,107,0.45)";
              return "rgba(255,255,255,0.32)";
            }}
            pannable
            zoomable
          />
        </ReactFlow>
      </section>
    </div>
  );
}

function ProcessTileNode({ data }: NodeProps<ProcessRFNode>) {
  const { node, taskCount, isZoomTarget, isZoomBackdrop } = data;
  const isWorkspace = node.type === "workspace";
  const isCreate = node.id === CREATE_NODE_ID;
  const isSubprocess = node.type === "subprocess";
  return (
    <div
      style={{
        width: NODE_WIDTH,
        minHeight: NODE_HEIGHT,
        transition: "transform 1100ms cubic-bezier(0.22, 1, 0.36, 1), opacity 600ms, box-shadow 600ms",
        // The zoomed-to tile pushes forward — small scale-up combined with
        // a champagne glow telegraphs "this is where the camera is going".
        // Other tiles fade so the destination is the only thing that reads.
        transform: isZoomTarget ? "scale(1.06)" : "scale(1)",
        opacity: isZoomBackdrop ? 0.18 : 1,
        boxShadow: isZoomTarget
          ? "0 0 64px rgba(207, 169, 107, 0.55), 0 0 0 2px rgba(207, 169, 107, 0.6)"
          : undefined,
      }}
      className={[
        "group cursor-pointer rounded-[1.5rem] border p-4 shadow-2xl",
        "hover:-translate-y-0.5 hover:border-aqua/55 hover:bg-white/[0.08]",
        isWorkspace
          ? "border-aqua/40 bg-[linear-gradient(135deg,rgba(207,169,107,0.18),rgba(207,169,107,0.06))] ring-1 ring-aqua/20"
          : isCreate
            ? "border-dashed border-aqua/35 bg-aqua/[0.045]"
            : isSubprocess
              ? "border-violet-300/25 bg-[linear-gradient(135deg,rgba(168,85,247,0.14),rgba(255,255,255,0.04))]"
              : "border-white/15 bg-[linear-gradient(135deg,rgba(255,255,255,0.11),rgba(255,255,255,0.04))]",
      ].join(" ")}
    >
      <Handle
        type="target"
        position={RFPosition.Left}
        className="!h-1.5 !w-1.5 !border-aqua/30 !bg-aqua/30"
      />
      <div className="flex items-center justify-between gap-2">
        <span
          className={[
            "rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]",
            isWorkspace
              ? "border-aqua/30 text-aqua/85"
              : "border-white/10 text-white/50",
          ].join(" ")}
        >
          {isWorkspace
            ? "Workspace"
            : isCreate
              ? "Create"
              : isSubprocess
                ? "Subprocess"
                : "Process"}
        </span>
        <span className={healthClass(node.status)}>{node.status}</span>
      </div>
      <p className="mt-3 text-sm font-semibold text-white">{node.name}</p>
      <p className="mt-1 line-clamp-3 text-xs leading-5 text-white/55">
        {node.description || (isCreate ? "Open template picker" : "Open editor")}
      </p>
      {!isWorkspace && (
        <div className="mt-3 flex items-center justify-between text-xs text-white/45">
          <span>
            {isCreate ? "Template picker" : `${taskCount} active task${taskCount === 1 ? "" : "s"}`}
          </span>
          <span className="text-aqua/75 transition group-hover:text-aqua">
            {isCreate ? "Create" : "Enter →"}
          </span>
        </div>
      )}
      <Handle
        type="source"
        position={RFPosition.Right}
        className="!h-1.5 !w-1.5 !border-aqua/30 !bg-aqua/30"
      />
    </div>
  );
}

function healthClass(status: string) {
  if (status === "ok") {
    return "rounded-full bg-emerald-400/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-emerald-300";
  }
  if (status === "warning") {
    return "rounded-full bg-amber-400/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-amber-200";
  }
  return "rounded-full bg-coral/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-coral";
}

function graphWithFallback(processList: ApiProcessList): ApiProcessGraph {
  // Backend already returns a populated graph in real workspaces; this
  // fallback covers the brand-new workspace where ``processes`` is empty
  // so the canvas isn't completely blank.
  if (processList.process_graph) return processList.process_graph;
  return { nodes: [], links: [] };
}
