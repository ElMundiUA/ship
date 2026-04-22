"use client";

import { useMemo } from "react";

import { Badge } from "@/components/ui";

import {
  cronToSpec,
  humanizeCron,
  specToCron,
  type ScheduleSpec,
} from "./cron";

/**
 * Outlook-style schedule picker.
 *
 * Instead of making users speak cron, we expose the small handful of
 * shapes that actually cover ~95% of lane usage:
 *
 * - **Daily** at HH:MM
 * - **Weekly** at HH:MM on a multi-select of weekdays
 * - **Monthly** at HH:MM on a specific day of the month
 * - **Custom** — an advanced escape hatch that lets an operator type
 *   raw cron if the presets don't fit (for example ``*\/15 * * * 1-5``).
 *
 * Everything is UTC because GitHub Actions ``schedule:`` is UTC;
 * we surface that in the human preview so there are no surprises.
 *
 * The component is controlled: the parent owns the
 * {@link ScheduleSpec} and the string cron derived from it (via
 * {@link specToCron}) — we never write directly. This keeps the
 * Library catalog's "Add" flow and the Active tab's "Edit schedule"
 * drawer using the same widget with no state-sync surprises.
 */

const WEEKDAYS: { label: string; long: string; value: number }[] = [
  { label: "Mon", long: "Monday", value: 1 },
  { label: "Tue", long: "Tuesday", value: 2 },
  { label: "Wed", long: "Wednesday", value: 3 },
  { label: "Thu", long: "Thursday", value: 4 },
  { label: "Fri", long: "Friday", value: 5 },
  { label: "Sat", long: "Saturday", value: 6 },
  { label: "Sun", long: "Sunday", value: 0 },
];

const QUICK_PRESETS: { label: string; make: () => ScheduleSpec }[] = [
  {
    label: "Weekdays 09:00",
    make: () => ({ kind: "weekly", hour: 9, minute: 0, weekdays: [1, 2, 3, 4, 5] }),
  },
  {
    label: "Weekdays 06:00",
    make: () => ({ kind: "weekly", hour: 6, minute: 0, weekdays: [1, 2, 3, 4, 5] }),
  },
  {
    label: "Daily 04:00",
    make: () => ({ kind: "daily", hour: 4, minute: 0 }),
  },
  {
    label: "Mondays 06:00",
    make: () => ({ kind: "weekly", hour: 6, minute: 0, weekdays: [1] }),
  },
];

