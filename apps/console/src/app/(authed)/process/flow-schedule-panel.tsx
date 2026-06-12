"use client";

import {
  type DragEvent as ReactDragEvent,
  useEffect,
  useMemo,
  useState,
} from "react";

import { Card, CardHeader } from "@/components/ui";
import type {
  ApiProcess,
  ApiProcessRoutine,
  ApiProcessSchedule,
  ApiProcessScheduleSlot,
  ApiRepoConfig,
} from "@/lib/api/client";
import { classifyCron, formatNextRun } from "@/lib/cron-next";
import { processConfigFromApiProcess } from "./process-config";
import { ProcessConfigProposalFields } from "./process-config-proposal-fields";
import { ProcessReviewSummary, processChangeSummary } from "./process-review-summary";
import {
  TIME_WINDOW_EXHAUSTED_TOOLTIP,
  knownTimes,
  nextAvailableTime,
} from "./flow-schedule-times";
import {
  BUILTIN_ROUTINE_CATALOG,
  HIDDEN_ROUTINE_IDS,
  isCanonicalRoutineId,
} from "./routine-catalog";

const WEEKDAYS = [
  { id: 1, label: "Mon" },
  { id: 2, label: "Tue" },
  { id: 3, label: "Wed" },
  { id: 4, label: "Thu" },
  { id: 5, label: "Fri" },
  { id: 6, label: "Sat" },
  { id: 0, label: "Sun" },
] as const;
const DRAG_SPECIALIST_MIME = "application/x-ship-specialist";

