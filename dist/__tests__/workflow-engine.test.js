import { describe, it, expect } from "vitest";
import { computeHandoff, shouldSkipRole, getEffectiveFirstRole, canTakeIssue, getBlockedHandoff, getEscalatedHandoff, getQaFailedHandoff, } from "../workflow-engine.js";
import { loadConfig } from "../config.js";
const config = loadConfig();
describe("workflow-engine", () => {
    it("computeHandoff from developer to qa-automation", () => {
        const h = computeHandoff("developer", "qa-automation", config);
        expect(h.from).toBe("developer");
        expect(h.to).toBe("qa-automation");
        expect(h.labelsToRemove).toContain("ready:developer");
        expect(h.labelsToAdd).toContain("stage:qa-automation");
        expect(h.labelsToAdd).toContain("ready:qa-automation");
    });
    it("computeHandoff from release-manager has no next", () => {
        const h = computeHandoff("release-manager", null, config);
        expect(h.to).toBeNull();
        expect(h.labelsToRemove).toContain("ready:release-manager");
    });
    it("shouldSkipRole returns true for flow:no-ba", () => {
        expect(shouldSkipRole("ba", ["flow:no-ba"], config)).toBe(true);
        expect(shouldSkipRole("architect", ["flow:no-ba"], config)).toBe(false);
    });
    it("getEffectiveFirstRole with flow:no-ba", () => {
        expect(getEffectiveFirstRole(["flow:no-ba"], config)).toBe("architect");
        expect(getEffectiveFirstRole([], config)).toBe("ba");
    });
    it("getEffectiveFirstRole with flow:bug returns bug-agent", () => {
        expect(getEffectiveFirstRole(["flow:bug"], config)).toBe("bug-agent");
    });
    it("canTakeIssue requires ready label", () => {
        expect(canTakeIssue("developer", ["ready:developer"], config)).toBe(true);
        expect(canTakeIssue("developer", ["ready:architect"], config)).toBe(false);
        expect(canTakeIssue("ba", ["ready:ba", "flow:no-ba"], config)).toBe(false);
        expect(canTakeIssue("bug-agent", ["ready:bug-agent", "flow:bug"], config)).toBe(true);
        expect(canTakeIssue("bug-agent", ["ready:bug-agent"], config)).toBe(false);
    });
    it("getBlockedHandoff adds result:blocked", () => {
        const h = getBlockedHandoff("architect", "Missing API");
        expect(h.labelsToAdd).toContain("result:blocked");
        expect(h.newState).toBe("Blocked");
    });
    it("getEscalatedHandoff adds result:needs-human", () => {
        const h = getEscalatedHandoff("release-manager", "Preview failed");
        expect(h.labelsToAdd).toContain("result:needs-human");
    });
    it("getQaFailedHandoff returns to developer", () => {
        const h = getQaFailedHandoff();
        expect(h.to).toBe("developer");
        expect(h.labelsToAdd).toContain("result:failed");
    });
});
//# sourceMappingURL=workflow-engine.test.js.map