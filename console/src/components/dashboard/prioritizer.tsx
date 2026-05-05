"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useTransition,
} from "react";
import type { DragEvent as ReactDragEvent, KeyboardEvent } from "react";

import type {
  ApiPrioritiesResponse,
  ApiPriorityProject,
  ApiPriorityTracker,
  ApiPriorityUpNext,
} from "@/lib/api/client";
import { cn } from "@/lib/cn";

import {
  reorderPrioritiesAction,
  setAutonomyPausedAction,
} from "./actions";

/**
 * Dashboard v2 prioritizer — drag-to-reorder list of tracker projects
 * plus the workspace-level autonomy switch and tracker connection
 * hairline. Designer wireframe locks: aqua = positive editorial,
 * sun = paused/attention, coral = errors, no bordered cards.
 *
 * Why a client component: drag, keyboard reorder, and autonomy toggle
 * all need optimistic UI that survives the network round-trip without
 * the page collapsing. Mutations route through server actions so the
 * session token never crosses the wire.
 *
 * Empty states are two distinct shapes — disconnected = single CTA
 * row; connected-but-empty = three faint scaffold rows; flipped on
 * ``tracker.status``. Conflating them was the previous draft's bug.
 */

const DRAG_MIME = "application/x-ship-priority-id";

type Props = {
  workspaceId: string;
  initial: ApiPrioritiesResponse;
};

