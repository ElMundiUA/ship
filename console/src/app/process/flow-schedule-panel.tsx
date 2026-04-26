"use client";

import { useEffect, useMemo, useState } from "react";

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

  useEffect(() => {
    setSchedule(normalizedSchedule(process));
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

  function patchSlot(slotId: string, patch: Partial<ApiProcessScheduleSlot>) {
    setSchedule((current) => ({
      ...current,
      slots: current.slots.map((slot) =>
        slot.id === slotId ? { ...slot, ...patch } : slot,
      ),
    }));
  }

  function addSlot() {
    setSchedule((current) => ({
      ...current,
      trigger: { kind: "schedule", event: null },
      slots: [
        ...current.slots,
        {
          id: uniqueSlotId(current.slots),
          label: "New slot",
          local_time: "09:00",
          weekdays: [1, 2, 3, 4, 5],
          specialist_ids: [process.specialists[0]?.id ?? "business_analyst"],
        },
      ],
    }));
  }

  function removeSlot(slotId: string) {
    setSchedule((current) => ({
      ...current,
      slots: current.slots.filter((slot) => slot.id !== slotId),
    }));
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

        <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
          <div className="grid gap-3 sm:grid-cols-[180px_minmax(0,1fr)]">
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
              Ship opens capacity for the selected specialists at the local wall time below.
              If matching tickets do not exist, the backend agent does not start.
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-white/10 bg-white/[0.025] px-3 py-2 text-xs leading-relaxed text-white/45">
          First build guardrail: config/UI validation prevents duplicate specialists
          in a slot. Deep runtime locks by process, slot start, and specialist remain
          a follow-up executor phase.
        </div>

        {warnings.length > 0 ? (
          <div className="rounded-xl border border-coral/25 bg-coral/[0.05] px-3 py-2 text-xs text-coral/90">
            {warnings.join(" ")}
          </div>
        ) : null}

        <div className="space-y-3">
          {schedule.slots.map((slot) => (
            <div
              key={slot.id}
              className="rounded-xl border border-white/10 bg-white/[0.035] p-3"
            >
              <div className="grid gap-3 lg:grid-cols-[160px_120px_minmax(0,1fr)_auto]">
                <label className="block">
                  <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
                    Slot name
                  </span>
                  <input
                    value={slot.label ?? ""}
                    onChange={(e) => patchSlot(slot.id, { label: e.target.value })}
                    placeholder={slot.id}
                    className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
                    Local time
                  </span>
                  <input
                    type="time"
                    value={slot.local_time}
                    onChange={(e) => patchSlot(slot.id, { local_time: e.target.value })}
                    className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
                  />
                </label>
                <div>
                  <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
                    Specialists
                  </span>
                  <div className="flex flex-wrap gap-2">
                    {process.specialists.map((specialist) => {
                      const checked = slot.specialist_ids.includes(specialist.id);
                      return (
                        <label
                          key={specialist.id}
                          className={[
                            "cursor-pointer rounded-full border px-3 py-1 text-xs font-semibold transition",
                            checked
                              ? "border-aqua/35 bg-aqua/10 text-aqua"
                              : "border-white/10 bg-white/[0.03] text-white/55 hover:border-white/20",
                          ].join(" ")}
                        >
                          <input
                            type="checkbox"
                            className="sr-only"
                            checked={checked}
                            onChange={(e) => {
                              const next = e.target.checked
                                ? [...slot.specialist_ids, specialist.id]
                                : slot.specialist_ids.filter((id) => id !== specialist.id);
                              patchSlot(slot.id, { specialist_ids: Array.from(new Set(next)) });
                            }}
                          />
                          {specialist.name}
                        </label>
                      );
                    })}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => removeSlot(slot.id)}
                  className="self-end rounded-full border border-coral/25 px-3 py-2 text-xs font-semibold text-coral/90 hover:bg-coral/10"
                >
                  Remove
                </button>
              </div>
              <div className="mt-3 flex flex-wrap gap-2">
                {WEEKDAYS.map((day) => (
                  <label key={day.id} className="text-xs text-white/55">
                    <input
                      type="checkbox"
                      checked={slot.weekdays.includes(day.id)}
                      onChange={(e) => {
                        const next = e.target.checked
                          ? [...slot.weekdays, day.id]
                          : slot.weekdays.filter((value) => value !== day.id);
                        patchSlot(slot.id, { weekdays: next.sort((a, b) => a - b) });
                      }}
                      className="mr-1 accent-aqua"
                    />
                    {day.label}
                  </label>
                ))}
              </div>
              <p className="mt-3 text-xs text-white/45">
                At {slot.local_time}, {slot.specialist_ids.length
                  ? namesFor(slot.specialist_ids, process)
                  : "no specialists"} may pick work if matching tickets exist.
              </p>
            </div>
          ))}
        </div>

        <button
          type="button"
          onClick={addSlot}
          className="rounded-full border border-white/10 bg-white/[0.05] px-4 py-2 text-xs font-bold text-white/75 transition hover:border-aqua/25 hover:text-aqua"
        >
          Add capacity slot
        </button>
      </div>
    </Card>
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

function duplicateWarnings(slot: ApiProcessScheduleSlot): string[] {
  const duplicates = slot.specialist_ids.filter(
    (id, index) => slot.specialist_ids.indexOf(id) !== index,
  );
  return duplicates.length
    ? [`Slot "${slot.label ?? slot.id}" contains duplicate specialists.`]
    : [];
}

function uniqueSlotId(slots: ApiProcessScheduleSlot[]) {
  for (let index = 1; index < 1000; index += 1) {
    const id = `slot_${index}`;
    if (!slots.some((slot) => slot.id === id)) return id;
  }
  return `slot_${Date.now()}`;
}

function namesFor(ids: string[], process: ApiProcess) {
  const byId = new Map(process.specialists.map((specialist) => [specialist.id, specialist.name]));
  return ids.map((id) => byId.get(id) ?? id).join(" and ");
}
