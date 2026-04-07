import { describe, it, expect } from "vitest";
import { ROLES, ROLE_ORDER, getNextRole, getPrevRole, STAGE_LABELS, READY_LABELS, formatAgentComment, parseAgentComment, AgentArtifactSchema, } from "../agent-contracts.js";
describe("agent-contracts", () => {
    it("ROLE_ORDER has all roles", () => {
        expect(ROLE_ORDER).toHaveLength(ROLES.length);
        expect(ROLE_ORDER).toContain("ba");
        expect(ROLE_ORDER).toContain("release-manager");
    });
    it("getNextRole returns next role", () => {
        expect(getNextRole("ba")).toBe("architect");
        expect(getNextRole("architect")).toBe("qa-architect");
        expect(getNextRole("developer")).toBe("release-manager");
        expect(getNextRole("release-manager")).toBe("qa-automation");
        expect(getNextRole("qa-automation")).toBeNull();
    });
    it("getPrevRole returns previous role", () => {
        expect(getPrevRole("ba")).toBeNull();
        expect(getPrevRole("architect")).toBe("ba");
        expect(getPrevRole("release-manager")).toBe("developer");
        expect(getPrevRole("qa-automation")).toBe("release-manager");
    });
    it("STAGE_LABELS and READY_LABELS have correct format", () => {
        for (const role of ROLES) {
            expect(STAGE_LABELS[role]).toMatch(/^stage:/);
            expect(READY_LABELS[role]).toMatch(/^ready:/);
        }
    });
    it("formatAgentComment and parseAgentComment roundtrip", () => {
        const artifact = {
            agentRole: "developer",
            agentRunId: "run_001",
            issue: "ENG-123",
            status: "completed",
            summary: "Done",
            nextRole: "qa-automation",
        };
        const formatted = formatAgentComment(artifact);
        expect(formatted).toContain("```json");
        const parsed = parseAgentComment(formatted);
        expect(parsed).not.toBeNull();
        expect(parsed.agentRole).toBe("developer");
        expect(parsed.issue).toBe("ENG-123");
    });
    it("AgentArtifactSchema validates", () => {
        const valid = {
            agentRole: "developer",
            agentRunId: "run_001",
            issue: "ENG-123",
            status: "completed",
            summary: "Done",
        };
        expect(() => AgentArtifactSchema.parse(valid)).not.toThrow();
    });
});
//# sourceMappingURL=agent-contracts.test.js.map