export function DashboardPrioritizer({ workspaceId, initial }: Props) {
  const [serverState, setServerState] = useState<ApiPrioritiesResponse>(initial);
  // Working copy of the order — only touched while the user is
  // dragging or in keyboard move-mode. Save flushes it through the
  // server action, Revert restores from the last server state.
  const [draftOrder, setDraftOrder] = useState<string[] | null>(null);
  const [pending, startTransition] = useTransition();
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const tracker = serverState.tracker;
  const allProjects = serverState.projects;
  const prioritisedIds = useMemo(
    () =>
      allProjects
        .filter((p) => p.ordinal !== null)
        .map((p) => p.project_native_id),
    [allProjects],
  );
  const projectById = useMemo(() => {
    const map = new Map<string, ApiPriorityProject>();
    for (const p of allProjects) map.set(p.project_native_id, p);
    return map;
  }, [allProjects]);

  // Compose the displayed list: when the user is editing, it's the
  // draftOrder (prioritised items in user-chosen order) followed by
  // unprioritised tail in name order. Otherwise it's the server's
  // own sort.
  const displayList = useMemo(() => {
    if (draftOrder === null) return allProjects;
    const seen = new Set(draftOrder);
    const head = draftOrder
      .map((id) => projectById.get(id))
      .filter((p): p is ApiPriorityProject => p !== undefined)
      .map((p, idx) => ({ ...p, ordinal: idx }));
    const tail = allProjects
      .filter((p) => !seen.has(p.project_native_id))
      .map((p) => ({ ...p, ordinal: null as number | null }));
    return [...head, ...tail];
  }, [allProjects, draftOrder, projectById]);

  const dirty = draftOrder !== null;

  // ----- mutations --------------------------------------------------

  const handleSave = useCallback(() => {
    if (!dirty || draftOrder === null) return;
    setErrorMessage(null);
    startTransition(async () => {
      const result = await reorderPrioritiesAction(workspaceId, draftOrder);
      if (result.ok) {
        setServerState(result.payload);
        setDraftOrder(null);
      } else {
        setErrorMessage(result.message);
      }
    });
  }, [dirty, draftOrder, workspaceId]);

  const handleRevert = useCallback(() => {
    setDraftOrder(null);
    setErrorMessage(null);
  }, []);

  const toggleAutonomy = useCallback(() => {
    const nextPaused = !serverState.autonomy_paused;
    // Optimistic UI — flip locally, fall back on error.
    setServerState((s) => ({ ...s, autonomy_paused: nextPaused }));
    setErrorMessage(null);
    startTransition(async () => {
      const result = await setAutonomyPausedAction(workspaceId, nextPaused);
      if (!result.ok) {
        setServerState((s) => ({ ...s, autonomy_paused: !nextPaused }));
        setErrorMessage(result.message);
      }
    });
  }, [serverState.autonomy_paused, workspaceId]);

  // ----- reorder helpers --------------------------------------------

  const moveBy = useCallback(
    (id: string, delta: number) => {
      // Use the current draftOrder; if it's null, seed from the
      // server's prioritised order.
      const current =
        draftOrder ?? prioritisedIds.slice();
      const idx = current.indexOf(id);
      if (idx === -1) {
        // Not yet prioritised — append, then move.
        const seeded = [...current, id];
        const next = moveIndex(seeded, seeded.length - 1, delta);
        setDraftOrder(next);
        return;
      }
      const next = moveIndex(current, idx, delta);
      if (next.join("|") === current.join("|")) return;
      setDraftOrder(next);
    },
    [draftOrder, prioritisedIds],
  );

  const reorderTo = useCallback(
    (sourceId: string, targetId: string) => {
      const current = draftOrder ?? prioritisedIds.slice();
      const working = current.slice();
      // If the source row is unprioritised, append it before reorder
      // — dropping onto a prioritised row should pin it into the
      // list. If the target is unprioritised, leave the list alone.
      if (!working.includes(sourceId)) working.push(sourceId);
      if (!working.includes(targetId)) return;
      const fromIdx = working.indexOf(sourceId);
      const [moved] = working.splice(fromIdx, 1);
      const targetIdx = working.indexOf(targetId);
      working.splice(targetIdx, 0, moved);
      if (working.join("|") === current.join("|")) return;
      setDraftOrder(working);
    },
    [draftOrder, prioritisedIds],
  );

  // ----- empty states -----------------------------------------------

  if (tracker.status === "disconnected") {
    return (
      <PrioritizerSection
        autonomyPaused={serverState.autonomy_paused}
        upNext={null}
        onToggleAutonomy={toggleAutonomy}
        tracker={tracker}
        pending={pending}
      >
        <li className="flex items-baseline justify-between gap-4 py-3">
          <p className="text-sm text-white/65">
            Connect a tracker to see priorities here.
          </p>
          <Link
            href={`/onboarding?step=tracker&ws=${encodeURIComponent(workspaceId)}`}
            className="shrink-0 text-[11px] font-bold uppercase tracking-widest text-aqua hover:text-white"
          >
            Connect Linear →
          </Link>
        </li>
      </PrioritizerSection>
    );
  }

  if (allProjects.length === 0) {
    // Two empty shapes share an empty list, but mean very different
    // things: the tracker errored on this fetch (we can't say
    // anything about Linear's project count) vs. the tracker
    // connected fine and the team has no projects yet. Conflating
    // them puts a scaffold "Add a project" line under a coral
    // ``error · reconnect`` hairline, which reads as "the prioritizer
    // is broken" — exactly the bug we hit in the first PR.
    if (tracker.status === "error") {
      return (
        <PrioritizerSection
          autonomyPaused={serverState.autonomy_paused}
          upNext={null}
          onToggleAutonomy={toggleAutonomy}
          tracker={tracker}
          pending={pending}
        >
          <li className="space-y-1 py-3 text-[12px]">
            <p className="text-coral/85">
              Couldn&apos;t fetch projects from{" "}
              {tracker.kind === "linear" ? "Linear" : "the tracker"}.
            </p>
            {tracker.last_health_error ? (
              <p className="font-mono text-[10.5px] text-coral/60">
                {tracker.last_health_error}
              </p>
            ) : null}
          </li>
        </PrioritizerSection>
      );
    }
    return (
      <PrioritizerSection
        autonomyPaused={serverState.autonomy_paused}
        upNext={null}
        onToggleAutonomy={toggleAutonomy}
        tracker={tracker}
        pending={pending}
      >
        {[0, 1, 2].map((i) => (
          <li
            key={i}
            className="flex items-baseline justify-between gap-4 py-3 text-[12px] text-white/30"
          >
            <span className="inline-flex items-center gap-2">
              <span className="h-1.5 w-1.5 rounded-full bg-white/15" />
              <span>Add a project in Linear — it&apos;ll appear here.</span>
            </span>
          </li>
        ))}
      </PrioritizerSection>
    );
  }

  return (
    <PrioritizerSection
      autonomyPaused={serverState.autonomy_paused}
      upNext={serverState.up_next}
      onToggleAutonomy={toggleAutonomy}
      tracker={tracker}
      pending={pending}
    >
      {displayList.map((project) => (
        <PrioritizerRow
          key={project.project_native_id}
          project={project}
          paused={serverState.autonomy_paused}
          onDragStart={(event) => {
            event.dataTransfer.setData(DRAG_MIME, project.project_native_id);
            event.dataTransfer.effectAllowed = "move";
          }}
          onDragOver={(event) => {
            const sourceId = event.dataTransfer.types.includes(DRAG_MIME);
            if (!sourceId) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = "move";
          }}
          onDrop={(event) => {
            const sourceId = event.dataTransfer.getData(DRAG_MIME);
            if (!sourceId || sourceId === project.project_native_id) return;
            event.preventDefault();
            reorderTo(sourceId, project.project_native_id);
          }}
          onMoveUp={() => moveBy(project.project_native_id, -1)}
          onMoveDown={() => moveBy(project.project_native_id, +1)}
        />
      ))}
      {dirty && (
        <li className="-mx-3 mt-1 flex items-baseline justify-between bg-white/[0.04] px-3 py-2">
          <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/50">
            Order changed
          </span>
          <span className="flex items-center gap-4 text-[11px] font-bold uppercase tracking-widest">
            <button
              type="button"
              onClick={handleRevert}
              disabled={pending}
              className="text-white/55 transition hover:text-white disabled:opacity-50"
            >
              Revert
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={pending}
              className="text-aqua transition hover:text-white disabled:opacity-50"
            >
              {pending ? "Saving…" : "Save"}
            </button>
          </span>
        </li>
      )}
      {errorMessage && (
        <li className="pt-2 text-[11px] text-coral/85">{errorMessage}</li>
      )}
    </PrioritizerSection>
  );
}


