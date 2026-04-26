"use client";

import {
  type RoutineScheduleMode,
  type RoutineScheduleV1,
} from "@/lib/routine-schedule-spec";
import { COMMON_TIME_ZONES } from "./timezone-options";

const DOW: { d: number; label: string }[] = [
  { d: 0, label: "Sun" },
  { d: 1, label: "Mon" },
  { d: 2, label: "Tue" },
  { d: 3, label: "Wed" },
  { d: 4, label: "Thu" },
  { d: 5, label: "Fri" },
  { d: 6, label: "Sat" },
];

const MODE_OPTIONS: { value: RoutineScheduleMode; label: string }[] = [
  { value: "every_hours_utc", label: "Every N hours (UTC clock)" },
  { value: "daily", label: "Daily" },
  { value: "weekdays", label: "Weekdays (Mon–Fri)" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "expert_utc", label: "Custom cron (UTC)" },
];

function ensureZone(spec: RoutineScheduleV1, extras: string[]): string {
  const z = spec.time_zone || "UTC";
  if (COMMON_TIME_ZONES.includes(z) || extras.includes(z)) return z;
  return z;
}

export function RoutineScheduleForm({
  spec,
  onChange,
  allZones,
}: {
  spec: RoutineScheduleV1;
  onChange: (next: RoutineScheduleV1) => void;
  /** Browser-reported zone first if not in common list. */
  allZones: string[];
}) {
  const timeZone = ensureZone(spec, allZones);
  const zoneOptions = [...new Set([...COMMON_TIME_ZONES, ...allZones, timeZone])].sort();
  return (
    <div className="space-y-3">
      <label className="block">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
          Time zone
        </span>
        <select
          value={timeZone}
          onChange={(e) =>
            onChange({ ...spec, v: 1, time_zone: e.target.value })
          }
          className="w-full rounded-xl border border-white/10 bg-ink px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
        >
          {zoneOptions.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
        <p className="mt-1 text-[11px] text-white/35">
          Local wall time; cadence in the file is still a 5-field UTC cron for runners.
        </p>
      </label>

      <label className="block">
        <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
          Schedule type
        </span>
        <select
          value={spec.mode}
          onChange={(e) => {
            const mode = e.target.value as RoutineScheduleMode;
            const next: RoutineScheduleV1 = { ...spec, v: 1, mode };
            if (mode === "monthly" && !next.day_of_month) next.day_of_month = 1;
            if (mode === "every_hours_utc" && !next.every_hours) next.every_hours = 1;
            if (mode === "weekly" && !next.weekdays?.length) {
              next.weekdays = [1, 2, 3, 4, 5];
            }
            onChange(next);
          }}
          className="w-full rounded-xl border border-white/10 bg-ink px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
        >
          {MODE_OPTIONS.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </select>
      </label>

      {spec.mode === "every_hours_utc" ? (
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
            Every (hours, UTC)
          </span>
          <input
            type="number"
            min={1}
            max={23}
            value={spec.every_hours ?? 1}
            onChange={(e) =>
              onChange({
                ...spec,
                v: 1,
                every_hours: Math.min(23, Math.max(1, parseInt(e.target.value, 10) || 1)),
              })
            }
            className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
          />
        </label>
      ) : null}

      {spec.mode === "monthly" ? (
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
            Day of month
          </span>
          <input
            type="number"
            min={1}
            max={31}
            value={spec.day_of_month ?? 1}
            onChange={(e) =>
              onChange({
                ...spec,
                v: 1,
                day_of_month: Math.min(31, Math.max(1, parseInt(e.target.value, 10) || 1)),
              })
            }
            className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
          />
        </label>
      ) : null}

      {spec.mode === "weekly" ? (
        <div>
          <span className="mb-2 block text-[10px] font-bold uppercase tracking-widest text-white/45">
            Days
          </span>
          <div className="flex flex-wrap gap-1.5">
            {DOW.map(({ d, label }) => {
              const on = (spec.weekdays ?? []).includes(d);
              return (
                <button
                  key={d}
                  type="button"
                  onClick={() => {
                    const current = new Set(spec.weekdays ?? []);
                    if (on) current.delete(d);
                    else current.add(d);
                    onChange({
                      ...spec,
                      v: 1,
                      weekdays: Array.from(current).sort((a, b) => a - b),
                    });
                  }}
                  className={[
                    "rounded-lg px-2.5 py-1 text-xs font-semibold transition",
                    on
                      ? "border border-aqua/40 bg-aqua/15 text-aqua"
                      : "border border-white/10 text-white/45 hover:border-white/20",
                  ].join(" ")}
                >
                  {label}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {spec.mode !== "every_hours_utc" && spec.mode !== "expert_utc" ? (
        <div className="grid grid-cols-2 gap-2">
          <label className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
              Hour
            </span>
            <input
              type="number"
              min={0}
              max={23}
              value={spec.local_hour ?? 9}
              onChange={(e) =>
                onChange({
                  ...spec,
                  v: 1,
                  local_hour: Math.min(23, Math.max(0, parseInt(e.target.value, 10) || 0)),
                })
              }
              className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
              Minute
            </span>
            <input
              type="number"
              min={0}
              max={59}
              value={spec.local_minute ?? 0}
              onChange={(e) =>
                onChange({
                  ...spec,
                  v: 1,
                  local_minute: Math.min(59, Math.max(0, parseInt(e.target.value, 10) || 0)),
                })
              }
              className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
            />
          </label>
        </div>
      ) : null}

      {spec.mode === "expert_utc" ? (
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
            UTC cron (5 fields: min hour dom month dow)
          </span>
          <input
            value={spec.cron_utc ?? ""}
            onChange={(e) =>
              onChange({ ...spec, v: 1, cron_utc: e.target.value })
            }
            placeholder="0 9 * * 1-5"
            className="w-full rounded-xl border border-white/10 bg-white/[0.04] font-mono px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
          />
        </label>
      ) : null}
    </div>
  );
}
