import type { ApiProcessSchedule } from "@/lib/api/client";

export const DEFAULT_TIMES = ["09:00", "13:00", "17:00"];

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
