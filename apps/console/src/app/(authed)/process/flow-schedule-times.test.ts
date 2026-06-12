import { describe, expect, it } from "vitest";

import type { ApiProcessSchedule, ApiProcessScheduleSlot } from "@/lib/api/client";

import {
  DEFAULT_TIMES,
  TIME_WINDOW_HOUR_END,
  TIME_WINDOW_HOUR_START,
  formatHourTime,
  knownTimes,
  nextAvailableTime,
} from "./flow-schedule-times";

function scheduleWithSlots(slots: ApiProcessScheduleSlot[]): ApiProcessSchedule {
  return { time_zone: "UTC", slots };
}

function slot(localTime: string, weekdays: number[] = [1]): ApiProcessScheduleSlot {
  return {
    id: `slot-${localTime}`,
    local_time: localTime,
    weekdays,
    specialist_ids: ["specialist-1"],
  };
}

describe("knownTimes", () => {
  it("TC-1: returns default rows when schedule is empty", () => {
    expect(knownTimes(scheduleWithSlots([]))).toEqual(DEFAULT_TIMES);
  });

  it("TC-2: keeps all default rows after one assignment", () => {
    expect(knownTimes(scheduleWithSlots([slot("09:00")]))).toEqual(DEFAULT_TIMES);
  });

  it("TC-3: keeps unassigned default rows when other defaults are assigned", () => {
    expect(
      knownTimes(scheduleWithSlots([slot("09:00"), slot("17:00")])),
    ).toEqual(DEFAULT_TIMES);
  });

  it("TC-4: includes routine-seeded extra times alongside defaults", () => {
    expect(knownTimes(scheduleWithSlots([slot("09:00")]), ["06:00"])).toEqual([
      "06:00",
      "09:00",
      "13:00",
      "17:00",
    ]);
  });

  it("TC-5: includes manually added extra times alongside defaults", () => {
    expect(knownTimes(scheduleWithSlots([slot("09:00")]), ["10:30"])).toEqual([
      "09:00",
      "10:30",
      "13:00",
      "17:00",
    ]);
  });

  it("TC-6: returns defaults when the last specialist slot is removed", () => {
    expect(knownTimes(scheduleWithSlots([]))).toEqual(DEFAULT_TIMES);
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
