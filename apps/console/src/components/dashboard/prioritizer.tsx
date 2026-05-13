"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
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
  ApiPriorityState,
  ApiPriorityTracker,
  ApiPriorityUpNext,
  ApiReorderRow,
} from "@/lib/api/client";
import { cn } from "@/lib/cn";

import {
  reorderPrioritiesAction,
  setAutonomyPausedAction,
  startDecompositionAction,
} from "./actions";

/**
 * Dashboard v2 prioritizer — drag-to-reorder + drag-across-divider list of
 * tracker projects, plus the workspace-level autonomy switch and tracker
 * connection hairline. Designer wireframe locks: aqua = positive editorial,
 * sun = paused/attention, lilac = planning, coral = errors, no bordered cards.
 *
 * The list is grouped into three buckets — ``active`` (agent picks from
 * here), ``planning`` (operator is shaping the brief; agent-invisible),
 * ``parked`` (explicit hold; collapsed by default). Section dividers are
 * the state-change affordance: dropping a row across a divider moves it
 * into that bucket. Within a bucket, drag reorders.
 *
 * Why a client component: drag, keyboard reorder, and autonomy toggle
 * all need optimistic UI that survives the network round-trip without
 * the page collapsing. Mutations route through server actions so the
 * session token never crosses the wire.
 */

const DRAG_MIME = "application/x-ship-priority-id";

type Props = {
  workspaceId: string;
  initial: ApiPrioritiesResponse;
};

type DraftRow = { id: string; state: ApiPriorityState };

const STATE_RANK: Record<ApiPriorityState, number> = {
  active: 0,
  planning: 1,
  parked: 2,
};

const SECTION_ORDER: ApiPriorityState[] = ["active", "planning", "parked"];

// UI labels for ``priority_state`` enum values. The DB enum stays
// ``planning`` (renaming would force an audit-log + JSONB churn for
// what is, at the dashboard, just a label). "Drafts" is the operator
// word — what's actually in the bucket is a one-line title + a brief
// the PO is shaping in the Navigator. Don't rename in the API client
// or the server; only here.
const SECTION_COPY: Record<
  ApiPriorityState,
  { label: string; sub: (n: number) => string }
> = {
  active: {
    label: "Active",
    sub: (n) => (n === 1 ? "1 pick for the agent" : `${n} picks for the agent`),
  },
  planning: {
    label: "Drafts",
    sub: (n) => (n === 1 ? "1 you're shaping" : `${n} you're shaping`),
  },
  parked: {
    label: "Parked",
    sub: (n) => (n === 1 ? "1 not now" : `${n} not now`),
  },
};


