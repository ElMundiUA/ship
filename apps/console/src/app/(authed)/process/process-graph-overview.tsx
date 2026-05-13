"use client";

import { useRouter } from "next/navigation";
import { useMemo } from "react";

import type {
  ApiProcessList,
  ApiProcessSummary,
} from "@/lib/api/client";

/**
 * Workspace overview at /process — flat list of editable processes.
 *
 * The previous implementation rendered a card grid where the workspace
 * itself was a same-size tile (non-clickable, just visual noise) and
 * the "+ New process" CTA had the same rank as a real process. The
 * operator's question was "what's the difference between these
 * boxes?" — the answer being "the workspace is the container, the
 * processes are the things you edit, the create button is an action"
 * needed to be encoded in the layout.
 *
 * Now it's a flat list: one row per process with name + description +
 * metrics + Open editor link. The "+ Add process" sits below as a
 * small secondary action. The workspace tile is gone; the workspace
 * is the AppShell context, no need to repeat it on the page.
 */
export function ProcessGraphOverview({
  processList,
  repoId,
}: {
  processList: ApiProcessList;
  repoId?: string;
}) {
  const router = useRouter();

  // Top-level processes only (subprocesses live inside their parent's
  // editor, not as standalone rows here).
  const processes = useMemo<ApiProcessSummary[]>(
    () =>
      processList.processes.filter((p) => !p.parent_process_id),
    [processList.processes],
  );

  function openProcess(p: ApiProcessSummary) {
    const href = repoId
      ? `/process/${encodeURIComponent(p.id)}?repo=${encodeURIComponent(repoId)}`
      : `/process/${encodeURIComponent(p.id)}`;
    router.push(href);
  }

  function openCreatePicker() {
    window.alert("New workspace process — template picker coming soon.");
  }

  return (
    <div className="space-y-2">
      {processes.length === 0 ? (
        <div className="rounded-xl border border-dashed border-white/15 bg-white/[0.02] p-6 text-center text-sm text-white/55">
          No processes yet. Add one to start orchestrating work.
        </div>
      ) : (
        <ul className="overflow-hidden rounded-xl border border-white/10">
          {processes.map((p) => (
            <li
              key={p.id}
              className="border-b border-white/[0.06] last:border-b-0"
            >
              <button
                type="button"
                onClick={() => openProcess(p)}
                className="group flex w-full items-center justify-between gap-4 bg-white/[0.02] px-4 py-3 text-left transition hover:bg-white/[0.04] focus:outline-none focus-visible:bg-white/[0.04]"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-bold text-white">
                      {p.name}
                    </span>
                    {p.primary && (
                      <span className="rounded border border-aqua/25 bg-aqua/[0.08] px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-aqua/85">
                        default
                      </span>
                    )}
                  </div>
                  {p.description && (
                    <p className="mt-0.5 truncate text-xs text-white/45">
                      {p.description}
                    </p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2 text-[10px] font-semibold uppercase tracking-widest">
                  <span className="rounded border border-white/10 bg-black/30 px-1.5 py-0.5 text-white/55">
                    {p.state_count} stage{p.state_count === 1 ? "" : "s"}
                  </span>
                  <span className="rounded border border-white/10 bg-black/30 px-1.5 py-0.5 text-white/55">
                    {p.task_count} active
                  </span>
                  {p.blocked_count > 0 && (
                    <span className="rounded border border-coral/30 bg-coral/[0.08] px-1.5 py-0.5 text-coral/85">
                      {p.blocked_count} blocked
                    </span>
                  )}
                  <HealthDot status={p.health} />
                  <span className="text-aqua/65 group-hover:text-aqua">→</span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      )}
      <button
        type="button"
        onClick={openCreatePicker}
        className="w-full rounded-md border border-dashed border-white/15 bg-white/[0.015] px-3 py-2 text-xs font-semibold text-white/55 transition hover:border-aqua/40 hover:text-aqua"
      >
        + Add process from template
      </button>
    </div>
  );
}

function HealthDot({ status }: { status: string }) {
  let bg = "bg-aqua";
  if (status === "warning" || status === "degraded") bg = "bg-sun";
  else if (status !== "ok") bg = "bg-coral";
  return (
    <span
      className="inline-flex items-center gap-1 rounded border border-white/10 bg-black/30 px-1.5 py-0.5 text-white/55"
      title={status}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${bg}`} aria-hidden />
      {status}
    </span>
  );
}
