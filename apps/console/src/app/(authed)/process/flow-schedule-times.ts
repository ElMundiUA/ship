import type { ApiProcessSchedule } from "@/lib/api/client";

export const DEFAULT_TIMES = ["09:00", "13:00", "17:00"];

/** Inclusive whole-hour bounds for operator-added capacity rows. */
export const TIME_WINDOW_HOUR_START = 8;
export const TIME_WINDOW_HOUR_END = 20;

export function formatHourTime(hour: number): string {
  return `${String(hour).padStart(2, "0")}:00`;
}

export function knownTimes(
  schedule: ApiProcessSchedule,
  extras: string[] = [],
): string[] {
  const times = new Set(
    [
      ...DEFAULT_TIMES,
      ...schedule.slots.map((slot) => slot.local_time),
      ...extras,
    ].filter((value): value is string => Boolean(value)),
  );
  return Array.from(times).sort();
}

export function nextAvailableTime(existing: string[]): string | null {
  const used = new Set(existing);
  for (let hour = TIME_WINDOW_HOUR_START; hour <= TIME_WINDOW_HOUR_END; hour++) {
    const time = formatHourTime(hour);
    if (!used.has(time)) return time;
  }
  return null;
}

export const TIME_WINDOW_EXHAUSTED_TOOLTIP =
  "All hourly slots between 08:00 and 20:00 are already shown";
