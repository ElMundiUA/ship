import { describe, expect, it } from "vitest";

import {
  formatInboxHeadline,
  formatIntakeReasonTooltip,
  INTAKE_REASON_LABELS,
} from "@/lib/inbox-copy";

describe("formatInboxHeadline", () => {
  it("rewrites agent blocked titles when ticket_ref is present", () => {
    expect(
      formatInboxHeadline({
        title: "agent blocked: validation",
        type: "blocker",
        payload: { ticket_ref: "ELS-99", fsm_stage: "validation" },
      }),
    ).toBe("ELS-99 validation bounced — restart or skip?");
  });

  it("leaves unknown titles unchanged", () => {
    expect(
      formatInboxHeadline({
        title: "custom title",
        type: "clarification",
      }),
    ).toBe("custom title");
  });

  it("does not invent ticket_ref when payload lacks it", () => {
    expect(
      formatInboxHeadline({
        title: "agent blocked: validation",
        type: "blocker",
        payload: {},
      }),
    ).toBe("agent blocked: validation");
  });

  it("uses list-row ticket_ref and fsm_stage without full payload", () => {
    expect(
      formatInboxHeadline({
        title: "agent blocked: validation",
        type: "blocker",
        ticket_ref: "ELS-99",
        fsm_stage: "validation",
      }),
    ).toBe("ELS-99 validation bounced — restart or skip?");
  });
});

describe("formatIntakeReasonTooltip", () => {
  it("maps known intake_reason prefixes", () => {
    expect(formatIntakeReasonTooltip("fallback:workspace_admin")).toBe(
      INTAKE_REASON_LABELS["fallback:"],
    );
  });

  it("returns raw string for unknown reasons", () => {
    expect(formatIntakeReasonTooltip("custom:reason")).toBe("custom:reason");
  });
});