export function DashboardPrioritizer({ workspaceId, initial }: Props) {
  const router = useRouter();
  const [serverState, setServerState] = useState<ApiPrioritiesResponse>(initial);
  // Working copy of the prioritised rows — only touched while the
  // user is dragging or in keyboard move-mode. Save flushes through
  // the server action, Revert restores from the last server state.
  const [draftOrder, setDraftOrder] = useState<DraftRow[] | null>(null);
  const [pending, startTransition] = useTransition();
  const [draftingPending, setDraftingPending] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // ELS-74: dashboard "+ New project" CTA. Opens a fresh Navigator
  // thread tagged ``intent=shape_project`` and navigates the user
  // there. The drafting-mode system prompt then biases the agent
  // toward shaping a brief and waiting for explicit confirmation
  // before ``create_project`` fires. Errors fall through to the
  // existing errorMessage band — there's no separate failure surface
  // for this CTA because failure means "the API is down," which the
  // tracker hairline already conveys.
  const startProjectDrafting = useCallback(async () => {
    if (draftingPending) return;
    setDraftingPending(true);
    setErrorMessage(null);
    try {
      const res = await fetch("/api/chat/new-thread", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          workspace_id: workspaceId,
          intent: "shape_project",
          title: "Drafting a project",
        }),
      });
      if (!res.ok) {
        setErrorMessage(
          res.status === 401
            ? "Session expired — reload to sign in again."
            : `Couldn't start drafting (HTTP ${res.status}).`,
        );
        return;
      }
      router.push("/chat");
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : "Couldn't start drafting.",
      );
    } finally {
      setDraftingPending(false);
    }
  }, [draftingPending, router, workspaceId]);

  const tracker = serverState.tracker;
  const allProjects = serverState.projects;
  const projectById = useMemo(() => {
    const map = new Map<string, ApiPriorityProject>();
    for (const p of allProjects) map.set(p.project_native_id, p);
    return map;
  }, [allProjects]);

  // Server-truth rows — projects that already have a saved row in the
  // priorities table. We seed the draft from these whenever the user
  // starts editing.
  const serverRows: DraftRow[] = useMemo(() => {
    return allProjects
      .filter(
        (p): p is ApiPriorityProject & { priority_state: ApiPriorityState } =>
          p.priority_state !== null && p.ordinal !== null,
      )
      .slice()
      .sort((a, b) => {
        const stateDelta =
          STATE_RANK[a.priority_state] - STATE_RANK[b.priority_state];
        if (stateDelta !== 0) return stateDelta;
        return (a.ordinal ?? 0) - (b.ordinal ?? 0);
      })
      .map((p) => ({ id: p.project_native_id, state: p.priority_state }));
  }, [allProjects]);

  const effectiveRows: DraftRow[] = draftOrder ?? serverRows;
  const dirty = draftOrder !== null;

  const rowsByState: Record<ApiPriorityState, DraftRow[]> = useMemo(() => {
    const acc: Record<ApiPriorityState, DraftRow[]> = {
      active: [],
      planning: [],
      parked: [],
    };
    for (const row of effectiveRows) acc[row.state].push(row);
    return acc;
  }, [effectiveRows]);

  const unprioritised = useMemo(() => {
    const seen = new Set(effectiveRows.map((r) => r.id));
    return allProjects.filter((p) => !seen.has(p.project_native_id));
  }, [allProjects, effectiveRows]);

  // ----- mutations --------------------------------------------------

  const handleSave = useCallback(() => {
    if (!dirty || draftOrder === null) return;
    setErrorMessage(null);
    const payload: ApiReorderRow[] = draftOrder.map((row) => ({
      project_native_id: row.id,
      state: row.state,
    }));
    startTransition(async () => {
      const result = await reorderPrioritiesAction(workspaceId, payload);
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

  // Move a row by ±1 within its current section. If it sits at the
  // section edge and is moved past it, the row crosses the divider
  // and changes state — matches the drag-across-divider semantics so
  // keyboard users have feature parity.
  const moveBy = useCallback(
    (id: string, delta: number) => {
      const current = draftOrder ?? serverRows.slice();
      const seeded = current.some((r) => r.id === id)
        ? current.slice()
        : [
            ...current,
            {
              id,
              state: "active" as ApiPriorityState,
            },
          ];
      const idx = seeded.findIndex((r) => r.id === id);
      if (idx === -1) return;
      const target = idx + delta;
      if (target < 0 || target >= seeded.length) return;
      const moved = { ...seeded[idx] };
      seeded.splice(idx, 1);
      // Adopt the state of whichever row we land next to so the
      // section a row belongs to is always coherent with its
      // neighbours.
      const neighbour =
        delta < 0
          ? seeded[Math.max(0, target)]
          : seeded[Math.min(seeded.length - 1, target - 1)];
      if (neighbour) moved.state = neighbour.state;
      seeded.splice(target, 0, moved);
      if (rowsEqual(seeded, current)) return;
      setDraftOrder(seeded);
    },
    [draftOrder, serverRows],
  );

  // Drop ``sourceId`` immediately above ``targetId``, adopting the
  // target row's state. If either row isn't yet prioritised, append
  // it first.
  const reorderTo = useCallback(
    (sourceId: string, targetId: string) => {
      const current = draftOrder ?? serverRows.slice();
      const working = current.slice();
      ensureRow(working, sourceId, "active");
      ensureRow(working, targetId, "active");
      const fromIdx = working.findIndex((r) => r.id === sourceId);
      const [moved] = working.splice(fromIdx, 1);
      const targetIdx = working.findIndex((r) => r.id === targetId);
      const targetRow = working[targetIdx];
      if (targetRow) moved.state = targetRow.state;
      working.splice(targetIdx, 0, moved);
      if (rowsEqual(working, current)) return;
      setDraftOrder(working);
    },
    [draftOrder, serverRows],
  );

  // Drop ``sourceId`` at the bottom of ``state``'s section. Used by
  // section-divider drops, "send to section" row menus (future), and
  // the cross-section keyboard moves.
  const moveToSection = useCallback(
    (sourceId: string, state: ApiPriorityState) => {
      const current = draftOrder ?? serverRows.slice();
      const working = current.slice();
      ensureRow(working, sourceId, state);
      const idx = working.findIndex((r) => r.id === sourceId);
      const [moved] = working.splice(idx, 1);
      moved.state = state;
      // Insert at the END of the section so the user's existing top-of-
      // section ordering is preserved (parking the most-recent active
      // shouldn't shuffle the queue).
      let insertAt = working.length;
      for (let i = 0; i < working.length; i++) {
        if (STATE_RANK[working[i].state] > STATE_RANK[state]) {
          insertAt = i;
          break;
        }
      }
      working.splice(insertAt, 0, moved);
      if (rowsEqual(working, current)) return;
      setDraftOrder(working);
    },
    [draftOrder, serverRows],
  );

  // Hand off to decomposition. Records pending state per-project so
  // double-clicks don't spawn two start runs (the endpoint is also
  // server-side idempotent, but optimistic UI keeps the row from
  // flashing twice). On success we mark the row "decomposing" locally
  // so the chip swaps before the next /priorities round-trip.
  //
  // These hooks live ABOVE the empty-state early returns below — moving
  // them under the returns broke ``react-hooks/rules-of-hooks`` because
  // hooks must run in a stable order on every render and the empty
  // branches return before reaching them. ``next build`` (lint enabled)
  // catches it; bare ``tsc`` doesn't, which is how the regression slipped
  // past local checks in the original PR3.
  const [handoffPending, setHandoffPending] = useState<string | null>(null);
  const [decomposingIds, setDecomposingIds] = useState<Set<string>>(new Set());
  const handleHandoff = useCallback(
    async (nativeId: string) => {
      if (handoffPending) return;
      setHandoffPending(nativeId);
      setErrorMessage(null);
      const result = await startDecompositionAction(workspaceId, nativeId);
      setHandoffPending(null);
      if (!result.ok) {
        setErrorMessage(result.message);
        return;
      }
      setDecomposingIds((prev) => {
        const next = new Set(prev);
        next.add(nativeId);
        return next;
      });
    },
    [handoffPending, workspaceId],
  );

  // ----- empty states -----------------------------------------------

  if (tracker.status === "disconnected") {
    return (
      <PrioritizerSection
        autonomyPaused={serverState.autonomy_paused}
        upNext={null}
        activeCount={0}
        onToggleAutonomy={toggleAutonomy}
        tracker={tracker}
        pending={pending}
        onStartDrafting={startProjectDrafting}
        draftingPending={draftingPending}
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
    if (tracker.status === "error") {
      const missingRead =
        tracker.kind === "linear" &&
        tracker.scopes !== null &&
        !tracker.scopes.includes("read");
      return (
        <PrioritizerSection
          autonomyPaused={serverState.autonomy_paused}
          upNext={null}
          activeCount={0}
          onToggleAutonomy={toggleAutonomy}
          tracker={tracker}
          pending={pending}
          onStartDrafting={startProjectDrafting}
          draftingPending={draftingPending}
        >
          <li className="space-y-1 py-3 text-[12px]">
            <p className="text-coral/85">
              Couldn&apos;t fetch projects from{" "}
              {tracker.kind === "linear" ? "Linear" : "the tracker"}.
            </p>
            {missingRead ? (
              <p className="text-[11px] text-sun/85">
                Token scopes don&apos;t include <span className="font-mono">read</span> — re-authorize Linear with{" "}
                <span className="font-mono">read,write,issues:create,comments:create</span>.
              </p>
            ) : null}
            {tracker.scopes && tracker.scopes.length > 0 ? (
              <p className="text-[10.5px] text-white/45">
                scopes: {tracker.scopes.join(", ")}
              </p>
            ) : null}
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
        activeCount={0}
        onToggleAutonomy={toggleAutonomy}
        tracker={tracker}
        pending={pending}
        onStartDrafting={startProjectDrafting}
        draftingPending={draftingPending}
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

  // ----- main list (state-grouped) ----------------------------------

  const renderRow = (project: ApiPriorityProject, state: ApiPriorityState) => (
    <PrioritizerRow
      key={project.project_native_id}
      project={project}
      rowState={state}
      paused={serverState.autonomy_paused}
      decomposing={decomposingIds.has(project.project_native_id)}
      handoffPending={handoffPending === project.project_native_id}
      onHandoff={() => handleHandoff(project.project_native_id)}
      onDragStart={(event) => {
        event.dataTransfer.setData(DRAG_MIME, project.project_native_id);
        event.dataTransfer.effectAllowed = "move";
      }}
      onDragOver={(event) => {
        if (!event.dataTransfer.types.includes(DRAG_MIME)) return;
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
  );

  const sectionDropHandlers = (state: ApiPriorityState) => ({
    onDragOver: (event: ReactDragEvent<HTMLLIElement>) => {
      if (!event.dataTransfer.types.includes(DRAG_MIME)) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
    },
    onDrop: (event: ReactDragEvent<HTMLLIElement>) => {
      const sourceId = event.dataTransfer.getData(DRAG_MIME);
      if (!sourceId) return;
      event.preventDefault();
      moveToSection(sourceId, state);
    },
  });

  return (
    <PrioritizerSection
      autonomyPaused={serverState.autonomy_paused}
      upNext={serverState.up_next}
      activeCount={rowsByState.active.length}
      onToggleAutonomy={toggleAutonomy}
      tracker={tracker}
      pending={pending}
      onStartDrafting={startProjectDrafting}
      draftingPending={draftingPending}
    >
      {SECTION_ORDER.map((state) => {
        const rows = rowsByState[state];
        const showEmptyHint =
          rows.length === 0 && state === "active" && unprioritised.length > 0;
        return (
          <SectionGroup
            key={state}
            state={state}
            count={rows.length}
            dropHandlers={sectionDropHandlers(state)}
          >
            {rows
              .map((row) => projectById.get(row.id))
              .filter((p): p is ApiPriorityProject => p !== undefined)
              .map((project) => renderRow(project, state))}
            {rows.length === 0 && !showEmptyHint && (
              <li
                className="py-2 pl-3 text-[11px] italic text-white/30"
                {...sectionDropHandlers(state)}
              >
                drop a project here to {state === "active" ? "queue it" : state === "planning" ? "draft it" : "park it"}
              </li>
            )}
            {showEmptyHint && (
              <li className="py-2 pl-3 text-[11px] text-sun/80">
                No active projects — drag one in to give the agent something to pick up.
              </li>
            )}
          </SectionGroup>
        );
      })}
      {unprioritised.length > 0 && (
        <SectionGroup
          state={null}
          count={unprioritised.length}
          dropHandlers={undefined}
        >
          {unprioritised.map((project) => renderRow(project, "active"))}
        </SectionGroup>
      )}
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
  activeCount,
  tracker,
  pending,
  onToggleAutonomy,
  onStartDrafting,
  draftingPending,
}: {
  children: React.ReactNode;
  autonomyPaused: boolean;
  upNext: ApiPriorityUpNext | null;
  activeCount: number;
  tracker: ApiPriorityTracker;
  pending: boolean;
  onToggleAutonomy: () => void;
  onStartDrafting: () => void;
  draftingPending: boolean;
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
          <button
            type="button"
            onClick={onStartDrafting}
            disabled={draftingPending}
            className="font-bold uppercase tracking-widest text-aqua transition hover:text-white disabled:opacity-50"
          >
            {draftingPending ? "Opening…" : "+ New project"}
          </button>
          <AutonomyToggle
            paused={autonomyPaused}
            disabled={pending}
            onToggle={onToggleAutonomy}
          />
          <TrackerHairline tracker={tracker} />
        </div>
      </div>
      <UpNextStrip
        upNext={upNext}
        paused={autonomyPaused}
        activeCount={activeCount}
      />
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
  activeCount,
}: {
  upNext: ApiPriorityUpNext | null;
  paused: boolean;
  activeCount: number;
}) {
  if (paused) {
    return (
      <p className="flex items-center gap-2 text-[11px] text-white/30">
        <span aria-hidden>↑</span>
        <span>Up next · paused · resume to pull</span>
      </p>
    );
  }
  if (upNext === null) {
    // No active projects → tell the operator the agent is idle on
    // purpose so an empty Active list doesn't read as a server bug.
    if (activeCount === 0) {
      return (
        <p className="flex items-center gap-2 text-[11px] text-sun/85">
          <span aria-hidden>↑</span>
          <span>Up next · nothing active — drag a project into Active to give the agent work.</span>
        </p>
      );
    }
    return null;
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
// Section header + drop zone
// ---------------------------------------------------------------------------


function SectionGroup({
  state,
  count,
  dropHandlers,
  children,
}: {
  state: ApiPriorityState | null;
  count: number;
  dropHandlers?: {
    onDragOver: (event: ReactDragEvent<HTMLLIElement>) => void;
    onDrop: (event: ReactDragEvent<HTMLLIElement>) => void;
  };
  children: React.ReactNode;
}) {
  const headerLabel =
    state === null ? "Unprioritised" : SECTION_COPY[state].label;
  const headerSub =
    state === null
      ? count === 1
        ? "1 in tracker · drag in to bucket"
        : `${count} in tracker · drag in to bucket`
      : SECTION_COPY[state].sub(count);

  const headerToneByState: Record<ApiPriorityState | "none", string> = {
    active: "text-white/55",
    planning: "text-lilac/85",
    parked: "text-white/40",
    none: "text-white/35",
  };
  const headerTone = headerToneByState[state ?? "none"];

  return (
    <>
      <li
        className={cn(
          "flex items-baseline justify-between gap-3 pt-3 pb-1",
          state === null && "border-t border-dashed border-white/10",
        )}
        {...(dropHandlers ?? {})}
      >
        <span
          className={cn(
            "text-[10px] font-bold uppercase tracking-[0.22em]",
            headerTone,
          )}
        >
          {headerLabel}
          <span className="ml-2 font-normal normal-case tracking-normal text-white/35">
            · {headerSub}
          </span>
        </span>
      </li>
      {children}
    </>
  );
}


// ---------------------------------------------------------------------------
// Row
// ---------------------------------------------------------------------------


function PrioritizerRow({
  project,
  rowState,
  paused,
  decomposing,
  handoffPending,
  onHandoff,
  onDragStart,
  onDragOver,
  onDrop,
  onMoveUp,
  onMoveDown,
}: {
  project: ApiPriorityProject;
  rowState: ApiPriorityState;
  paused: boolean;
  decomposing: boolean;
  handoffPending: boolean;
  onHandoff: () => void;
  onDragStart: (event: ReactDragEvent<HTMLLIElement>) => void;
  onDragOver: (event: ReactDragEvent<HTMLLIElement>) => void;
  onDrop: (event: ReactDragEvent<HTMLLIElement>) => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}) {
  // Continue shaping → re-open the originating Navigator thread when
  // the project was drafted in chat. Only surface on Drafts rows
  // (rowState=='planning'); a project that's already Active/Parked
  // doesn't benefit from "shaping" affordance.
  const showContinueShaping =
    rowState === "planning" && Boolean(project.originating_thread_id);
  // Hand-off button — only on Drafts rows that haven't been handed
  // off yet. When ``decomposing`` flips on (post-handoff) the row
  // shows a quiet "Decomposing…" hint instead so the operator knows
  // the chain is running.
  const showHandoff = rowState === "planning" && !decomposing;
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

  const isParked = rowState === "parked";
  const isPlanning = rowState === "planning";
  const isPrioritised = project.ordinal !== null;
  const fractionLabel = isPlanning
    ? "brief"
    : formatFraction(project.completed, project.total);
  const dotStyle = isPlanning
    ? undefined
    : project.color
      ? { backgroundColor: project.color }
      : undefined;
  const dotClass = cn(
    "h-2 w-2 shrink-0 rounded-full",
    isPlanning ? "bg-lilac/70" : project.color ? "" : "bg-white/25",
  );

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
        isParked ? "opacity-40 hover:opacity-70" : paused ? "opacity-80" : "",
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
      <span aria-hidden className={dotClass} style={dotStyle} />
      <div className="min-w-0 flex-1">
        {project.url ? (
          <a
            href={project.url}
            target="_blank"
            rel="noreferrer"
            draggable={false}
            className={cn(
              "truncate text-[13px] font-semibold hover:text-aqua",
              isPlanning ? "italic text-white/85" : "text-white",
            )}
          >
            {project.name}
          </a>
        ) : (
          <p
            className={cn(
              "truncate text-[13px] font-semibold",
              isPlanning ? "italic text-white/85" : "text-white",
            )}
          >
            {project.name}
          </p>
        )}
        {isPrioritised ? null : (
          <p className="mt-0.5 text-[10px] uppercase tracking-[0.16em] text-white/30">
            unprioritised — drag in
          </p>
        )}
      </div>
      <span
        className={cn(
          "shrink-0 font-mono text-[11px] tabular-nums",
          isPlanning ? "text-lilac/70" : "text-white/55",
        )}
      >
        {fractionLabel}
      </span>
      {decomposing ? (
        // Post-handoff: decomposition pipeline is running on the anchor.
        // Quiet hint so the operator knows the chain started without
        // overwhelming the row.
        <span className="block w-20 shrink-0 text-right text-[10px] font-bold uppercase tracking-widest text-lilac/70">
          Decomposing…
        </span>
      ) : showHandoff ? (
        // The Drafts row gets BOTH the Continue-shaping link (above
        // the row, conditional on originating_thread_id) and a
        // **Hand off** button here in the progress slot. Clicking
        // posts to the start_decomposition endpoint; on success the
        // row swaps to the "Decomposing…" hint above.
        <span className="flex w-20 shrink-0 flex-col items-end gap-0.5">
          {showContinueShaping ? (
            <Link
              href="/chat"
              className="text-[10px] font-bold uppercase tracking-widest text-lilac/85 transition hover:text-white"
              draggable={false}
            >
              Continue →
            </Link>
          ) : null}
          <button
            type="button"
            onClick={onHandoff}
            disabled={handoffPending}
            className="text-[10px] font-bold uppercase tracking-widest text-aqua transition hover:text-white disabled:opacity-50"
          >
            {handoffPending ? "…" : "Hand off →"}
          </button>
        </span>
      ) : (
        // Fixed-width spacer keeps the row gutter aligned across
        // sections so the progress column doesn't reflow per-state.
        <span aria-hidden className="block h-1 w-20 shrink-0" />
      )}
    </li>
  );
}


// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------


function ensureRow(
  arr: DraftRow[],
  id: string,
  defaultState: ApiPriorityState,
): void {
  if (!arr.some((r) => r.id === id)) {
    arr.push({ id, state: defaultState });
  }
}


function rowsEqual(a: DraftRow[], b: DraftRow[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].id !== b[i].id || a[i].state !== b[i].state) return false;
  }
  return true;
}


function formatFraction(completed: number | null, total: number | null): string {
  if (completed === null || total === null) return "—";
  if (total === 0) return "0/0";
  return `${completed}/${total}`;
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
