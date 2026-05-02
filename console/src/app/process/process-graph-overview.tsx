"use client";

import { useRouter } from "next/navigation";
import { useMemo } from "react";

import type { ApiProcessList, ApiProcessNode } from "@/lib/api/client";

const CREATE_TILE_ID = "tile-create-process";

/**
 * Workspace map at /process — a responsive grid of process tiles.
 *
 * Replaces the previous React Flow canvas. The "cinematic zoom"
 * drill-down was nice in theory but didn't actually work in practice
 * (timing was off, fitView raced router.push, the destination flickered),
 * and a card grid is what every other workspace surface in the console
 * already uses (Repos / Knowledge / Inbox), so operators see one
 * consistent visual model.
 *
 * Click on any process tile → router.push to /process/[processId]
 * with optional ?repo= preserved. Hover lift gives the visual feedback
 * the zoom-in used to provide.
 */
export function ProcessGraphOverview({
  processList,
  repoId,
}: {
  processList: ApiProcessList;
  repoId?: string;
}) {
  const router = useRouter();

  // Real top-level processes from the projection. Subprocesses (with a
  // parent_process_id) live INSIDE their parent and aren't surfaced as
  // standalone tiles here — that's a deliberate choice; if we want
  // subprocess hover-cards we add them as nested tiles later.
  const tiles = useMemo<ApiProcessNode[]>(() => {
    const graph = processList.process_graph;
    if (!graph) return [];
    return graph.nodes.filter((node) => node.type !== "subprocess");
  }, [processList.process_graph]);

  const taskCountById = useMemo(() => {
    const map = new Map<string, number>();
    for (const p of processList.processes) {
      map.set(p.id, p.task_count);
    }
    return map;
  }, [processList.processes]);

  const processCount = processList.processes.filter(
    (p) => !p.parent_process_id,
  ).length;

  function openProcess(node: ApiProcessNode) {
    if (node.type === "workspace") return;
    const href = repoId
      ? `/process/${encodeURIComponent(node.process_id)}?repo=${encodeURIComponent(repoId)}`
      : `/process/${encodeURIComponent(node.process_id)}`;
    router.push(href);
  }

  function openCreatePicker() {
    window.alert("New workspace process — template picker coming soon.");
  }

  return (
    <div className="space-y-4">
      <section className="rounded-2xl border border-white/10 bg-[#090f1c]/80 px-4 py-3 shadow-xl shadow-black/20">
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
              Top-level automation map. Click a process to open its editor.
            </p>
          </div>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {tiles.map((node) => (
          <ProcessTile
            key={node.id}
            node={node}
            taskCount={taskCountById.get(node.process_id) ?? 0}
            onOpen={() => openProcess(node)}
          />
        ))}
        <CreateProcessTile onOpen={openCreatePicker} />
      </section>
    </div>
  );
}

function ProcessTile({
  node,
  taskCount,
  onOpen,
}: {
  node: ApiProcessNode;
  taskCount: number;
  onOpen: () => void;
}) {
  const isWorkspace = node.type === "workspace";
  const isSubprocess = node.type === "subprocess";
  const tone =
    isWorkspace
      ? "border-aqua/40 bg-[linear-gradient(135deg,rgba(207,169,107,0.18),rgba(207,169,107,0.04))] ring-1 ring-aqua/20"
      : isSubprocess
        ? "border-violet-300/25 bg-[linear-gradient(135deg,rgba(168,85,247,0.14),rgba(255,255,255,0.04))]"
        : "border-white/15 bg-[linear-gradient(135deg,rgba(255,255,255,0.10),rgba(255,255,255,0.03))]";
  return (
    <button
      type="button"
      onClick={onOpen}
      disabled={isWorkspace}
      className={[
        "group relative overflow-hidden rounded-2xl border p-5 text-left shadow-2xl transition",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-aqua/60",
        isWorkspace
          ? "cursor-default"
          : "hover:-translate-y-0.5 hover:border-aqua/55 hover:shadow-aqua/10",
        tone,
      ].join(" ")}
    >
      <div className="flex items-center justify-between gap-2">
        <span
          className={[
            "rounded-full border px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em]",
            isWorkspace
              ? "border-aqua/30 text-aqua/85"
              : "border-white/10 text-white/50",
          ].join(" ")}
        >
          {isWorkspace ? "Workspace" : isSubprocess ? "Subprocess" : "Process"}
        </span>
        <HealthChip status={node.status} />
      </div>
      <p className="mt-3 text-sm font-semibold text-white">{node.name}</p>
      <p className="mt-1 line-clamp-3 text-xs leading-5 text-white/55">
        {node.description || "Open editor"}
      </p>
      {!isWorkspace && (
        <div className="mt-3 flex items-center justify-between text-xs text-white/45">
          <span>
            {taskCount} active task{taskCount === 1 ? "" : "s"}
          </span>
          <span className="text-aqua/75 transition group-hover:text-aqua">
            Open editor →
          </span>
        </div>
      )}
    </button>
  );
}

function CreateProcessTile({ onOpen }: { onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex flex-col items-start rounded-2xl border border-dashed border-aqua/35 bg-aqua/[0.04] p-5 text-left transition hover:-translate-y-0.5 hover:border-aqua/60 hover:bg-aqua/[0.07] focus:outline-none focus-visible:ring-2 focus-visible:ring-aqua/60"
    >
      <span className="rounded-full border border-aqua/30 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-aqua/85">
        Create
      </span>
      <p
        id={CREATE_TILE_ID}
        className="mt-3 text-sm font-semibold text-white"
      >
        New process
      </p>
      <p className="mt-1 text-xs leading-5 text-white/55">
        Create another top-level workspace process from a template.
      </p>
      <span className="mt-3 inline-flex items-center text-xs font-semibold text-aqua/75 transition group-hover:text-aqua">
        Choose template →
      </span>
    </button>
  );
}

function HealthChip({ status }: { status: string }) {
  if (status === "ok") {
    return (
      <span className="rounded-full bg-emerald-400/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-emerald-300">
        ok
      </span>
    );
  }
  if (status === "warning" || status === "degraded") {
    return (
      <span className="rounded-full bg-amber-400/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-amber-200">
        {status}
      </span>
    );
  }
  return (
    <span className="rounded-full bg-coral/20 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest text-coral">
      {status}
    </span>
  );
}