// ---------------------------------------------------------------------------
// Section shell — kicker, autonomy toggle, tracker hairline, up-next strip
// ---------------------------------------------------------------------------


function PrioritizerSection({
  children,
  autonomyPaused,
  upNext,
  tracker,
  pending,
  onToggleAutonomy,
}: {
  children: React.ReactNode;
  autonomyPaused: boolean;
  upNext: ApiPriorityUpNext | null;
  tracker: ApiPriorityTracker;
  pending: boolean;
  onToggleAutonomy: () => void;
}) {
  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h3 className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
          Priorities
          {autonomyPaused && (
            <span className="ml-2 text-sun">· paused</span>
          )}
        </h3>
        <div className="flex items-center gap-4 text-[11px]">
          <AutonomyToggle
            paused={autonomyPaused}
            disabled={pending}
            onToggle={onToggleAutonomy}
          />
          <TrackerHairline tracker={tracker} />
        </div>
      </div>
      {upNext ? (
        <UpNextStrip upNext={upNext} paused={autonomyPaused} />
      ) : null}
      <ul className="divide-y divide-white/[0.06]">{children}</ul>
    </section>
  );
}


function AutonomyToggle({
  paused,
  disabled,
  onToggle,
}: {
  paused: boolean;
  disabled: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={!paused}
      className="group inline-flex items-center gap-2 disabled:opacity-50"
    >
      <span className="text-white/45">Autonomy</span>
      <span
        aria-hidden
        className={cn(
          "relative inline-flex h-3 w-6 items-center rounded-full transition",
          paused ? "bg-sun/30" : "bg-aqua/40",
        )}
      >
        <span
          className={cn(
            "block h-2 w-2 translate-x-0.5 rounded-full bg-white transition",
            paused ? "translate-x-0.5" : "translate-x-3.5",
          )}
        />
      </span>
      <span
        className={cn(
          "font-bold uppercase tracking-widest text-[10px]",
          paused ? "text-sun" : "text-aqua/85 group-hover:text-aqua",
        )}
      >
        {paused ? "off" : "on"}
      </span>
    </button>
  );
}


function TrackerHairline({ tracker }: { tracker: ApiPriorityTracker }) {
  if (tracker.status === "disconnected") {
    return (
      <span className="text-coral">
        No tracker · <a href="/onboarding?step=tracker" className="font-bold underline-offset-4 hover:underline">connect →</a>
      </span>
    );
  }
  const label = tracker.kind === "linear" ? "Linear" : tracker.kind === "jira" ? "Jira" : "Tracker";
  if (tracker.status === "error") {
    return (
      <span className="text-coral" title={tracker.last_health_error ?? undefined}>
        {label} · error · <a href="/onboarding?step=tracker" className="font-bold underline-offset-4 hover:underline">reconnect →</a>
      </span>
    );
  }
  const synced =
    tracker.last_health_at !== null
      ? `synced ${formatRelative(tracker.last_health_at)}`
      : "synced";
  return <span className="text-white/40">{label} · {synced}</span>;
}


function UpNextStrip({
  upNext,
  paused,
}: {
  upNext: ApiPriorityUpNext;
  paused: boolean;
}) {
  if (paused) {
    return (
      <p className="flex items-center gap-2 text-[11px] text-white/30">
        <span aria-hidden>↑</span>
        <span>Up next · paused · resume to pull</span>
      </p>
    );
  }
  return (
    <p className="flex items-center gap-2 text-[11px] text-white/65">
      <span aria-hidden className="text-aqua">↑</span>
      <span>
        <span className="text-white/45">Up next ·</span>{" "}
        <span className="font-semibold text-white">{upNext.project_name}</span>
      </span>
    </p>
  );
}


// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------


