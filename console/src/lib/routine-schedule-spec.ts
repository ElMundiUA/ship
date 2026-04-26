import { DateTime } from "luxon";

export type RoutineScheduleMode =
  | "every_hours_utc"
  | "daily"
  | "weekdays"
  | "weekly"
  | "monthly"
  | "expert_utc";

/** v1 shape stored in process YAML under `schedule` (alongside `cadence`). */
export type RoutineScheduleV1 = {
  v: 1;
  time_zone: string;
  mode: RoutineScheduleMode;
  /** 1-23, for every_hours_utc (minute 0) */
  every_hours?: number;
  local_hour?: number;
  local_minute?: number;
  /** crontab DOW: 0=Sun .. 6=Sat */
  weekdays?: number[];
  day_of_month?: number;
  /** Raw 5-field UTC cron; used when mode === expert_utc */
  cron_utc?: string;
};

export const defaultScheduleSpec = (): RoutineScheduleV1 => ({
  v: 1,
  time_zone: safeDefaultTimeZone(),
  mode: "weekdays",
  local_hour: 9,
  local_minute: 0,
  weekdays: [1, 2, 3, 4, 5],
});

function safeDefaultTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/**
 * Produces 5-field cron in UTC from a schedule spec. Best-effort for DST
 * (uses a single reference day for time conversion).
 */
export function buildUtcCadenceFromSpec(spec: RoutineScheduleV1): string | null {
  const tz = spec.time_zone || "UTC";
  try {
    switch (spec.mode) {
      case "every_hours_utc": {
        const n = spec.every_hours ?? 1;
        if (n < 1 || n > 23) return "0 * * * *";
        if (n === 1) return "0 * * * *";
        return `0 */${n} * * *`;
      }
      case "expert_utc": {
        const c = (spec.cron_utc ?? "").trim();
        if (!c) return null;
        const parts = c.split(/\s+/);
        return parts.length === 5 ? c : null;
      }
      case "daily": {
        return buildDailyOrWeekdays(
          spec,
          tz,
          (m, h) => `${m} ${h} * * *`,
        );
      }
      case "weekdays": {
        return buildDailyOrWeekdays(
          spec,
          tz,
          (m, h) => `${m} ${h} * * 1-5`,
        );
      }
      case "weekly": {
        const days = (spec.weekdays?.length
          ? spec.weekdays
          : [1, 2, 3, 4, 5]
        )
          .filter((d) => d >= 0 && d <= 6)
          .sort((a, b) => a - b);
        if (days.length === 0) return null;
        const dows = days.join(",");
        return buildDailyOrWeekdays(
          { ...spec, time_zone: tz },
          tz,
          (m, h) => `${m} ${h} * * ${dows}`,
        );
      }
      case "monthly": {
        const dom = Math.min(31, Math.max(1, spec.day_of_month ?? 1));
        return buildMonthlyCadence(
          spec,
          tz,
          dom,
        );
      }
      default:
        return null;
    }
  } catch {
    return null;
  }
}

function buildDailyOrWeekdays(
  spec: RoutineScheduleV1,
  tz: string,
  fmt: (minute: string, hour: string) => string,
): string | null {
  const wh = toUtcParts(
    spec,
    tz,
    DateTime.now().setZone(tz).plus({ days: 1 }),
  );
  if (!wh) return null;
  return fmt(wh.m, wh.h);
}

function toUtcParts(
  spec: RoutineScheduleV1,
  tz: string,
  ref: DateTime,
): { m: string; h: string } | null {
  const h = spec.local_hour ?? 9;
  const m = spec.local_minute ?? 0;
  if (h < 0 || h > 23 || m < 0 || m > 59) return null;
  const wall = ref.set({ hour: h, minute: m, second: 0, millisecond: 0 });
  if (!wall.isValid) return null;
  const utc = wall.toUTC();
  return { m: String(utc.minute), h: String(utc.hour) };
}

function buildMonthlyCadence(
  spec: RoutineScheduleV1,
  tz: string,
  dom: number,
): string | null {
  const h = spec.local_hour ?? 9;
  const m = spec.local_minute ?? 0;
  if (h < 0 || h > 23 || m < 0 || m > 59) return null;
  const zone = DateTime.now().setZone(tz);
  for (let i = 0; i < 48; i += 1) {
    const c = zone.plus({ months: i }).set({
      day: dom,
      hour: h,
      minute: m,
      second: 0,
      millisecond: 0,
    });
    if (c.isValid && c > DateTime.now() && c.day === dom) {
      const parts = toUtcParts(spec, tz, c);
      if (parts) return `${parts.m} ${parts.h} ${dom} * *`;
    }
  }
  return null;
}

/** Load UI spec from stored YAML, or from cadence (expert) only. */
export function parseScheduleFromYaml(
  row: Record<string, unknown>,
  cadence: string | null,
): RoutineScheduleV1 {
  const raw = row["schedule"];
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const s = raw as Record<string, unknown>;
    if (
      s.v === 1 &&
      typeof s.mode === "string" &&
      typeof s.time_zone === "string"
    ) {
      return {
        v: 1,
        time_zone: s.time_zone,
        mode: s.mode as RoutineScheduleMode,
        every_hours:
          typeof s.every_hours === "number" ? s.every_hours : undefined,
        local_hour: typeof s.local_hour === "number" ? s.local_hour : undefined,
        local_minute:
          typeof s.local_minute === "number" ? s.local_minute : undefined,
        weekdays: Array.isArray(s.weekdays)
          ? s.weekdays.filter((d): d is number => typeof d === "number")
          : undefined,
        day_of_month:
          typeof s.day_of_month === "number" ? s.day_of_month : undefined,
        cron_utc: typeof s.cron_utc === "string" ? s.cron_utc : undefined,
      };
    }
  }
  if (cadence?.trim()) {
    return {
      v: 1,
      time_zone: "UTC",
      mode: "expert_utc",
      cron_utc: cadence.trim(),
    };
  }
  return defaultScheduleSpec();
}