export function FlowSchedulePanel({
  workspaceId,
  process,
  repoId,
  config,
}: {
  workspaceId: string;
  process: ApiProcess;
  repoId?: string;
  config: ApiRepoConfig | null;
}) {
  const [schedule, setSchedule] = useState<ApiProcessSchedule>(() =>
    normalizedSchedule(process),
  );
  const [extraTimes, setExtraTimes] = useState<string[]>([]);

  useEffect(() => {
    setSchedule(normalizedSchedule(process));
    setExtraTimes([]);
  }, [process]);

  const processDraft = useMemo<ApiProcess>(
    () => ({ ...process, schedule }),
    [process, schedule],
  );
  const processConfig = useMemo(
    () => processConfigFromApiProcess(processDraft),
    [processDraft],
  );
  const initialConfig = useMemo(
    () => processConfigFromApiProcess({ ...process, schedule: normalizedSchedule(process) }),
    [process],
  );
  const initialReviewProcess = useMemo<ApiProcess>(
    () => ({ ...process, schedule: normalizedSchedule(process) }),
    [process],
  );
  const dirty = JSON.stringify(processConfig) !== JSON.stringify(initialConfig);
  const changeSummary = processChangeSummary(initialReviewProcess, processDraft, [
    ...(dirty ? ["Flow schedule slots changed"] : []),
  ]);
  const warnings = schedule.slots.flatMap((slot) => duplicateWarnings(slot));

  // Routine cron projection: visible (non-hidden) routines that are
  // enabled and have a parseable cron split into two buckets:
  //   - fixed → champagne dot in the (weekday, time) cells they fire in.
  //     We also expand their times into ``timeRows`` so a routine firing
  //     at 06:00 doesn't get hidden just because no specialist sits in
  //     that row.
  //   - highfreq → live in a separate "Continuous" strip outside the
  //     grid; one cell can't represent "every 30 min" anyway.
  const routineProjection = useMemo(() => {
    const fixed: Array<{
      routine: ApiProcessRoutine;
      weekday: number;
      time: string;
      cadence: string;
    }> = [];
    const continuous: Array<{ routine: ApiProcessRoutine; cadence: string }> = [];
    for (const r of process.routines) {
      if (HIDDEN_ROUTINE_IDS.has(r.id)) continue;
      if (r.enabled === false) continue;
      const shape = classifyCron(r.schedule ?? "");
      if (shape.kind === "fixed") {
        for (const slot of shape.slots) {
          fixed.push({
            routine: r,
            weekday: slot.weekday,
            time: slot.time,
            cadence: shape.cadence,
          });
        }
      } else if (shape.kind === "highfreq") {
        continuous.push({ routine: r, cadence: shape.cadence });
      }
    }
    return { fixed, continuous };
  }, [process.routines]);

  // Routine times also seed the time-row list so the calendar shows a
  // 06:00 row when Security review fires there, even if no specialist
  // ever sits at 06:00.
  const routineTimeSeeds = useMemo(
    () => routineProjection.fixed.map((r) => r.time),
    [routineProjection.fixed],
  );
  const timeRows = useMemo(
    () => knownTimes(schedule, [...extraTimes, ...routineTimeSeeds]),
    [schedule, extraTimes, routineTimeSeeds],
  );
  const nextTimeRow = useMemo(() => nextAvailableTime(timeRows), [timeRows]);
  const assignments = useMemo(() => assignmentMap(schedule), [schedule]);
  const assignedSpecialistIds = useMemo(
    () => new Set(schedule.slots.flatMap((slot) => slot.specialist_ids)),
    [schedule.slots],
  );

  const routineMarkers = useMemo(() => {
    const map = new Map<string, ApiProcessRoutine[]>();
    for (const f of routineProjection.fixed) {
      const key = cellKey(f.weekday, f.time);
      const list = map.get(key) ?? [];
      list.push(f.routine);
      map.set(key, list);
    }
    return map;
  }, [routineProjection.fixed]);

  // Display label per routine — short canonical label when available.
  const routineLabel = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of BUILTIN_ROUTINE_CATALOG) map.set(c.id, c.name);
    return map;
  }, []);

  function addSpecialistToCell(day: number, localTime: string, specialistId: string) {
    setSchedule((current) => {
      const key = cellKey(day, localTime);
      const existing = current.slots.find((slot) => slot.id === key);
      if (existing) {
        if (existing.specialist_ids.includes(specialistId)) return current;
        return {
          ...current,
          trigger: { kind: "schedule", event: null },
          slots: current.slots.map((slot) =>
            slot.id === key
              ? {
                  ...slot,
                  specialist_ids: [...slot.specialist_ids, specialistId],
                }
              : slot,
          ),
        };
      }
      return {
        ...current,
        trigger: { kind: "schedule", event: null },
        slots: [
          ...current.slots,
          {
            id: key,
            label: cellLabel(day, localTime),
            local_time: localTime,
            weekdays: [day],
            specialist_ids: [specialistId],
          },
        ],
      };
    });
  }

  function removeSpecialistFromCell(day: number, localTime: string, specialistId: string) {
    setSchedule((current) => ({
      ...current,
      slots: current.slots
        .map((slot) => {
          if (slot.local_time !== localTime || !slot.weekdays.includes(day)) {
            return slot;
          }
          const nextIds = slot.specialist_ids.filter((id) => id !== specialistId);
          const nextWeekdays =
            nextIds.length === 0
              ? slot.weekdays.filter((weekday) => weekday !== day)
              : slot.weekdays;
          return {
            ...slot,
            specialist_ids: nextIds,
            weekdays: nextWeekdays,
          };
        })
        .filter((slot) => slot.specialist_ids.length > 0 && slot.weekdays.length > 0),
    }));
  }

  function addTimeRow() {
    if (!nextTimeRow) return;
    setExtraTimes((current) => [...current, nextTimeRow].sort());
  }

  return (
    <Card>
      <div className="space-y-4 p-1">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <CardHeader
            className="p-0"
            title="Capacity"
            subtitle="When specialists are free to pick up tracker work. Routines on this process — daily, retro, healthcheck, tech / qa / security review — fire on their own cron and are summarised below."
          />
          <form
            action="/api/process/config-propose"
            method="post"
            className="flex flex-wrap items-center gap-2"
          >
            <ProcessConfigProposalFields
              workspaceId={workspaceId}
              repoId={repoId}
              config={config}
              processConfig={processConfig}
              changeSummary={changeSummary}
            />
            <button
              type="submit"
              disabled={!repoId || !dirty || warnings.length > 0}
              className="inline-flex h-9 items-center justify-center gap-1.5 whitespace-nowrap rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 text-xs font-semibold text-ink shadow-glow transition hover:brightness-110 active:scale-[0.99] disabled:cursor-not-allowed disabled:bg-none disabled:bg-white/[0.05] disabled:text-white/35 disabled:shadow-none"
            >
              Review changes
            </button>
          </form>
        </div>

        <ProcessReviewSummary
          initial={initialReviewProcess}
          draft={processDraft}
          changedAreas={dirty ? ["Flow schedule slots changed"] : []}
        />

        <RoutinesSummary routines={process.routines} />

        {/* Time zone shrunk to a tight inline control — the previous
            220px label + full-width input + duplicate help text ate
            ~120px of vertical space for one timezone string. */}
        <label className="flex flex-wrap items-center gap-2 text-xs text-white/55">
          <span className="text-[10px] font-bold uppercase tracking-widest text-white/45">
            Time zone
          </span>
          <input
            value={schedule.time_zone}
            onChange={(e) =>
              setSchedule((current) => ({
                ...current,
                time_zone: e.target.value || "UTC",
              }))
            }
            className="w-32 rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 font-mono text-[12px] text-white outline-none focus:border-aqua/40"
          />
          <span className="text-white/40">
            · drag a specialist into a day/time cell to assign capacity
          </span>
        </label>

        {warnings.length > 0 ? (
          <div className="rounded-xl border border-coral/25 bg-coral/[0.05] px-3 py-2 text-xs text-coral/90">
            {warnings.join(" ")}
          </div>
        ) : null}

        <div className="grid gap-3 xl:grid-cols-[200px_minmax(0,1fr)]">
          <aside className="max-h-[640px] overflow-y-auto p-1">
            <div className="sticky top-0 -mx-3 mb-2 -mt-3 border-b border-white/5 bg-black/85 px-3 py-2 backdrop-blur">
              <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-aqua/70">
                Roles · drag to a cell
              </div>
            </div>
            <div className="grid gap-1.5">
              {process.specialists.map((specialist) => (
                <button
                  key={specialist.id}
                  type="button"
                  draggable
                  onDragStart={(event) => {
                    event.dataTransfer.setData(DRAG_SPECIALIST_MIME, specialist.id);
                    event.dataTransfer.effectAllowed = "copy";
                  }}
                  title={specialist.role}
                  className={[
                    "cursor-grab rounded-lg border px-2.5 py-1.5 text-left text-xs font-semibold transition active:cursor-grabbing",
                    assignedSpecialistIds.has(specialist.id)
                      ? "border-aqua/25 bg-aqua/[0.08] text-aqua/95"
                      : "border-white/10 bg-white/[0.035] text-white hover:border-aqua/25",
                  ].join(" ")}
                >
                  {specialist.name}
                </button>
              ))}
            </div>
          </aside>

          <section className="overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 bg-white/[0.035] px-4 py-3">
              <div>
                <div className="text-sm font-semibold text-white">Capacity calendar</div>
                <div className="text-xs text-white/45">
                  {timeRows.length} time windows · local to {schedule.time_zone || "UTC"}
                </div>
              </div>
              <div className="flex items-center gap-3 text-[10px] uppercase tracking-widest">
                <span className="inline-flex items-center gap-1.5 text-aqua/85">
                  <span className="h-2 w-2 rounded border border-aqua/40 bg-aqua/[0.10]" aria-hidden />
                  specialist · drag
                </span>
                <span className="inline-flex items-center gap-1.5 text-sun/85">
                  <span className="h-2 w-2 rounded border border-sun/40 bg-sun/[0.10]" aria-hidden />
                  routine · cron
                </span>
                <button
                  type="button"
                  onClick={addTimeRow}
                  disabled={!nextTimeRow}
                  title={nextTimeRow ? undefined : TIME_WINDOW_EXHAUSTED_TOOLTIP}
                  className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-[11px] font-bold text-white/60 transition hover:border-aqua/25 hover:text-aqua disabled:cursor-not-allowed disabled:border-white/5 disabled:text-white/25 disabled:hover:border-white/5 disabled:hover:text-white/25"
                >
                  + Time window
                </button>
              </div>
            </div>

            <div className="overflow-x-auto">
              <div className="min-w-[980px]">
                <div className="grid grid-cols-[92px_repeat(7,minmax(120px,1fr))] border-b border-white/10">
                  <div className="bg-black/20 px-3 py-2 text-[10px] font-bold uppercase tracking-widest text-white/35">
                    Time
                  </div>
                  {WEEKDAYS.map((day) => (
                    <div
                      key={day.id}
                      className="border-l border-white/10 px-3 py-2 text-center text-[10px] font-bold uppercase tracking-widest text-white/45"
                    >
                      {day.label}
                    </div>
                  ))}
                </div>
                {timeRows.map((localTime) => (
                  <div
                    key={localTime}
                    className="grid grid-cols-[92px_repeat(7,minmax(120px,1fr))] border-b border-white/10 last:border-b-0"
                  >
                    <div className="flex items-start justify-center bg-black/20 px-3 py-4 font-mono text-sm font-semibold text-aqua/80">
                      {localTime}
                    </div>
                    {WEEKDAYS.map((day) => {
                      const ids = assignments.get(cellKey(day.id, localTime)) ?? [];
                      const routinesHere =
                        routineMarkers.get(cellKey(day.id, localTime)) ?? [];
                      return (
                        <CalendarCell
                          key={`${day.id}-${localTime}`}
                          day={day.id}
                          localTime={localTime}
                          specialistIds={ids}
                          process={process}
                          routines={routinesHere}
                          routineLabel={routineLabel}
                          onDropSpecialist={addSpecialistToCell}
                          onRemoveSpecialist={removeSpecialistFromCell}
                        />
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>
            {routineProjection.continuous.length > 0 && (
              <div className="border-t border-white/10 bg-black/20 px-4 py-3">
                <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.22em] text-sun/80">
                  Continuous routines · fire too often for the grid
                </div>
                <div className="flex flex-wrap gap-2">
                  {routineProjection.continuous.map(({ routine, cadence }) => (
                    <span
                      key={routine.id}
                      className="inline-flex items-center gap-1.5 rounded-full border border-sun/30 bg-sun/[0.07] px-3 py-1 text-[11px] font-semibold text-sun/90"
                    >
                      <span className="h-1.5 w-1.5 rounded-full bg-sun" aria-hidden />
                      {routineLabel.get(routine.id) ?? routine.name ?? routine.id}
                      <span className="font-normal text-sun/60">· {cadence}</span>
                    </span>
                  ))}
                </div>
              </div>
            )}
          </section>
        </div>
      </div>
    </Card>
  );
}

function CalendarCell({
  day,
  localTime,
  specialistIds,
  process,
  routines,
  routineLabel,
  onDropSpecialist,
  onRemoveSpecialist,
}: {
  day: number;
  localTime: string;
  specialistIds: string[];
  process: ApiProcess;
  routines: ApiProcessRoutine[];
  routineLabel: Map<string, string>;
  onDropSpecialist: (day: number, localTime: string, specialistId: string) => void;
  onRemoveSpecialist: (day: number, localTime: string, specialistId: string) => void;
}) {
  const [hovering, setHovering] = useState(false);
  const byId = useMemo(
    () => new Map(process.specialists.map((specialist) => [specialist.id, specialist])),
    [process.specialists],
  );

  function handleDrop(event: ReactDragEvent<HTMLDivElement>) {
    event.preventDefault();
    setHovering(false);
    const specialistId = event.dataTransfer.getData(DRAG_SPECIALIST_MIME);
    if (!specialistId) return;
    onDropSpecialist(day, localTime, specialistId);
  }

  return (
    <div
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = "copy";
        setHovering(true);
      }}
      onDragLeave={() => setHovering(false)}
      onDrop={handleDrop}
      className={[
        "min-h-[112px] border-l border-white/10 p-2 transition",
        hovering
          ? "bg-aqua/[0.12] shadow-[inset_0_0_0_1px_rgba(99,245,255,0.35)]"
          : specialistIds.length
            ? "bg-aqua/[0.035]"
            : "bg-white/[0.015] hover:bg-white/[0.035]",
      ].join(" ")}
    >
      <div className="flex min-h-full flex-col gap-1.5">
        {/* Routines fire on their own cron, you can't drag them around.
            Render as a thin amber stripe at the top so they're clearly
            distinct from the specialist drop targets below. Empty
            stripe is hidden entirely. */}
        {routines.length > 0 ? (
          <div
            className="flex flex-wrap items-center gap-1 rounded-md border border-sun/25 bg-sun/[0.07] px-1.5 py-1"
            title="Routines firing in this slot — fire on cron, not capacity"
          >
            <span className="text-[9px]" aria-hidden>⏱</span>
            {routines.map((r, idx) => (
              <span key={r.id} className="text-[9px] font-bold uppercase tracking-widest text-sun/90">
                {routineLabel.get(r.id) ?? r.name ?? r.id}
                {idx < routines.length - 1 ? <span className="text-sun/40"> · </span> : null}
              </span>
            ))}
          </div>
        ) : null}
        {/* Specialist pills are the interactive bit — drag them in,
            click to remove. They consume capacity (they're "available
            in this slot"); routines don't. */}
        {specialistIds.map((id) => {
          const specialist = byId.get(id);
          return (
            <button
              key={id}
              type="button"
              onClick={() => onRemoveSpecialist(day, localTime, id)}
              className="group rounded-xl border border-aqua/30 bg-aqua/[0.10] px-2 py-1.5 text-left transition hover:border-coral/35 hover:bg-coral/[0.08]"
              title="Click to remove this specialist from the slot"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[11px] font-semibold text-white">
                  {specialist?.name ?? id}
                </span>
                <span className="text-[10px] text-aqua/60 group-hover:text-coral">
                  ✕
                </span>
              </div>
            </button>
          );
        })}
        {specialistIds.length === 0 ? (
          <div className="grid flex-1 place-items-center rounded-xl border border-dashed border-white/10 text-center text-[11px] leading-4 text-white/25">
            Drop role
          </div>
        ) : null}
      </div>
    </div>
  );
}

function normalizedSchedule(process: ApiProcess): ApiProcessSchedule {
  return process.schedule ?? {
    trigger: { kind: "schedule", event: null },
    time_zone:
      typeof Intl !== "undefined"
        ? Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
        : "UTC",
    slots: [],
  };
}

function assignmentMap(schedule: ApiProcessSchedule): Map<string, string[]> {
  const out = new Map<string, string[]>();
  for (const slot of schedule.slots) {
    for (const day of slot.weekdays) {
      const key = cellKey(day, slot.local_time);
      const current = out.get(key) ?? [];
      out.set(key, Array.from(new Set([...current, ...slot.specialist_ids])));
    }
  }
  return out;
}

function cellKey(day: number, localTime: string) {
  return `slot_${localTime.replace(":", "")}_${day}`;
}

function cellLabel(day: number, localTime: string) {
  const weekday = WEEKDAYS.find((item) => item.id === day)?.label ?? `Day ${day}`;
  return `${weekday} ${localTime}`;
}

function duplicateWarnings(slot: ApiProcessScheduleSlot): string[] {
  const duplicates = slot.specialist_ids.filter(
    (id, index) => slot.specialist_ids.indexOf(id) !== index,
  );
  return duplicates.length
    ? [`Slot "${slot.label ?? slot.id}" contains duplicate specialists.`]
    : [];
}

function RoutinesSummary({
  routines,
}: {
  routines: ApiProcessRoutine[];
}) {
  // Drop SDLC cadence lanes (those are specialists, not routines) but
  // keep legacy / orphan routine ids visible — they signal drift the
  // operator needs to clean up. Canonical six get the champagne pill;
  // anything else gets a "legacy" badge so it stands out.
  const visible = routines.filter((r) => !HIDDEN_ROUTINE_IDS.has(r.id));
  const labelById = new Map(BUILTIN_ROUTINE_CATALOG.map((c) => [c.id, c.name] as const));
  return (
    <div className="p-1">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/55">
          Routines on this process
        </div>
        <span className="text-[10px] uppercase tracking-widest text-white/35">
          Drag specialists into cells; routines fire on the cron below.
        </span>
      </div>
      {visible.length === 0 ? (
        <p className="mt-3 text-xs text-white/55">
          No routines configured on this process. The seed bundle ships
          the canonical six (Daily, Retro, Healthcheck, Tech / QA / Security
          review) — re-seed the repo to add them.
        </p>
      ) : (
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((routine) => {
            const enabled = routine.enabled !== false;
            const canonical = isCanonicalRoutineId(routine.id);
            const cron = routine.schedule?.trim() ?? "";
            let nextRun = "—";
            if (cron) {
              try {
                nextRun = formatNextRun(cron);
              } catch {
                nextRun = "invalid cron";
              }
            }
            const label = labelById.get(routine.id) ?? routine.name ?? routine.id;
            return (
              <div
                key={routine.id}
                className={[
                  "rounded-xl border p-2.5",
                  !canonical
                    ? "border-sun/35 bg-sun/[0.05]"
                    : enabled
                      ? "border-aqua/25 bg-aqua/[0.05]"
                      : "border-white/10 bg-white/[0.02] opacity-65",
                ].join(" ")}
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="truncate text-xs font-semibold text-white">
                      {label}
                    </span>
                    {!canonical && (
                      <span
                        className="shrink-0 rounded-full border border-sun/40 bg-sun/[0.10] px-1.5 py-0.5 text-[8px] font-bold uppercase tracking-widest text-sun"
                        title="Pre-canonical routine id — leftover from an older seed. Clean up the DB row to remove it from this view."
                      >
                        legacy
                      </span>
                    )}
                  </div>
                  <span
                    className={[
                      "h-1.5 w-1.5 rounded-full",
                      enabled ? (canonical ? "bg-aqua" : "bg-sun") : "bg-white/30",
                    ].join(" ")}
                    aria-hidden
                  />
                </div>
                <div className="mt-1 truncate font-mono text-[10px] text-white/50">
                  {cron || "no schedule"}
                </div>
                <div className="mt-1 text-[10px] text-white/45">
                  {enabled ? (cron ? `Next: ${nextRun}` : "Manual trigger") : "Disabled"}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