function PrioritizerRow({
  project,
  paused,
  onDragStart,
  onDragOver,
  onDrop,
  onMoveUp,
  onMoveDown,
}: {
  project: ApiPriorityProject;
  paused: boolean;
  onDragStart: (event: ReactDragEvent<HTMLLIElement>) => void;
  onDragOver: (event: ReactDragEvent<HTMLLIElement>) => void;
  onDrop: (event: ReactDragEvent<HTMLLIElement>) => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}) {
  const [moveMode, setMoveMode] = useState(false);
  const rowRef = useRef<HTMLLIElement | null>(null);

  // Exit move-mode if the row loses focus entirely (e.g. user
  // tabs/clicks away). Move-mode is a per-row affordance and
  // should never persist when the row isn't active.
  useEffect(() => {
    if (!moveMode) return;
    const onBlur = (event: FocusEvent) => {
      if (
        rowRef.current &&
        !rowRef.current.contains(event.relatedTarget as Node | null)
      ) {
        setMoveMode(false);
      }
    };
    const node = rowRef.current;
    node?.addEventListener("focusout", onBlur);
    return () => node?.removeEventListener("focusout", onBlur);
  }, [moveMode]);

  const handleKey = (event: KeyboardEvent<HTMLLIElement>) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setMoveMode((prev) => !prev);
      return;
    }
    if (event.key === "Escape") {
      setMoveMode(false);
      return;
    }
    if (!moveMode) return;
    if (event.key === "ArrowUp") {
      event.preventDefault();
      onMoveUp();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      onMoveDown();
    }
  };

  const fractionLabel = formatFraction(project.completed, project.total);
  const barFraction = computeBarFraction(project.completed, project.total);
  const colorStyle = project.color
    ? { backgroundColor: project.color }
    : undefined;
  const isPrioritised = project.ordinal !== null;

  return (
    <li
      ref={rowRef}
      tabIndex={0}
      draggable
      onDragStart={onDragStart}
      onDragOver={onDragOver}
      onDrop={onDrop}
      onKeyDown={handleKey}
      className={cn(
        "group relative flex items-center gap-3 py-2 pl-3 pr-1 outline-none transition",
        "focus-visible:before:absolute focus-visible:before:inset-y-1 focus-visible:before:left-0 focus-visible:before:w-px focus-visible:before:bg-aqua/60",
        moveMode && "before:absolute before:inset-y-1 before:left-0 before:w-0.5 before:bg-aqua",
        paused ? "opacity-80" : "",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "shrink-0 select-none text-[12px] transition",
          moveMode ? "text-aqua" : "text-white/20 group-hover:text-white/55",
        )}
      >
        {moveMode ? "↕" : "⋮"}
      </span>
      <span
        aria-hidden
        className={cn(
          "h-2 w-2 shrink-0 rounded-full",
          project.color ? "" : "bg-white/25",
        )}
        style={colorStyle}
      />
      <div className="min-w-0 flex-1">
        {project.url ? (
          <a
            href={project.url}
            target="_blank"
            rel="noreferrer"
            className="truncate text-[13px] font-semibold text-white hover:text-aqua"
          >
            {project.name}
          </a>
        ) : (
          <p className="truncate text-[13px] font-semibold text-white">
            {project.name}
          </p>
        )}
        {isPrioritised ? null : (
          <p className="mt-0.5 text-[10px] uppercase tracking-[0.16em] text-white/30">
            unprioritised — drag in
          </p>
        )}
      </div>
      <span className="shrink-0 font-mono text-[11px] tabular-nums text-white/55">
        {fractionLabel}
      </span>
      <span
        aria-hidden
        className="block h-1 w-20 shrink-0 rounded-sm bg-white/[0.06]"
      >
        <span
          className="block h-full rounded-sm transition-all"
          style={{
            width: `${barFraction * 100}%`,
            backgroundColor: project.color ?? undefined,
          }}
        />
      </span>
    </li>
  );
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function moveIndex(arr: string[], idx: number, delta: number): string[] {
  const next = arr.slice();
  const target = idx + delta;
  if (target < 0 || target >= next.length) return arr;
  const [moved] = next.splice(idx, 1);
  next.splice(target, 0, moved);
  return next;
}


function formatFraction(completed: number | null, total: number | null): string {
  if (completed === null || total === null) return "—";
  if (total === 0) return "0/0";
  return `${completed}/${total}`;
}


function computeBarFraction(
  completed: number | null,
  total: number | null,
): number {
  if (completed === null || total === null || total <= 0) return 0;
  const fraction = completed / total;
  if (fraction < 0) return 0;
  if (fraction > 1) return 1;
  return fraction;
}


function formatRelative(value: string): string {
  const date = Date.parse(value);
  if (Number.isNaN(date)) return "now";
  const diff = Date.now() - date;
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return "just now";
  if (diff < hour) return `${Math.max(1, Math.round(diff / minute))}m ago`;
  if (diff < day) return `${Math.round(diff / hour)}h ago`;
  if (diff < 30 * day) return `${Math.round(diff / day)}d ago`;
  return new Date(date).toLocaleDateString();
}