export function ScheduleWizard({
  spec,
  onChange,
}: {
  spec: ScheduleSpec;
  onChange: (next: ScheduleSpec) => void;
}) {
  const cron = useMemo(() => specToCron(spec), [spec]);
  const human = useMemo(() => humanizeCron(cron), [cron]);

  const freq = spec.kind;

  function setFreq(next: ScheduleSpec["kind"]) {
    const base = "kind" in spec && spec.kind !== "custom" ? spec : null;
    const hour = base ? base.hour : 9;
    const minute = base ? base.minute : 0;
    if (next === "daily") onChange({ kind: "daily", hour, minute });
    else if (next === "weekly")
      onChange({
        kind: "weekly",
        hour,
        minute,
        weekdays:
          spec.kind === "weekly" && spec.weekdays.length
            ? spec.weekdays
            : [1, 2, 3, 4, 5],
      });
    else if (next === "monthly")
      onChange({
        kind: "monthly",
        hour,
        minute,
        dayOfMonth: spec.kind === "monthly" ? spec.dayOfMonth : 1,
      });
    else onChange({ kind: "custom", cron });
  }

  function setTime(hour: number, minute: number) {
    if (spec.kind === "custom") return;
    onChange({ ...spec, hour, minute });
  }

  function toggleWeekday(d: number) {
    if (spec.kind !== "weekly") return;
    const has = spec.weekdays.includes(d);
    const next = has
      ? spec.weekdays.filter((x) => x !== d)
      : [...spec.weekdays, d];
    onChange({ ...spec, weekdays: next });
  }

  function setDayOfMonth(n: number) {
    if (spec.kind !== "monthly") return;
    onChange({ ...spec, dayOfMonth: Math.max(1, Math.min(31, n)) });
  }

  const timeHour = spec.kind === "custom" ? 9 : spec.hour;
  const timeMinute = spec.kind === "custom" ? 0 : spec.minute;

  return (
    <div className="space-y-4">
      {/* Frequency */}
      <Field label="Frequency">
        <div className="flex flex-wrap gap-1.5">
          {(
            [
              { id: "daily", label: "Daily" },
              { id: "weekly", label: "Weekly" },
              { id: "monthly", label: "Monthly" },
              { id: "custom", label: "Custom (cron)" },
            ] as const
          ).map((opt) => {
            const active = freq === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                onClick={() => setFreq(opt.id)}
                className={
                  "rounded-full border px-3 py-1 text-[11px] font-semibold transition " +
                  (active
                    ? "border-aqua/50 bg-aqua/15 text-aqua"
                    : "border-white/15 bg-white/[0.04] text-white/70 hover:border-white/30 hover:text-white")
                }
              >
                {opt.label}
              </button>
            );
          })}
        </div>
      </Field>

      {/* Time (all non-custom frequencies) */}
      {spec.kind !== "custom" ? (
        <Field label="Time (UTC)">
          <div className="flex items-center gap-2">
            <NumberInput
              value={timeHour}
              min={0}
              max={23}
              width="w-16"
              onChange={(n) => setTime(n, timeMinute)}
            />
            <span className="text-white/55">:</span>
            <NumberInput
              value={timeMinute}
              min={0}
              max={59}
              step={5}
              width="w-16"
              onChange={(n) => setTime(timeHour, n)}
            />
            <span className="ml-1 text-[11px] text-white/45">UTC</span>
          </div>
        </Field>
      ) : null}

      {/* Weekly: weekday pills */}
      {spec.kind === "weekly" ? (
        <Field label="Days of the week">
          <div className="flex flex-wrap gap-1.5">
            {WEEKDAYS.map((d) => {
              const active = spec.weekdays.includes(d.value);
              return (
                <button
                  key={d.value}
                  type="button"
                  onClick={() => toggleWeekday(d.value)}
                  title={d.long}
                  className={
                    "rounded-md border px-3 py-1.5 text-[11px] font-semibold transition " +
                    (active
                      ? "border-aqua/50 bg-aqua/15 text-aqua"
                      : "border-white/15 bg-white/[0.04] text-white/70 hover:border-white/30 hover:text-white")
                  }
                >
                  {d.label}
                </button>
              );
            })}
          </div>
          {spec.weekdays.length === 0 ? (
            <p className="mt-1 text-[11px] text-coral">
              Pick at least one day.
            </p>
          ) : null}
        </Field>
      ) : null}

      {/* Monthly: day-of-month picker */}
      {spec.kind === "monthly" ? (
        <Field label="Day of the month">
          <NumberInput
            value={spec.dayOfMonth}
            min={1}
            max={31}
            width="w-20"
            onChange={setDayOfMonth}
          />
          <p className="mt-1 text-[11px] text-white/45">
            If the month doesn&apos;t have that many days (e.g. day 31
            in February), GitHub simply skips it.
          </p>
        </Field>
      ) : null}

      {/* Custom cron escape hatch */}
      {spec.kind === "custom" ? (
        <Field
          label="Cron expression"
          hint="Raw 5-field cron. Saved verbatim into .ship/config.yml."
        >
          <input
            type="text"
            value={spec.cron}
            onChange={(e) => onChange({ kind: "custom", cron: e.target.value })}
            placeholder="*/15 * * * 1-5"
            spellCheck={false}
            className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-sm text-white focus:border-aqua focus:outline-none"
          />
        </Field>
      ) : null}

      {/* Quick presets — only meaningful for non-custom */}
      {spec.kind !== "custom" ? (
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-white/55">
          <span>Quick:</span>
          {QUICK_PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              onClick={() => onChange(p.make())}
              className="rounded-full border border-white/10 bg-white/[0.03] px-2.5 py-0.5 font-semibold text-white/70 hover:border-white/25 hover:text-white"
            >
              {p.label}
            </button>
          ))}
        </div>
      ) : null}

      {/* Live preview */}
      <div className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="info">Preview</Badge>
          <p className="text-xs text-white">{human}</p>
        </div>
        <p className="mt-1 font-mono text-[11px] text-white/45">cron: {cron}</p>
      </div>
    </div>
  );
}

/**
 * Construct a ``ScheduleSpec`` from an existing cron string — the
 * round-trip the Library "Edit schedule" flow uses on mount.
 */
export function specFromCron(cron: string | null): ScheduleSpec {
  return cronToSpec(cron);
}

// ----------------------------------------------------------------------------
// Internals
// ----------------------------------------------------------------------------

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-[10px] font-semibold uppercase tracking-widest text-white/55">
        {label}
      </label>
      <div className="mt-1.5">{children}</div>
      {hint ? <p className="mt-1 text-[11px] text-white/45">{hint}</p> : null}
    </div>
  );
}

function NumberInput({
  value,
  min,
  max,
  step = 1,
  width = "w-16",
  onChange,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  width?: string;
  onChange: (n: number) => void;
}) {
  return (
    <input
      type="number"
      min={min}
      max={max}
      step={step}
      value={value}
      onChange={(e) => {
        const n = Number(e.target.value);
        if (!Number.isFinite(n)) return;
        onChange(Math.max(min, Math.min(max, n)));
      }}
      className={
        "rounded-md border border-white/15 bg-black/30 px-2 py-1 text-center font-mono text-sm text-white focus:border-aqua focus:outline-none " +
        width
      }
    />
  );
}
