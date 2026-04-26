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
  ApiProcessSchedule,
  ApiProcessScheduleSlot,
  ApiRepoConfig,
} from "@/lib/api/client";
import { processConfigFromApiProcess } from "./process-config";
import { ProcessConfigProposalFields } from "./process-config-proposal-fields";
import { ProcessReviewSummary, processChangeSummary } from "./process-review-summary";

const WEEKDAYS = [
  { id: 1, label: "Mon" },
  { id: 2, label: "Tue" },
  { id: 3, label: "Wed" },
  { id: 4, label: "Thu" },
  { id: 5, label: "Fri" },
  { id: 6, label: "Sat" },
  { id: 0, label: "Sun" },
] as const;
const DEFAULT_TIMES = ["09:00", "13:00", "17:00"];
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

  const timeRows = useMemo(() => knownTimes(schedule, extraTimes), [schedule, extraTimes]);
  const assignments = useMemo(() => assignmentMap(schedule), [schedule]);
  const assignedSpecialistIds = useMemo(
    () => new Set(schedule.slots.flatMap((slot) => slot.specialist_ids)),
    [schedule.slots],
  );

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
    const nextTime = nextAvailableTime(timeRows);
    if (!nextTime) return;
    setExtraTimes((current) => [...current, nextTime].sort());
  }

  return (
    <Card>
      <div className="space-y-4 p-1">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <CardHeader
            className="p-0"
            title="Flow schedule"
            subtitle="Capacity windows for ticket-driven process steps. Routines have their own cadence and never pick tracker tickets."
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
              className="h-9 whitespace-nowrap rounded-full border border-aqua/30 bg-aqua/10 px-4 text-xs font-bold text-aqua transition hover:bg-aqua/15 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.05] disabled:text-white/35"
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

        <div className="rounded-2xl border border-white/10 bg-[radial-gradient(circle_at_top_left,rgba(99,245,255,0.10),transparent_28%),rgba(255,255,255,0.03)] p-3">
          <div className="grid gap-3 sm:grid-cols-[220px_minmax(0,1fr)]">
            <label className="block">
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
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
                className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
              />
            </label>
            <div className="text-xs leading-relaxed text-white/50">
              Ship already knows the available capacity slots from the process config.
              Drag a specialist into a day/time cell to let that role pick matching
              tickets in that window.
            </div>
          </div>
        </div>

        {warnings.length > 0 ? (
          <div className="rounded-xl border border-coral/25 bg-coral/[0.05] px-3 py-2 text-xs text-coral/90">
            {warnings.join(" ")}
          </div>
        ) : null}

        <div className="grid gap-3 xl:grid-cols-[240px_minmax(0,1fr)]">
          <aside className="rounded-3xl border border-white/10 bg-black/25 p-3">
            <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-aqua/70">
              Role palette
            </div>
            <p className="mt-1 text-xs leading-relaxed text-white/45">
              Drag roles into the calendar. Assigned roles stay highlighted so
              it is obvious what already has coverage.
            </p>
            <div className="mt-3 grid gap-2">
              {process.specialists.map((specialist) => (
                <button
                  key={specialist.id}
                  type="button"
                  draggable
                  onDragStart={(event) => {
                    event.dataTransfer.setData(DRAG_SPECIALIST_MIME, specialist.id);
                    event.dataTransfer.effectAllowed = "copy";
                  }}
                  className={[
                    "cursor-grab rounded-2xl border px-3 py-2 text-left transition active:cursor-grabbing",
                    assignedSpecialistIds.has(specialist.id)
                      ? "border-aqua/25 bg-aqua/[0.08]"
                      : "border-white/10 bg-white/[0.035] hover:border-aqua/25",
                  ].join(" ")}
                >
                  <div className="text-xs font-semibold text-white">
                    {specialist.name}
                  </div>
                  <div className="mt-1 line-clamp-2 text-[11px] leading-4 text-white/40">
                    {specialist.role}
                  </div>
                </button>
              ))}
            </div>
          </aside>

          <section className="overflow-hidden rounded-3xl border border-white/10 bg-[#050a15] shadow-2xl shadow-black/30">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 bg-white/[0.035] px-4 py-3">
              <div>
                <div className="text-sm font-semibold text-white">Capacity calendar</div>
                <div className="text-xs text-white/45">
                  {timeRows.length} time windows · local to {schedule.time_zone || "UTC"}
                </div>
              </div>
              <button
                type="button"
                onClick={addTimeRow}
                className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1.5 text-xs font-bold text-white/60 transition hover:border-aqua/25 hover:text-aqua"
              >
                Add time window
              </button>
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
                      return (
                        <CalendarCell
                          key={`${day.id}-${localTime}`}
                          day={day.id}
                          localTime={localTime}
                          specialistIds={ids}
                          process={process}
                          onDropSpecialist={addSpecialistToCell}
                          onRemoveSpecialist={removeSpecialistFromCell}
                        />
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>
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
  onDropSpecialist,
  onRemoveSpecialist,
}: {
  day: number;
  localTime: string;
  specialistIds: string[];
  process: ApiProcess;
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
        {specialistIds.map((id) => {
          const specialist = byId.get(id);
          return (
            <button
              key={id}
              type="button"
              onClick={() => onRemoveSpecialist(day, localTime, id)}
              className="group rounded-xl border border-aqua/20 bg-aqua/[0.09] px-2 py-1.5 text-left transition hover:border-coral/35 hover:bg-coral/[0.08]"
              title="Click to remove from this slot"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-[11px] font-semibold text-white">
                  {specialist?.name ?? id}
                </span>
                <span className="text-[10px] text-aqua/60 group-hover:text-coral">
                  remove
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

function knownTimes(schedule: ApiProcessSchedule, extras: string[] = []): string[] {
  const times = new Set(
    [...schedule.slots.map((slot) => slot.local_time), ...extras]
      .filter((value): value is string => Boolean(value)),
  );
  if (times.size === 0) {
    for (const time of DEFAULT_TIMES) times.add(time);
  }
  return Array.from(times).sort();
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

function nextAvailableTime(existing: string[]) {
  for (const time of DEFAULT_TIMES) {
    if (!existing.includes(time)) return time;
  }
  return null;
}
