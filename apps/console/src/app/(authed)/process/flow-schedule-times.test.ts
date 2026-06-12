import { describe, expect, it } from "vitest";

import type { ApiProcessSchedule, ApiProcessScheduleSlot } from "@/lib/api/client";

import { DEFAULT_TIMES, knownTimes } from "./flow-schedule-times";

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
