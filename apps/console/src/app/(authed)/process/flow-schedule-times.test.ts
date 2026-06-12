import { describe, expect, it } from "vitest";

import type { ApiProcessSchedule } from "@/lib/api/client";

import {
  DEFAULT_TIMES,
  TIME_WINDOW_HOUR_END,
  TIME_WINDOW_HOUR_START,
  formatHourTime,
  knownTimes,
  nextAvailableTime,
} from "./flow-schedule-times";

const emptySchedule: ApiProcessSchedule = {
  trigger: { kind: "schedule", event: null },
  time_zone: "UTC",
  slots: [],
};

describe("knownTimes", () => {
  it("seeds default rows when the schedule has no slot times", () => {
    expect(knownTimes(emptySchedule)).toEqual([...DEFAULT_TIMES].sort());
  });

  it("merges slot times, extras, and routine seeds without duplicates", () => {
    const schedule: ApiProcessSchedule = {
      ...emptySchedule,
      slots: [
        {
          id: "slot_0900_1",
          label: "Mon 09:00",
          local_time: "09:00",
          weekdays: [1],
          specialist_ids: ["dev"],
        },
      ],
    };
    expect(knownTimes(schedule, ["10:00", "06:00"])).toEqual([
      "06:00",
      "09:00",
      "10:00",
    ]);
  });
});

describe("nextAvailableTime", () => {
  it("returns the first unused whole-hour slot when defaults already fill the grid", () => {
    expect(nextAvailableTime([...DEFAULT_TIMES])).toBe("08:00");
  });

  it("skips occupied hours inside the allowed range", () => {
    expect(nextAvailableTime(["08:00", "09:00", "13:00", "17:00"])).toBe("10:00");
  });

  it("returns null when every hour in the range is already shown", () => {
    const allHours = Array.from(
      { length: TIME_WINDOW_HOUR_END - TIME_WINDOW_HOUR_START + 1 },
      (_, index) => formatHourTime(TIME_WINDOW_HOUR_START + index),
    );
    expect(nextAvailableTime(allHours)).toBeNull();
  });

  it("still finds a slot when routine projection seeds an early hour", () => {
    expect(nextAvailableTime([...DEFAULT_TIMES, "06:00"])).toBe("08:00");
  });
});
